"""The `sync` command: reconcile the Directory and the Ledger.

One pass reads every page of items, accounts and existing summaries,
decides which records are usable, computes one summary per active
well-formed account, posts the summaries the ledger is missing, and
writes what it did under the output directory as three JSON-lines
files: `orphans.jsonl`, `errors.jsonl` and `summary.jsonl`.

Because a passing run must be repeatable, the files are rewritten from
scratch each pass with content derived only from what the services say:
run against unchanged services the second pass posts nothing (every
required summary already exists) and reproduces the same three files.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .config import Config
from .http_client import HttpClient, Response

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

ORPHANS_FILENAME = "orphans.jsonl"
ERRORS_FILENAME = "errors.jsonl"
SUMMARIES_FILENAME = "summary.jsonl"

ITEMS_PATH = "/v1/items"
ACCOUNTS_PATH = "/v1/accounts"
SUMMARIES_PATH = "/v1/summaries"

ITEM_STATUSES = ("open", "done")

#: A 429 never abandons the work, but a service answering 429 forever
#: must not hang the run; this caps the attempts per single request.
MAX_ATTEMPTS_PER_REQUEST = 100

#: Belt-against-loops guard for pagination.
MAX_PAGES = 10000

#: Used when a 429 arrives without a parseable `Retry-After` header.
RETRY_AFTER_FALLBACK_SECONDS = 1.0

#: Slept on top of `Retry-After` so the wait is strictly "at least".
RATE_LIMIT_CUSHION_SECONDS = 0.01

Sleep = Callable[[float], None]


class SyncError(Exception):
    """A reconciliation could not be completed; sync fails loudly."""


def _is_int(value: Any) -> bool:
    """True for a JSON integer — a Python bool is not one, whatever isinstance says."""
    return isinstance(value, int) and not isinstance(value, bool)


def _well_formed_item(record: Any) -> bool:
    """A well-formed item: right fields, right JSON types, points >= 0."""
    if not isinstance(record, dict):
        return False
    return (
        isinstance(record.get("id"), str)
        and isinstance(record.get("owner"), str)
        and record.get("status") in ITEM_STATUSES
        and _is_int(record.get("points"))
        and record["points"] >= 0
    )


def _well_formed_account(record: Any) -> bool:
    """A well-formed account: right fields and a JSON boolean `active`."""
    if not isinstance(record, dict):
        return False
    return (
        isinstance(record.get("id"), str)
        and isinstance(record.get("owner"), str)
        and isinstance(record.get("active"), bool)
    )


def _retry_after_seconds(value: Any) -> float:
    """Parse a `Retry-After` header of whole seconds; fall back to 1.0."""
    if value is None:
        return RETRY_AFTER_FALLBACK_SECONDS
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return RETRY_AFTER_FALLBACK_SECONDS
    return max(seconds, 0.0)


class ServiceClient:
    """An `HttpClient` wearing the rate-limit rule of the `sync` command.

    Every request goes through `request`, which turns a 429 into a wait
    of at least `Retry-After` seconds and a retry of the very same
    request. A 429 is therefore never returned to the caller and never
    counts as data; any other status comes back as an ordinary
    `Response` for the caller to interpret.
    """

    def __init__(self, base_url: str, opener: Optional[Any] = None,
                 sleep: Sleep = time.sleep) -> None:
        self._http = HttpClient(base_url, opener=opener)
        self._sleep = sleep

    def request(self, method: str, path: str,
                params: Optional[Dict[str, Any]] = None,
                payload: Optional[Any] = None,
                headers: Optional[Dict[str, str]] = None) -> Response:
        for attempt in range(1, MAX_ATTEMPTS_PER_REQUEST + 1):
            response = self._http.request(method, path, params=params,
                                          payload=payload, headers=headers)
            if response.status != 429:
                return response
            if attempt == MAX_ATTEMPTS_PER_REQUEST:
                raise SyncError("%s %s: rate-limited %d times in a row"
                                % (method, path, attempt))
            self._sleep(_retry_after_seconds(response.headers.get("Retry-After"))
                        + RATE_LIMIT_CUSHION_SECONDS)
        raise SyncError("unreachable")


def _fetch_all(client: ServiceClient, path: str, records_key: str) -> List[Any]:
    """Follow `next_page` until it is null and return every record delivered.

    The page envelope itself must be sound: a non-200 status, a body that
    is not JSON, or a missing or wrongly-typed collection is a hard error,
    because there is no well-formed way to continue past it.
    """
    records: List[Any] = []
    page: Optional[int] = 1
    seen_pages: Set[int] = set()
    while page is not None:
        if page in seen_pages:
            raise SyncError("%s: page %d offered twice" % (path, page))
        seen_pages.add(page)
        if len(seen_pages) > MAX_PAGES:
            raise SyncError("%s: refused to follow more than %d pages"
                            % (path, MAX_PAGES))
        response = client.request("GET", path, params={"page": page})
        if response.status != 200:
            raise SyncError("GET %s?page=%d: unexpected status %d"
                            % (path, page, response.status))
        try:
            body = response.json()
        except ValueError as exc:
            raise SyncError("GET %s?page=%d: body is not JSON: %s"
                            % (path, page, exc)) from exc
        if not isinstance(body, dict) or not isinstance(body.get(records_key), list):
            raise SyncError("GET %s?page=%d: expected an object with a %s list"
                            % (path, page, records_key))
        records.extend(body[records_key])
        next_page = body.get("next_page")
        if next_page is None:
            break
        if not _is_int(next_page):
            raise SyncError("GET %s?page=%d: next_page is %r, not an integer"
                            % (path, page, next_page))
        page = next_page
    return records


def _write_jsonl(path: str, entries: List[Any]) -> None:
    """Write `entries` as one JSON line each, truncating any earlier content."""
    with open(path, "w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry))
            handle.write("\n")


def _reconcile(item_records: List[Any], account_records: List[Any]
               ) -> Tuple[Dict[str, Dict[str, Any]], List[Any],
                          List[Dict[str, Any]]]:
    """Turn raw records into (summaries by owner, orphans, error lines).

    Malformed records are skipped and reported, never counted; item ids
    are unique, so a well-formed item delivered twice is counted once
    and a well-formed orphan written once. Error lines are deduplicated
    so a repeated malformed record is also reported exactly once.
    """
    error_lines: List[Dict[str, Any]] = []
    error_keys: Set[str] = set()

    def report_error(service: str, record: Any) -> None:
        line = {"service": service, "record": record}
        key = json.dumps(line, sort_keys=True)
        if key not in error_keys:
            error_keys.add(key)
            error_lines.append(line)

    active_owners: Set[str] = set()
    for record in account_records:
        if _well_formed_account(record):
            if record["active"]:
                active_owners.add(record["owner"])
        else:
            report_error("ledger", record)

    summaries: Dict[str, Dict[str, Any]] = {
        owner: {"owner": owner, "open_items": 0, "done_items": 0,
                "total_points": 0}
        for owner in active_owners
    }
    orphans: List[Any] = []
    seen_item_ids: Set[str] = set()
    for record in item_records:
        if not _well_formed_item(record):
            report_error("directory", record)
            continue
        if record["id"] in seen_item_ids:
            continue
        seen_item_ids.add(record["id"])
        owner = record["owner"]
        if owner not in active_owners:
            orphans.append(record)
            continue
        summary = summaries[owner]
        if record["status"] == "open":
            summary["open_items"] += 1
        else:
            summary["done_items"] += 1
        summary["total_points"] += record["points"]

    return summaries, orphans, error_lines


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    directory = ServiceClient(config.directory_url)
    ledger = ServiceClient(config.ledger_url)

    item_records = _fetch_all(directory, ITEMS_PATH, "items")
    account_records = _fetch_all(ledger, ACCOUNTS_PATH, "accounts")
    summary_records = _fetch_all(ledger, SUMMARIES_PATH, "summaries")

    summaries, orphans, error_lines = _reconcile(item_records, account_records)

    posted_owners: Set[str] = set()
    for record in summary_records:
        if isinstance(record, dict) and isinstance(record.get("owner"), str):
            posted_owners.add(record["owner"])

    for owner in sorted(summaries):
        if owner in posted_owners:
            continue
        body = summaries[owner]
        response = ledger.request("POST", SUMMARIES_PATH, payload=body,
                                  headers={"Idempotency-Key": "sync-%s" % owner})
        if response.status not in (200, 201):
            raise SyncError("POST summary for %r: unexpected status %d"
                            % (owner, response.status))

    os.makedirs(out_dir, exist_ok=True)
    _write_jsonl(os.path.join(out_dir, ORPHANS_FILENAME), orphans)
    _write_jsonl(os.path.join(out_dir, ERRORS_FILENAME), error_lines)
    _write_jsonl(os.path.join(out_dir, SUMMARIES_FILENAME),
                 [summaries[owner] for owner in sorted(summaries)])
    return 0
