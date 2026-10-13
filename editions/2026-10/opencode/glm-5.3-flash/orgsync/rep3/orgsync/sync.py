"""The `sync` command: reconcile the Directory and the Ledger.

Everything else in the package is in place — config loading, the HTTP client,
the record types. This module holds the part that makes decisions: it reads
every page from both services, decides which records are well-formed and which
owners count, computes the per-owner summaries the Ledger ought to hold, posts
only what is missing, and writes the run's findings under `out/`.

The rules it implements:

- The Ledger decides who counts: one summary per active, well-formed account.
- Items whose owner has no active, well-formed account are orphans.
- Malformed records stop nothing and count for nothing; they are recorded.
- A 429 is a pause, not a rejection and not a loss of work: after one, no
  further request goes to that service until `Retry-After` seconds have
  passed, then the same request is retried.
- Sync is idempotent: run against unchanged services a second time, it issues
  no writes and leaves the output files byte-for-byte unchanged.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .config import Config
from .http_client import HttpClient, HttpClientError, Response
from .models import Account, Item, Summary

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

#: Service endpoints, relative to each service's base URL.
ACCOUNTS_PATH = "/v1/accounts"
ITEMS_PATH = "/v1/items"
SUMMARIES_PATH = "/v1/summaries"

#: The statuses a well-formed item may carry; anything else is malformed.
ITEM_STATUSES = ("open", "done")

#: Header carrying the pause a rate-limited response asks for.
RETRY_AFTER_HEADER = "Retry-After"

#: Used when a 429 arrives without a usable `Retry-After` value.
DEFAULT_RETRY_AFTER_SECONDS = 1.0

#: Prefix of the `Idempotency-Key` sent with every summary POST.
IDEMPOTENCY_PREFIX = "sync-"

#: Safety valves: a page must never be visited twice, there cannot be too
#: many pages, a service cannot answer 429 forever, and a transport failure
#: is retried only so many times before the run reports failure.
MAX_PAGES = 10_000
MAX_CONSECUTIVE_429 = 100
MAX_TRANSIENT_ATTEMPTS = 3
TRANSIENT_RETRY_PAUSE_SECONDS = 0.25

#: The output files, written under the out directory.
ORPHANS_FILE = "orphans.jsonl"
ERRORS_FILE = "errors.jsonl"
SUMMARY_FILE = "summary.jsonl"


class SyncError(Exception):
    """The reconciliation could not be completed."""


class Throttle:
    """Per-service rate-limit state.

    After a service answers 429, `defer` records how long that service wants
    to be left alone; `wait`, called before every request to that service,
    sleeps out the remaining silence. Requests to different services get
    different throttles, so a pause on one never delays the other.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._clock = clock
        self._sleep = sleep
        self._silent_until = clock()

    def wait(self) -> None:
        """Block until the service may be asked something again."""
        remaining = self._silent_until - self._clock()
        if remaining > 0:
            self._sleep(remaining)

    def defer(self, seconds: float) -> None:
        """Hold future requests until `seconds` from now have passed."""
        earliest = self._clock() + max(0.0, seconds)
        if earliest > self._silent_until:
            self._silent_until = earliest


def retry_after_seconds(response: Response) -> float:
    """The pause a 429 response asks for, in seconds.

    Falls back to `DEFAULT_RETRY_AFTER_SECONDS` when the header is missing or
    not a plain number of seconds; a pause of zero or less is no pause.
    """
    raw = response.headers.get(RETRY_AFTER_HEADER)
    if raw is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    try:
        return max(0.0, float(str(raw).strip()))
    except ValueError:
        return DEFAULT_RETRY_AFTER_SECONDS


