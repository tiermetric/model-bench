"""The `sync` command: reconcile the Directory and the Ledger.

One pass, in order: read every page of accounts, items, and existing
summaries; let the Ledger decide who counts (active, well-formed accounts
only); post exactly the summaries the Ledger is missing; and write the
run's findings under `out/` — malformed records to `errors.jsonl`, ownerless
items to `orphans.jsonl`, and each summary posted to `summary.jsonl`.

Two services behaviors shape the code here. A `429` with `Retry-After` is a
pause, not a failure: each service has its own `RateLimiter`, and after a 429
no request goes to that service until the header's seconds have elapsed. And
a passing run makes no rejected write: summaries are posted only for owners
of active, well-formed accounts that have no summary yet, so the Ledger never
answers 400, 409 or 422. Running against unchanged services therefore issues
no write at all and leaves the three output files byte-for-byte as they were.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import Config
from .http_client import HttpClient, HttpClientError, Response
from .models import Summary

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

#: Output files, newline-delimited JSON, written under `out_dir`.
ERRORS_FILENAME = "errors.jsonl"
ORPHANS_FILENAME = "orphans.jsonl"
SUMMARY_FILENAME = "summary.jsonl"

#: Service endpoints, appended to each service's base URL.
ACCOUNTS_PATH = "/v1/accounts"
ITEMS_PATH = "/v1/items"
SUMMARIES_PATH = "/v1/summaries"

#: The only statuses a well-formed work item may carry.
ITEM_STATUSES = ("open", "done")

#: Idempotency-Key prefix for posted summaries.
IDEMPOTENCY_PREFIX = "sync-"

#: Safety valves for talking to a misbehaving service. The normal path never
#: touches them: they only stop a pathological service from hanging the run.
MAX_PAGES = 10000
MAX_RATE_RETRIES = 32
MAX_TRANSPORT_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.5
DEFAULT_RETRY_AFTER_SECONDS = 1.0


class SyncError(Exception):
    """The run could not be completed against the services as they behave."""


# --------------------------------------------------------------------------
# Record well-formedness. A record is judged per delivery: the same item id
# delivered once well-formed and once malformed is one usable item and one
# error line. Extra fields never matter; JSON types do.
# --------------------------------------------------------------------------

def _is_json_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_json_int(value: Any) -> bool:
    # bool is a subclass of int in Python, but JSON true/false is not an int.
    return isinstance(value, int) and not isinstance(value, bool)


def _is_json_bool(value: Any) -> bool:
    return isinstance(value, bool)


def item_is_well_formed(record: Any) -> bool:
    """True for `{id: str, owner: str, status: "open"|"done", points: int>=0}`."""
    if not isinstance(record, dict):
        return False
    if not _is_json_str(record.get("id")):
        return False
    if not _is_json_str(record.get("owner")):
        return False
    if record.get("status") not in ITEM_STATUSES:
        return False
    points = record.get("points")
    return _is_json_int(points) and points >= 0


def account_is_well_formed(record: Any) -> bool:
    """True for `{id: str, owner: str, active: bool}` (extra fields ignored)."""
    if not isinstance(record, dict):
        return False
    return (_is_json_str(record.get("id"))
            and _is_json_str(record.get("owner"))
            and _is_json_bool(record.get("active")))


# --------------------------------------------------------------------------
# Rate limiting: after a 429 from a service, that service sees no request
# until at least Retry-After seconds have passed. A 429 is never data and
# never abandons the work.
# --------------------------------------------------------------------------

class RateLimiter:
    """The earliest moment one service will accept another request."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._clock = clock
        self._sleep = sleep
        self._not_before = 0.0

    def defer(self, retry_after_seconds: float) -> None:
        """Note a 429: hold this service's requests for `retry_after_seconds`."""
        self._not_before = max(self._not_before,
                               self._clock() + max(retry_after_seconds, 0.0))

    def wait(self) -> None:
        """Block until the service may be contacted again."""
        delay = self._not_before - self._clock()
        if delay > 0:
            self._sleep(delay)


def _retry_after_seconds(response: Response) -> float:
    """The Retry-After delay a 429 asked for, defaulting sensibly."""
    headers = response.headers
    raw = headers.get("Retry-After") if headers is not None else None
    if raw is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    try:
        return max(float(str(raw).strip()), 0.0)
    except ValueError:
        return DEFAULT_RETRY_AFTER_SECONDS


# --------------------------------------------------------------------------
# One request, retried. 429 defers via the limiter and is retried forever-ish
# (bounded only as a runaway guard); transport failures and 5xx get a few
# plain attempts. Any other status comes back for the caller to interpret.
# --------------------------------------------------------------------------

def _send(client: HttpClient, limiter: RateLimiter, method: str, path: str,
          params: Optional[Dict[str, Any]] = None,
          payload: Optional[Any] = None,
          headers: Optional[Dict[str, str]] = None) -> Response:
    transport_failures = 0
    rate_deferrals = 0
    while True:
        limiter.wait()
        try:
            response = client.request(method, path, params=params,
                                      payload=payload, headers=headers)
        except HttpClientError as exc:
            transport_failures += 1
            if transport_failures >= MAX_TRANSPORT_ATTEMPTS:
                raise SyncError("%s %s failed after %d attempts: %s"
                                % (method, path, transport_failures, exc)) from exc
            time.sleep(RETRY_BACKOFF_SECONDS)
            continue
        if response.status == 429:
            rate_deferrals += 1
            if rate_deferrals > MAX_RATE_RETRIES:
                raise SyncError("%s %s: still rate-limited after %d retries"
                                % (method, path, rate_deferrals))
            limiter.defer(_retry_after_seconds(response))
            continue
        if 500 <= response.status < 600:
            transport_failures += 1
            if transport_failures >= MAX_TRANSPORT_ATTEMPTS:
                return response
            time.sleep(RETRY_BACKOFF_SECONDS)
            continue
        return response


