"""The `sync` command: reconcile the Directory and the Ledger.

One pass reads every page of accounts, items and existing summaries, decides
which records are usable, computes one summary per active account, posts the
summaries the Ledger does not have yet, and writes three files under `out/`:

* `summary.jsonl` — the summaries orgsync has posted (exactly the POST bodies);
* `orphans.jsonl` — well-formed items whose owner is not an active account;
* `errors.jsonl`  — malformed records, tagged with the service that sent them.

Rate limits (`429` + `Retry-After`) are honoured per service: after a 429, that
service gets no request at all until the wait has elapsed, and the request is
then retried. A 429 is never data and never abandons the work.
"""

from __future__ import annotations

import email.utils
import json
import os
import sys
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .config import Config
from .http_client import HttpClient, HttpClientError, Response
from .models import Account, Item, Summary

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

SUMMARY_FILE = "summary.jsonl"
ORPHANS_FILE = "orphans.jsonl"
ERRORS_FILE = "errors.jsonl"

DIRECTORY = "directory"
LEDGER = "ledger"

#: Wait used when a 429 carries no usable Retry-After.
DEFAULT_RETRY_AFTER_SECONDS = 1.0
#: Added to every Retry-After so we never arrive a hair too early.
RETRY_AFTER_MARGIN_SECONDS = 0.05
#: Attempts for transport failures, 5xx and unparseable pages (not for 429).
MAX_TRANSIENT_ATTEMPTS = 6


class SyncError(Exception):
    """The run could not complete (a service kept failing or answered oddly)."""


# --------------------------------------------------------------------------
# HTTP with rate limiting


def parse_retry_after(value: Optional[str], now: Callable[[], float] = time.time) -> float:
    """Seconds to wait for a `Retry-After` header (delta-seconds or HTTP-date)."""
    if value is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return DEFAULT_RETRY_AFTER_SECONDS
        if when is None:
            return DEFAULT_RETRY_AFTER_SECONDS
        seconds = when.timestamp() - now()
    if seconds != seconds:  # NaN
        return DEFAULT_RETRY_AFTER_SECONDS
    return max(0.0, seconds)


class ServiceClient:
    """An `HttpClient` for one service that honours that service's 429s.

    `sleep` and `clock` are injectable for tests. The gate is per instance, so
    a 429 from one service never delays the other.
    """

    def __init__(self, name: str, http: HttpClient,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.name = name
        self.http = http
        self._sleep = sleep
        self._clock = clock
        self._not_before = 0.0

    def _wait_for_gate(self) -> None:
        while True:
            remaining = self._not_before - self._clock()
            if remaining <= 0:
                return
            self._sleep(remaining)

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
                payload: Any = None, headers: Optional[Dict[str, str]] = None) -> Response:
        """Send one logical request, retrying through 429s (always) and through
        transport failures / 5xx (a bounded number of times)."""
        transient_failures = 0
        while True:
            self._wait_for_gate()
            try:
                response = self.http.request(method, path, params=params,
                                             payload=payload, headers=headers)
            except HttpClientError as exc:
                transient_failures += 1
                if transient_failures >= MAX_TRANSIENT_ATTEMPTS:
                    raise SyncError("%s: %s" % (self.name, exc)) from exc
                self._backoff(transient_failures)
                continue
            if response.status == 429:
                wait = parse_retry_after(response.headers.get("Retry-After")
                                         if response.headers is not None else None)
                self._not_before = self._clock() + wait + RETRY_AFTER_MARGIN_SECONDS
                continue
            if response.status >= 500:
                transient_failures += 1
                if transient_failures >= MAX_TRANSIENT_ATTEMPTS:
                    raise SyncError("%s: %s %s kept failing with %d"
                                    % (self.name, method, path, response.status))
                self._backoff(transient_failures)
                continue
            return response

    def _backoff(self, attempt: int) -> None:
        self._not_before = max(self._not_before,
                               self._clock() + min(0.2 * (2 ** (attempt - 1)), 5.0))

    def fetch_all(self, path: str, key: str) -> List[Any]:
        """Every record under `key` across all pages, following `next_page`."""
        records: List[Any] = []
        page: Any = 1
        seen_pages = set()
        while page is not None:
            marker = json.dumps(page, sort_keys=True)
            if marker in seen_pages:  # a pagination cycle: we already have it
                break
            seen_pages.add(marker)
            body = self._fetch_page(path, key, page)
            records.extend(body[key])
            page = body.get("next_page")
        return records

    def _fetch_page(self, path: str, key: str, page: Any) -> Dict[str, Any]:
        bad_answers = 0
        while True:
            response = self.request("GET", path, params={"page": page})
            problem = None
            if response.status != 200:
                problem = "status %d" % response.status
            else:
                try:
                    body = response.json()
                except ValueError as exc:
                    problem = "unparseable body (%s)" % exc
                else:
                    if not isinstance(body, dict) or not isinstance(body.get(key), list):
                        problem = "no %r list in body" % key
                    else:
                        return body
            bad_answers += 1
            if bad_answers >= MAX_TRANSIENT_ATTEMPTS:
                raise SyncError("%s: GET %s page %r: %s" % (self.name, path, page, problem))
            self._backoff(bad_answers)


# --------------------------------------------------------------------------
# Validation


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_account(record: Any) -> Optional[Account]:
    """An `Account` if `record` is a well-formed account, else None."""
    if not isinstance(record, dict):
        return None
    ident, owner, active = record.get("id"), record.get("owner"), record.get("active")
    if not isinstance(ident, str) or not isinstance(owner, str) or not isinstance(active, bool):
        return None
    return Account(ident, owner, active)