def request_with_retry(client: HttpClient, throttle: Throttle, method: str, *,
                       path: str, params: Optional[Dict[str, Any]] = None,
                       payload: Optional[Any] = None,
                       headers: Optional[Dict[str, str]] = None) -> Response:
    """One unit of work against one service, honoring its rate limit.

    Every attempt first waits out the throttle. A 429 re-arms the throttle
    with the response's `Retry-After` and the same request is sent again —
    a 429 is never data and never abandons the work. Transport failures and
    5xx answers are retried a bounded number of times; anything else comes
    back for the caller to judge.
    """
    transient_left = MAX_TRANSIENT_ATTEMPTS
    consecutive_429 = 0
    while True:
        throttle.wait()
        try:
            response = client.request(method, path, params=params,
                                      payload=payload, headers=headers)
        except HttpClientError as exc:
            transient_left -= 1
            if transient_left <= 0:
                raise SyncError("%s %s kept failing: %s" % (method, path, exc)) from exc
            throttle.defer(TRANSIENT_RETRY_PAUSE_SECONDS)
            continue
        if response.status == 429:
            consecutive_429 += 1
            if consecutive_429 > MAX_CONSECUTIVE_429:
                raise SyncError("%s %s answered 429 %d times in a row"
                                % (method, path, consecutive_429))
            throttle.defer(retry_after_seconds(response))
            continue
        if response.status >= 500:
            transient_left -= 1
            if transient_left <= 0:
                raise SyncError("%s %s answered HTTP %d"
                                % (method, path, response.status))
            throttle.defer(TRANSIENT_RETRY_PAUSE_SECONDS)
            continue
        return response


def read_all_pages(client: HttpClient, throttle: Throttle, path: str,
                   list_key: str) -> List[Any]:
    """Follow `next_page` from page 1 and collect every record under `list_key`."""
    records: List[Any] = []
    page: Optional[int] = 1
    seen: Set[int] = set()
    while page is not None:
        if page in seen or len(seen) >= MAX_PAGES:
            raise SyncError("%s pagination is not making progress (page %r)"
                            % (path, page))
        seen.add(page)
        response = request_with_retry(client, throttle, "GET", path=path,
                                      params={"page": page})
        if response.status != 200:
            raise SyncError("%s page %d answered HTTP %d"
                            % (path, page, response.status))
        try:
            envelope = response.json()
        except ValueError as exc:
            raise SyncError("%s page %d has an unreadable body: %s"
                            % (path, page, exc)) from exc
        if not isinstance(envelope, dict) or not isinstance(envelope.get(list_key), list):
            raise SyncError("%s page %d does not carry a %r list" % (path, page, list_key))
        records.extend(envelope[list_key])
        next_page = envelope.get("next_page")
        if next_page is None:
            page = None
        elif isinstance(next_page, bool) or not isinstance(next_page, int):
            raise SyncError("%s page %d has a non-integer next_page %r"
                            % (path, page, next_page))
        else:
            page = next_page
    return records


def well_formed_account(record: Any) -> Optional[Account]:
    """The account a record describes, or None if the record is not one.

    Well-formed: a JSON object whose `id` and `owner` are strings and whose
    `active` is a JSON boolean. Extra fields never matter.
    """
    if not isinstance(record, dict):
        return None
    account_id = record.get("id")
    owner = record.get("owner")
    active = record.get("active")
    if not (isinstance(account_id, str) and isinstance(owner, str)
            and isinstance(active, bool)):
        return None
    return Account(account_id, owner, active)


def well_formed_item(record: Any) -> Optional[Item]:
    """The work item a record describes, or None if the record is not one.

    Well-formed: a JSON object whose `id` and `owner` are strings, whose
    `status` is `open` or `done`, and whose `points` is a JSON integer that
    is not negative — not a string, not a float, not a boolean. Extra fields
    never matter.
    """
    if not isinstance(record, dict):
        return None
    item_id = record.get("id")
    owner = record.get("owner")
    status = record.get("status")
    points = record.get("points")
    if not (isinstance(item_id, str) and isinstance(owner, str)):
        return None
    if status not in ITEM_STATUSES:
        return None
    if isinstance(points, bool) or not isinstance(points, int) or points < 0:
        return None
    return Item(item_id, owner, status, points)


