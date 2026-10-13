"""The `sync` command: reconcile the Directory and the Ledger.

One pass reads every page of accounts, items and existing summaries, works out
the summary each active account should have, posts only the ones the Ledger is
missing, and writes three files under the output directory:

* `summary.jsonl` — each summary posted (exactly the POST body);
* `orphans.jsonl` — well-formed items whose owner is not an active account;
* `errors.jsonl`  — malformed records, tagged with the service they came from.

Rate limits (429 + Retry-After) are honoured per service: after a 429 nothing
more is sent to that service until the wait has elapsed, then the same request
is retried.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .config import Config
from .http_client import HttpClient, HttpClientError, Response
from .models import Account, Item, Summary

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

SUMMARY_FILE = "summary.jsonl"
ORPHANS_FILE = "orphans.jsonl"
ERRORS_FILE = "errors.jsonl"

ITEM_STATUSES = ("open", "done")

#: Wait used when a 429 carries no usable Retry-After.
DEFAULT_RETRY_AFTER_SECONDS = 1.0
#: Added to every Retry-After wait so clock granularity never makes us early.
RETRY_MARGIN_SECONDS = 0.05
#: Transient failures (5xx, transport errors) tolerated per request.
MAX_TRANSIENT_RETRIES = 5
#: Status codes that mean the ledger accepted a summary (created / replay).
POST_OK = (200, 201)


class SyncError(Exception):
    """A service answered in a way sync cannot work with."""


# --------------------------------------------------------------------------
# Validation — the only place that decides whether a record is usable.

def _is_int(value: Any) -> bool:
    # bool is a subclass of int in Python, but JSON true is not an integer.
    return isinstance(value, int) and not isinstance(value, bool)


def parse_item(record: Any) -> Optional[Item]:
    """Return an `Item` if `record` is a well-formed item, else None."""
    if not isinstance(record, dict):
        return None
    ident, owner = record.get("id"), record.get("owner")
    status, points = record.get("status"), record.get("points")
    if not isinstance(ident, str) or not isinstance(owner, str):
        return None
    if not isinstance(status, str) or status not in ITEM_STATUSES:
        return None
    if not _is_int(points) or points < 0:
        return None
    return Item(ident, owner, status, points)


def parse_account(record: Any) -> Optional[Account]:
    """Return an `Account` if `record` is a well-formed account, else None."""
    if not isinstance(record, dict):
        return None
    ident, owner, active = record.get("id"), record.get("owner"), record.get("active")
    if not isinstance(ident, str) or not isinstance(owner, str):
        return None
    if not isinstance(active, bool):
        return None
    return Account(ident, owner, active)


def summary_owner(record: Any) -> Optional[str]:
    """Owner of an existing ledger summary, or None if it names none."""
    if isinstance(record, dict) and isinstance(record.get("owner"), str):
        return record["owner"]
    return None


# --------------------------------------------------------------------------
# Transport with rate limiting.

def _retry_after_seconds(response: Response) -> float:
    raw = response.headers.get("Retry-After") if response.headers is not None else None
    try:
        seconds = float(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS
    if seconds != seconds or seconds < 0:  # NaN or negative
        return DEFAULT_RETRY_AFTER_SECONDS
    return seconds


class ServiceClient:
    """Wraps one `HttpClient`, retrying 429s after the advertised wait.

    `not_before` is the monotonic time before which this service must not be
    contacted again; it is per service, so a 429 from one service does not
    hold up the other.
    """

    def __init__(self, name: str, http: HttpClient,
                 sleep=time.sleep, clock=time.monotonic) -> None:
        self.name = name
        self.http = http
        self._sleep = sleep
        self._clock = clock
        self.not_before = 0.0

    def _wait_turn(self) -> None:
        while True:
            remaining = self.not_before - self._clock()
            if remaining <= 0:
                return
            self._sleep(remaining)

    def request(self, method: str, path: str, params=None, payload=None,
                headers=None) -> Response:
        transient = 0
        while True:
            self._wait_turn()
            try:
                response = self.http.request(method, path, params=params,
                                             payload=payload, headers=headers)
            except HttpClientError:
                transient += 1
                if transient > MAX_TRANSIENT_RETRIES:
                    raise
                self.not_before = self._clock() + min(2.0 ** transient * 0.1, 5.0)
                continue
            if response.status == 429:
                wait = _retry_after_seconds(response) + RETRY_MARGIN_SECONDS
                self.not_before = self._clock() + wait
                continue
            if response.status >= 500:
                transient += 1
                if transient > MAX_TRANSIENT_RETRIES:
                    return response
                self.not_before = self._clock() + min(2.0 ** transient * 0.1, 5.0)
                continue
            return response

    def fetch_all(self, path: str, key: str) -> List[Any]:
        """Every record under `key` across all pages of `path`."""
        records: List[Any] = []
        page: Any = 1
        seen: Set[Any] = set()
        while page is not None:
            if page in seen:
                raise SyncError("%s %s: pagination loops back to page %r"
                                % (self.name, path, page))
            seen.add(page)
            response = self.request("GET", path, params={"page": page})
            if response.status != 200:
                raise SyncError("%s GET %s page %r: HTTP %d %s"
                                % (self.name, path, page, response.status,
                                   response.text[:200]))
            try:
                body = response.json()
            except ValueError as exc:
                raise SyncError("%s GET %s page %r: invalid JSON: %s"
                                % (self.name, path, page, exc)) from exc
            if not isinstance(body, dict) or not isinstance(body.get(key), list):
                raise SyncError("%s GET %s page %r: no %r list in response"
                                % (self.name, path, page, key))
            records.extend(body[key])
            page = body.get("next_page")
            if page is not None and not _is_int(page):
                raise SyncError("%s GET %s: bad next_page %r" % (self.name, path, page))
        return records


# --------------------------------------------------------------------------
# Reconciliation (pure: no I/O).

def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class Reconciliation:
    """What one pass over the fetched records decides."""

    def __init__(self) -> None:
        self.summaries: Dict[str, Summary] = {}
        self.orphans: List[Any] = []
        self.errors: List[Dict[str, Any]] = []


def reconcile(account_records: Iterable[Any], item_records: Iterable[Any]) -> Reconciliation:
    """Compute required summaries, orphans and malformed-record errors."""
    result = Reconciliation()
    error_keys: Set[str] = set()

    def error(service: str, record: Any) -> None:
        entry = {"service": service, "record": record}
        key = _canonical(entry)
        if key not in error_keys:
            error_keys.add(key)
            result.errors.append(entry)

    active_owners: Set[str] = set()
    for record in account_records:
        account = parse_account(record)
        if account is None:
            error("ledger", record)
        elif account.active:
            active_owners.add(account.owner)

    counts = {owner: [0, 0, 0] for owner in active_owners}
    seen_items: Set[str] = set()
    for record in item_records:
        item = parse_item(record)
        if item is None:
            error("directory", record)
            continue
        if item.id in seen_items:
            continue
        seen_items.add(item.id)
        if item.owner not in active_owners:
            result.orphans.append(record)
            continue
        tally = counts[item.owner]
        tally[0 if item.status == "open" else 1] += 1
        tally[2] += item.points

    result.summaries = {owner: Summary(owner, *tally) for owner, tally in counts.items()}
    return result


# --------------------------------------------------------------------------
# Output.

def _read_jsonl(path: str) -> List[Any]:
    entries: List[Any] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return entries


def _write_jsonl(path: str, entries: Iterable[Any]) -> None:
    lines = sorted({_canonical(entry): json.dumps(entry) for entry in entries}.items())
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        for _, line in lines:
            handle.write(line + "\n")
    os.replace(tmp, path)


def _summary_fields(record: Any) -> Optional[Tuple[Any, ...]]:
    if not isinstance(record, dict):
        return None
    return tuple(record.get(k) for k in ("owner", "open_items", "done_items", "total_points"))


# --------------------------------------------------------------------------
# The command.

def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR,
             directory: Optional[ServiceClient] = None,
             ledger: Optional[ServiceClient] = None) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    directory = directory or ServiceClient("directory", HttpClient(config.directory_url))
    ledger = ledger or ServiceClient("ledger", HttpClient(config.ledger_url))

    try:
        accounts = ledger.fetch_all("/v1/accounts", "accounts")
        existing = ledger.fetch_all("/v1/summaries", "summaries")
        items = directory.fetch_all("/v1/items", "items")
    except (SyncError, HttpClientError) as exc:
        print("orgsync: %s" % exc, file=sys.stderr)
        return 1

    result = reconcile(accounts, items)

    existing_by_owner: Dict[str, Any] = {}
    for record in existing:
        owner = summary_owner(record)
        if owner is not None:
            existing_by_owner.setdefault(owner, record)

    os.makedirs(out_dir, exist_ok=True)
    summary_path = os.path.join(out_dir, SUMMARY_FILE)

    # Summaries posted by an earlier run stay recorded, as long as the ledger
    # still holds exactly that summary for a still-required owner; this keeps
    # a rerun's summary.jsonl identical without re-posting anything.
    posted: List[Dict[str, Any]] = []
    for entry in _read_jsonl(summary_path):
        owner = entry.get("owner") if isinstance(entry, dict) else None
        if (isinstance(owner, str) and owner in result.summaries
                and owner in existing_by_owner
                and _summary_fields(existing_by_owner[owner]) == _summary_fields(entry)):
            posted.append(dict(zip(("owner", "open_items", "done_items", "total_points"),
                                   _summary_fields(entry))))

    failed = False
    for owner in sorted(result.summaries):
        if owner in existing_by_owner:
            continue
        body = result.summaries[owner].to_dict()
        try:
            response = ledger.request("POST", "/v1/summaries", payload=body,
                                      headers={"Idempotency-Key": "sync-%s" % owner})
        except HttpClientError as exc:
            print("orgsync: posting summary for %r failed: %s" % (owner, exc), file=sys.stderr)
            failed = True
            continue
        if response.status in POST_OK:
            posted.append(body)
        else:
            print("orgsync: ledger refused summary for %r: HTTP %d %s"
                  % (owner, response.status, response.text[:200]), file=sys.stderr)
            failed = True

    _write_jsonl(summary_path, posted)
    _write_jsonl(os.path.join(out_dir, ORPHANS_FILE), result.orphans)
    _write_jsonl(os.path.join(out_dir, ERRORS_FILE), result.errors)
    return 1 if failed else 0
