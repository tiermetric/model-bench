"""The `sync` command: reconcile the Directory and the Ledger.

One pass reads every page of accounts, items and existing summaries, decides
which records are usable, computes one summary per active account, posts the
summaries the Ledger does not have yet, and writes three files under `out/`:

* `summary.jsonl` — the summaries orgsync posted (exactly the POST bodies);
* `orphans.jsonl` — well-formed items whose owner has no active account;
* `errors.jsonl`  — malformed records, tagged with the service they came from.

Both services may answer anything with `429 Retry-After`; `RateLimitedClient`
holds each service off for the requested time and retries the same request.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

from .config import Config
from .http_client import HttpClient, HttpClientError, Response
from .models import Account, Item, Summary

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

SUMMARY_FILE = "summary.jsonl"
ORPHANS_FILE = "orphans.jsonl"
ERRORS_FILE = "errors.jsonl"

ITEM_STATUSES = ("open", "done")

#: Retries for failures that are not rate limits: 5xx, transport errors and
#: unparsable 2xx bodies. A 429 is retried without limit, as rule 6 requires.
MAX_TRANSIENT_RETRIES = 5
#: Retry-After to assume when a 429 carries no usable header.
DEFAULT_RETRY_AFTER = 1.0
#: Added to every Retry-After wait so clock granularity never makes us early.
RETRY_MARGIN_SECONDS = 0.05

#: HTTP statuses on which the Ledger has rejected a write.
REJECTED_WRITE_STATUSES = (400, 409, 422)


class SyncError(Exception):
    """The pass cannot complete: a service kept failing or rejected a write."""


# --------------------------------------------------------------------------
# HTTP with rate limiting


def parse_retry_after(value: Optional[str]) -> float:
    """Seconds to wait from a Retry-After header (delta-seconds form)."""
    if value is None:
        return DEFAULT_RETRY_AFTER
    try:
        seconds = float(value.strip())
    except ValueError:
        return DEFAULT_RETRY_AFTER
    if seconds != seconds or seconds < 0:  # NaN or negative
        return DEFAULT_RETRY_AFTER
    return seconds


class RateLimitedClient:
    """Wraps one service's `HttpClient`, honouring its 429 responses.

    After a 429 no request at all goes to the service until Retry-After has
    elapsed; then the same request is reissued. `clock` and `sleep` are
    injectable for tests.
    """

    def __init__(self, name: str, http: HttpClient,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.name = name
        self.http = http
        self._clock = clock
        self._sleep = sleep
        self._not_before = 0.0

    def _wait_turn(self) -> None:
        while True:
            remaining = self._not_before - self._clock()
            if remaining <= 0:
                return
            self._sleep(remaining)

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
                payload: Any = None, headers: Optional[Dict[str, str]] = None,
                accept: Callable[[Response], bool] = lambda r: True) -> Response:
        """Issue a request until it gets a non-429, non-5xx answer that
        `accept` approves. Raises SyncError after too many transient failures."""
        failures = 0
        last = ""
        while True:
            self._wait_turn()
            try:
                response = self.http.request(method, path, params=params,
                                             payload=payload, headers=headers)
            except HttpClientError as exc:
                last = str(exc)
            else:
                if response.status == 429:
                    delay = parse_retry_after(response.headers.get("Retry-After"))
                    self._not_before = self._clock() + delay + RETRY_MARGIN_SECONDS
                    continue
                if response.status < 500 and accept(response):
                    return response
                last = "%s %s answered %d" % (method, response.url, response.status)
            failures += 1
            if failures > MAX_TRANSIENT_RETRIES:
                raise SyncError("%s: giving up: %s" % (self.name, last))
            # Back off briefly; this delay is ours, not the service's.
            self._not_before = max(self._not_before,
                                   self._clock() + min(0.2 * 2 ** failures, 5.0))


def _page_body(key: str) -> Callable[[Response], bool]:
    def accept(response: Response) -> bool:
        if response.status != 200:
            return False
        try:
            body = response.json()
        except ValueError:
            return False
        return isinstance(body, dict) and isinstance(body.get(key), list)
    return accept


def fetch_all(client: RateLimitedClient, path: str, key: str) -> List[Any]:
    """Every record under `key` across all pages, following `next_page`."""
    records: List[Any] = []
    page: Any = 1
    seen = set()
    while page is not None:
        if not isinstance(page, int) or isinstance(page, bool) or page in seen:
            raise SyncError("%s %s: bad next_page %r" % (client.name, path, page))
        seen.add(page)
        body = client.request("GET", path, params={"page": page},
                              accept=_page_body(key)).json()
        records.extend(body[key])
        page = body.get("next_page")
    return records


# --------------------------------------------------------------------------
# Validation


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_int(value: Any) -> bool:
    # bool is a subclass of int in Python but not a JSON integer.
    return isinstance(value, int) and not isinstance(value, bool)


def parse_item(record: Any) -> Optional[Item]:
    """The record as an Item if it is well-formed, else None."""
    if not isinstance(record, dict):
        return None
    id_, owner, status, points = (record.get(k) for k in ("id", "owner", "status", "points"))
    if not (_is_str(id_) and _is_str(owner) and _is_str(status)
            and status in ITEM_STATUSES and _is_int(points) and points >= 0):
        return None
    return Item(id_, owner, status, points)


def parse_account(record: Any) -> Optional[Account]:
    """The record as an Account if it is well-formed, else None."""
    if not isinstance(record, dict):
        return None
    id_, owner, active = record.get("id"), record.get("owner"), record.get("active")
    if not (_is_str(id_) and _is_str(owner) and isinstance(active, bool)):
        return None
    return Account(id_, owner, active)


def _canonical(record: Any) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


# --------------------------------------------------------------------------
# Reconciliation (pure: no I/O)


class Plan:
    """What one pass decided, before anything is posted."""

    def __init__(self) -> None:
        self.active_owners: List[str] = []
        self.summaries: Dict[str, Summary] = {}
        self.orphans: List[Any] = []
        self.errors: List[Dict[str, Any]] = []


def reconcile(accounts: Iterable[Any], items: Iterable[Any]) -> Plan:
    """Compute summaries, orphans and errors from raw account/item records."""
    plan = Plan()
    error_keys = set()

    def error(service: str, record: Any) -> None:
        key = (service, _canonical(record))
        if key not in error_keys:
            error_keys.add(key)
            plan.errors.append({"service": service, "record": record})

    active = set()
    for record in accounts:
        account = parse_account(record)
        if account is None:
            error("ledger", record)
        elif account.active:
            active.add(account.owner)
    plan.active_owners = sorted(active)

    counts = {owner: [0, 0, 0] for owner in active}  # open, done, points
    seen_items = set()
    for record in items:
        item = parse_item(record)
        if item is None:
            error("directory", record)
            continue
        if item.id in seen_items:
            continue
        seen_items.add(item.id)
        if item.owner not in active:
            plan.orphans.append(record)
            continue
        tally = counts[item.owner]
        tally[0 if item.status == "open" else 1] += 1
        tally[2] += item.points

    plan.summaries = {owner: Summary(owner, *counts[owner]) for owner in plan.active_owners}
    return plan


def existing_summary_owners(records: Iterable[Any]) -> Dict[str, Any]:
    """owner -> summary record, for every summary already in the Ledger."""
    found: Dict[str, Any] = {}
    for record in records:
        if isinstance(record, dict) and _is_str(record.get("owner")):
            found.setdefault(record["owner"], record)
    return found


def _same_summary(record: Any, body: Dict[str, Any]) -> bool:
    return isinstance(record, dict) and all(record.get(k) == v for k, v in body.items())


# --------------------------------------------------------------------------
# Output


def _read_jsonl(path: str) -> List[Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _write_jsonl(path: str, records: Iterable[Any]) -> None:
    """Write one JSON line per record, sorted so reruns give identical bytes."""
    lines = sorted(json.dumps(r) for r in records)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write("".join(line + "\n" for line in lines))
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# The command


def post_summary(ledger: RateLimitedClient, body: Dict[str, Any]) -> None:
    """POST one summary. 201 and 200 (replay) are success; anything else fails."""
    response = ledger.request("POST", "/v1/summaries", payload=body,
                              headers={"Idempotency-Key": "sync-%s" % body["owner"]})
    if response.status not in (200, 201):
        raise SyncError("ledger rejected summary for %r: %d %s"
                        % (body["owner"], response.status, response.text[:200]))


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR,
             directory: Optional[RateLimitedClient] = None,
             ledger: Optional[RateLimitedClient] = None) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    directory = directory or RateLimitedClient("directory", HttpClient(config.directory_url))
    ledger = ledger or RateLimitedClient("ledger", HttpClient(config.ledger_url))
    os.makedirs(out_dir, exist_ok=True)
    summary_path = os.path.join(out_dir, SUMMARY_FILE)

    try:
        accounts = fetch_all(ledger, "/v1/accounts", "accounts")
        existing = existing_summary_owners(fetch_all(ledger, "/v1/summaries", "summaries"))
        items = fetch_all(directory, "/v1/items", "items")
    except SyncError as exc:
        print("orgsync: %s" % exc, file=sys.stderr)
        return 1

    plan = reconcile(accounts, items)
    _write_jsonl(os.path.join(out_dir, ORPHANS_FILE), plan.orphans)
    _write_jsonl(os.path.join(out_dir, ERRORS_FILE), plan.errors)

    # summary.jsonl lists what orgsync posted. A rerun posts nothing, so keep
    # earlier entries that the Ledger confirms it holds exactly as posted.
    posted: Dict[str, Dict[str, Any]] = {}
    for record in _read_jsonl(summary_path):
        if (isinstance(record, dict) and record.get("owner") in plan.summaries
                and _same_summary(existing.get(record["owner"]), record)):
            posted[record["owner"]] = record

    code = 0
    try:
        for owner in plan.active_owners:
            if owner in existing:
                continue
            body = plan.summaries[owner].to_dict()
            post_summary(ledger, body)
            posted[owner] = body
    except SyncError as exc:
        print("orgsync: %s" % exc, file=sys.stderr)
        code = 1
    finally:
        _write_jsonl(summary_path, posted.values())
    return code