def summarized_owners(summary_records: List[Any]) -> Set[str]:
    """The owners the Ledger already holds a summary for."""
    owners: Set[str] = set()
    for record in summary_records:
        if isinstance(record, dict) and isinstance(record.get("owner"), str):
            owners.add(record["owner"])
    return owners


def write_lines(path: str, lines: List[str]) -> None:
    """Write `lines` as one JSON-lines file, newest content throughout."""
    with open(path, "w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")


def read_lines(path: str) -> List[str]:
    """The non-blank lines of an existing JSON-lines file, or []."""
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        return [line.rstrip("\n") for line in handle if line.strip()]


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)
    directory_throttle = Throttle()
    ledger_throttle = Throttle()

    account_records = read_all_pages(ledger, ledger_throttle,
                                     ACCOUNTS_PATH, "accounts")
    item_records = read_all_pages(directory, directory_throttle,
                                  ITEMS_PATH, "items")
    summary_records = read_all_pages(ledger, ledger_throttle,
                                     SUMMARIES_PATH, "summaries")

    # --- Sort every record into its bucket ------------------------------
    errors: List[Tuple[str, Any]] = []

    accounts: Dict[str, Account] = {}  # owner -> account (owner is unique)
    for record in account_records:
        account = well_formed_account(record)
        if account is None:
            errors.append(("ledger", record))
        else:
            accounts.setdefault(account.owner, account)
    active_owners = {owner for owner, account in accounts.items()
                     if account.active}

    items: Dict[str, Item] = {}        # id -> item; a repeated id is a repeat
    received_items: Dict[str, Any] = {}  # id -> the record as received
    for record in item_records:
        item = well_formed_item(record)
        if item is None:
            errors.append(("directory", record))
            continue
        if item.id not in items:
            items[item.id] = item
            received_items[item.id] = record

    # --- Count, and set aside the orphans -------------------------------
    counts: Dict[str, List[int]] = {owner: [0, 0, 0] for owner in active_owners}
    orphan_lines: List[str] = []
    seen_orphans: Set[str] = set()
    for item_id, item in items.items():
        if item.owner in active_owners:
            bucket = counts[item.owner]
            if item.status == "open":
                bucket[0] += 1
            else:
                bucket[1] += 1
            bucket[2] += item.points
        else:
            line = json.dumps(received_items[item_id])
            if line not in seen_orphans:
                seen_orphans.add(line)
                orphan_lines.append(line)

    error_lines: List[str] = []
    seen_errors: Set[Tuple[str, str]] = set()
    for service, record in errors:
        identity = (service, json.dumps(record, sort_keys=True))
        if identity in seen_errors:
            continue
        seen_errors.add(identity)
        error_lines.append(json.dumps({"service": service, "record": record}))

    # --- Post only what the Ledger is missing ---------------------------
    already_summarized = summarized_owners(summary_records)
    posted_lines: List[str] = []
    for owner in sorted(active_owners):
        if owner in already_summarized:
            continue
        open_items, done_items, total_points = counts[owner]
        body = Summary(owner, open_items, done_items, total_points).to_dict()
        response = request_with_retry(
            ledger, ledger_throttle, "POST", path=SUMMARIES_PATH, payload=body,
            headers={"Idempotency-Key": IDEMPOTENCY_PREFIX + owner})
        if response.status not in (200, 201):
            raise SyncError("ledger rejected the summary for %r: HTTP %d"
                            % (owner, response.status))
        posted_lines.append(json.dumps(body))

    # --- Write the run's findings ---------------------------------------
    os.makedirs(out_dir, exist_ok=True)
    write_lines(os.path.join(out_dir, ORPHANS_FILE), orphan_lines)
    write_lines(os.path.join(out_dir, ERRORS_FILE), error_lines)
    summary_path = os.path.join(out_dir, SUMMARY_FILE)
    previous = read_lines(summary_path)
    known = set(previous)
    write_lines(summary_path,
                previous + [line for line in posted_lines if line not in known])
    return 0