def parse_item(record: Any) -> Optional[Item]:
    """An `Item` if `record` is a well-formed work item, else None."""
    if not isinstance(record, dict):
        return None
    ident, owner = record.get("id"), record.get("owner")
    status, points = record.get("status"), record.get("points")
    if not isinstance(ident, str) or not isinstance(owner, str):
        return None
    if status not in ("open", "done") or not isinstance(status, str):
        return None
    if not _is_int(points) or points < 0:
        return None
    return Item(ident, owner, status, points)


def _canonical(record: Any) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --------------------------------------------------------------------------
# Reconciliation (pure: no I/O)


class Plan:
    """What one pass decided, before any write happens."""

    def __init__(self) -> None:
        self.required: Dict[str, Summary] = {}       # owner -> summary it must have
        self.orphans: List[Any] = []                 # items as received
        self.errors: List[Dict[str, Any]] = []       # {"service", "record"}


def reconcile(accounts: Iterable[Any], items: Iterable[Any]) -> Plan:
    """Decide summaries, orphans and errors from the raw records."""
    plan = Plan()
    seen_errors = set()

    def error(service: str, record: Any) -> None:
        entry = {"service": service, "record": record}
        key = _canonical(entry)
        if key not in seen_errors:
            seen_errors.add(key)
            plan.errors.append(entry)

    active_owners = set()
    for record in accounts:
        account = parse_account(record)
        if account is None:
            error(LEDGER, record)
        elif account.active:
            active_owners.add(account.owner)

    unique_items: Dict[str, Tuple[Item, Any]] = {}
    for record in items:
        item = parse_item(record)
        if item is None:
            error(DIRECTORY, record)
        elif item.id not in unique_items:
            unique_items[item.id] = (item, record)

    counts = {owner: [0, 0, 0] for owner in active_owners}
    for item, record in unique_items.values():
        tally = counts.get(item.owner)
        if tally is None:
            plan.orphans.append(record)
            continue
        tally[0 if item.status == "open" else 1] += 1
        tally[2] += item.points

    for owner in sorted(counts):
        open_items, done_items, points = counts[owner]
        plan.required[owner] = Summary(owner, open_items, done_items, points)
    return plan


def existing_summary_owners(summaries: Iterable[Any]) -> set:
    """Owners that already have a summary in the Ledger."""
    return {s["owner"] for s in summaries
            if isinstance(s, dict) and isinstance(s.get("owner"), str)}


def idempotency_key(owner: str) -> str:
    return "sync-%s" % owner


# --------------------------------------------------------------------------
# Output


def _read_jsonl(path: str) -> List[Any]:
    entries: List[Any] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        pass
    return entries


def _write_jsonl(path: str, entries: Iterable[Any]) -> None:
    """Write each distinct entry as one JSON line, in a stable order, atomically.

    Entries keep their own key order; duplicates (by content) are dropped.
    """
    unique: Dict[str, Any] = {}
    for entry in entries:
        unique.setdefault(_canonical(entry), entry)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        for key in sorted(unique):
            handle.write(json.dumps(unique[key], ensure_ascii=False) + "\n")
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# The command


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR,
             sleep: Callable[[float], None] = time.sleep,
             clock: Callable[[], float] = time.monotonic) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    directory = ServiceClient(DIRECTORY, HttpClient(config.directory_url), sleep, clock)
    ledger = ServiceClient(LEDGER, HttpClient(config.ledger_url), sleep, clock)

    try:
        accounts = ledger.fetch_all("/v1/accounts", "accounts")
        items = directory.fetch_all("/v1/items", "items")
        summaries = ledger.fetch_all("/v1/summaries", "summaries")
    except SyncError as exc:
        print("orgsync: %s" % exc, file=sys.stderr)
        return 1

    plan = reconcile(accounts, items)
    already_there = existing_summary_owners(summaries)

    os.makedirs(out_dir, exist_ok=True)
    summary_path = os.path.join(out_dir, SUMMARY_FILE)

    # Summaries an earlier run posted stay recorded while the Ledger still holds
    # them, so a rerun that posts nothing leaves summary.jsonl unchanged.
    posted: Dict[str, Dict[str, Any]] = {}
    for entry in _read_jsonl(summary_path):
        if not isinstance(entry, dict):
            continue
        owner = entry.get("owner")
        wanted = plan.required.get(owner) if isinstance(owner, str) else None
        if wanted is not None and owner in already_there and entry == wanted.to_dict():
            posted[owner] = wanted.to_dict()

    failures: List[str] = []
    for owner, summary in plan.required.items():
        if owner in already_there:
            continue
        body = summary.to_dict()
        try:
            response = ledger.request("POST", "/v1/summaries", payload=body,
                                      headers={"Idempotency-Key": idempotency_key(owner)})
        except SyncError as exc:
            failures.append(str(exc))
            continue
        if response.status in (200, 201):
            posted[owner] = body
        else:
            failures.append("POST summary for %r answered %d: %s"
                            % (owner, response.status, response.text[:200]))

    _write_jsonl(summary_path, posted.values())
    _write_jsonl(os.path.join(out_dir, ORPHANS_FILE), plan.orphans)
    _write_jsonl(os.path.join(out_dir, ERRORS_FILE), plan.errors)

    for failure in failures:
        print("orgsync: %s" % failure, file=sys.stderr)
    return 1 if failures else 0