def _fetch_all(client: HttpClient, limiter: RateLimiter, path: str,
               list_key: str) -> List[Any]:
    """Follow `next_page` from page 1 and return every record, in order."""
    records: List[Any] = []
    page = 1
    for _ in range(MAX_PAGES):
        response = _send(client, limiter, "GET", path, params={"page": page})
        if response.status != 200:
            raise SyncError("GET %s?page=%d returned HTTP %d"
                            % (path, page, response.status))
        try:
            payload = response.json()
        except ValueError as exc:
            raise SyncError("GET %s?page=%d returned a non-JSON body"
                            % (path, page)) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get(list_key), list):
            raise SyncError("GET %s?page=%d returned an unexpected body shape"
                            % (path, page))
        records.extend(payload[list_key])
        next_page = payload.get("next_page")
        if isinstance(next_page, int) and not isinstance(next_page, bool) and next_page >= 1:
            page = next_page
        else:
            return records
    raise SyncError("%s: did not reach a last page within %d pages"
                    % (path, MAX_PAGES))


def _post_summary(ledger: HttpClient, limiter: RateLimiter,
                  body: Dict[str, Any]) -> None:
    """POST one summary; 201 (accepted) and 200 (replay) are both success."""
    key = IDEMPOTENCY_PREFIX + body["owner"]
    response = _send(ledger, limiter, "POST", SUMMARIES_PATH, payload=body,
                     headers={"Idempotency-Key": key})
    if response.status not in (200, 201):
        raise SyncError("ledger rejected the summary for %r: HTTP %d"
                        % (body["owner"], response.status))


# --------------------------------------------------------------------------
# Output files. errors.jsonl and orphans.jsonl are rewritten whole each run
# (unchanged services produce unchanged bytes); summary.jsonl is append-only,
# because it records what this run posted and a second run posts nothing.
# --------------------------------------------------------------------------

def _write_jsonl(path: str, entries: List[Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry))
            handle.write("\n")


def _append_jsonl(path: str, entry: Any) -> None:
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry))
        handle.write("\n")


def _note_error(errors: Dict[Tuple[str, str], Dict[str, Any]],
                service: str, record: Any) -> None:
    """Record a malformed delivery once, however often it was delivered."""
    key = (service, json.dumps(record, sort_keys=True))
    errors.setdefault(key, {"service": service, "record": record})


# --------------------------------------------------------------------------
# The command.
# --------------------------------------------------------------------------

def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)
    directory_limiter = RateLimiter()
    ledger_limiter = RateLimiter()

    # 1. Read everything.
    account_records = _fetch_all(ledger, ledger_limiter, ACCOUNTS_PATH, "accounts")
    item_records = _fetch_all(directory, directory_limiter, ITEMS_PATH, "items")
    summary_records = _fetch_all(ledger, ledger_limiter, SUMMARIES_PATH, "summaries")

    # 4. Split well-formed from malformed. Malformed records stop nothing and
    #    count for nothing; a malformed account makes nobody active.
    errors: Dict[Tuple[str, str], Dict[str, Any]] = {}
    accounts: List[Dict[str, Any]] = []
    for record in account_records:
        if account_is_well_formed(record):
            accounts.append(record)
        else:
            _note_error(errors, "ledger", record)

    items: Dict[str, Dict[str, Any]] = {}  # id -> record; ids are unique
    for record in item_records:
        if item_is_well_formed(record):
            items.setdefault(record["id"], record)
        else:
            _note_error(errors, "directory", record)

    # 2. The ledger decides who counts; 3. everyone else's items are orphans.
    active_owners = {account["owner"] for account in accounts if account["active"]}
    counts = {owner: {"open": 0, "done": 0, "points": 0} for owner in active_owners}
    orphans: Dict[str, Dict[str, Any]] = {}  # id -> record; each orphan once
    for item in items.values():
        owner_counts = counts.get(item["owner"])
        if owner_counts is None:
            orphans.setdefault(item["id"], item)
            continue
        owner_counts["open" if item["status"] == "open" else "done"] += 1
        owner_counts["points"] += item["points"]

    required = [Summary(owner=owner,
                        open_items=counts[owner]["open"],
                        done_items=counts[owner]["done"],
                        total_points=counts[owner]["points"])
                for owner in sorted(active_owners)]

    # 5. Post what the ledger is missing, and only that.
    owners_already_summarized = {
        record["owner"] for record in summary_records
        if isinstance(record, dict) and isinstance(record.get("owner"), str)}
    to_post = [summary for summary in required
               if summary.owner not in owners_already_summarized]

    os.makedirs(out_dir, exist_ok=True)
    _write_jsonl(os.path.join(out_dir, ERRORS_FILENAME), list(errors.values()))
    _write_jsonl(os.path.join(out_dir, ORPHANS_FILENAME), list(orphans.values()))
    summary_path = os.path.join(out_dir, SUMMARY_FILENAME)
    open(summary_path, "a").close()  # exist even when nothing is posted
    for summary in to_post:
        body = summary.to_dict()
        _post_summary(ledger, ledger_limiter, body)
        _append_jsonl(summary_path, body)
    return 0
