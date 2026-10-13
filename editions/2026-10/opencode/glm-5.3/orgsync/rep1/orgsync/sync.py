"""The `sync` command: reconcile the Directory and the Ledger.

One pass reads every page the two services will give up (accounts, items,
existing summaries), decides which per-owner summaries the Ledger is owed,
writes the oddments — orphaned items, malformed records — to `out/`, and
posts, and only posts, the summaries the Ledger does not have yet.

The rules, in short:

* only active, well-formed accounts own a summary; an active account with no
  items is owed three zeroes;
* a well-formed item counts once no matter how many times it was delivered,
  and never counts at all unless its owner is active — otherwise it is an
  orphan, written as received;
* a malformed record never stops the run and never enters a count: it is
  written to `out/errors.jsonl` and skipped;
* a 429 is not an answer, it is a pause: wait `Retry-After` seconds, then ask
  the same thing again, and meanwhile send that service nothing.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional, Set

from .config import Config
from .http_client import HttpClient
from .models import Account, Item, Summary

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

ORPHANS_FILENAME = "orphans.jsonl"
ERRORS_FILENAME = "errors.jsonl"
SUMMARY_FILENAME = "summary.jsonl"

#: `service` tags used in out/errors.jsonl.
DIRECTORY_SERVICE = "directory"
LEDGER_SERVICE = "ledger"

#: Prefix of the Idempotency-Key header sent with every summary POST.
IDEMPOTENCY_KEY_PREFIX = "sync-"

#: Wait used when a 429 arrives without a usable `Retry-After` header.
DEFAULT_RETRY_AFTER_SECONDS = 1.0
#: Margin added to every wait so it is never a hair under what was asked.
WAIT_EPSILON_SECONDS = 0.01


class SyncError(Exception):
    """A reconciliation could not be completed; the run fails."""


# --------------------------------------------------------------------------
# Record validation — what the services say, sorted into usable and not.
# --------------------------------------------------------------------------

def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_int(value: Any) -> bool:
    # A JSON boolean is not a JSON integer, whatever Python thinks.
    return isinstance(value, int) and not isinstance(value, bool)


def parse_item(record: Any) -> Optional[Item]:
    """Return the Item `record` describes, or None if it is malformed.

    Well-formed is `{"id": str, "owner": str, "status": "open" or "done",
    "points": int >= 0}` with the JSON types exactly; extra fields are
    ignored, never a reason to reject.
    """
    if not isinstance(record, dict):
        return None
    if not _is_str(record.get("id")) or not _is_str(record.get("owner")):
        return None
    if record.get("status") not in ("open", "done"):
        return None
    points = record.get("points")
    if not _is_int(points) or points < 0:
        return None
    return Item(id=record["id"], owner=record["owner"],
                status=record["status"], points=points)


def parse_account(record: Any) -> Optional[Account]:
    """Return the Account `record` describes, or None if it is malformed."""
    if not isinstance(record, dict):
        return None
    if not _is_str(record.get("id")) or not _is_str(record.get("owner")):
        return None
    if not isinstance(record.get("active"), bool):
        return None
    return Account(id=record["id"], owner=record["owner"],
                   active=record["active"])


def summary_owner(record: Any) -> Optional[str]:
    """The owner an existing summary record belongs to, or None if it is not
    usable. Whether a summary is missing turns only on its `owner`, so that
    is the only field that matters here.
    """
    if isinstance(record, dict) and _is_str(record.get("owner")):
        return record["owner"]
    return None


# --------------------------------------------------------------------------
# One service that knows how to wait its turn.
# --------------------------------------------------------------------------

class RateLimitedClient:
    """An `HttpClient` for one service that honors 429/`Retry-After`.

    Nothing leaves this wrapper for the service until any wait a 429 asked
    for has fully elapsed, and a 429 never gives up: the same request is
    retried once the service is ready to talk again. `sleep_fn` and
    `monotonic_fn` are injectable so tests can watch the clock.
    """

    def __init__(self, http: HttpClient,
                 sleep_fn: Callable[[float], None] = time.sleep,
                 monotonic_fn: Callable[[], float] = time.monotonic) -> None:
        self._http = http
        self._sleep = sleep_fn
        self._monotonic = monotonic_fn
        self._not_before = 0.0

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self.request("GET", path, params=params)

    def post_json(self, path: str, payload: Any,
                  headers: Optional[Dict[str, str]] = None) -> Any:
        return self.request("POST", path, payload=payload, headers=headers)

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        while True:
            wait = self._not_before - self._monotonic()
            if wait > 0:
                self._sleep(wait + WAIT_EPSILON_SECONDS)
            response = self._http.request(method, path, **kwargs)
            if response.status != 429:
                return response
            self._not_before = self._monotonic() + self._retry_after(response)

    def _retry_after(self, response: Any) -> float:
        headers = getattr(response, "headers", None)
        raw = headers.get("Retry-After") if headers is not None else None
        if raw is None:
            return DEFAULT_RETRY_AFTER_SECONDS
        try:
            seconds = float(str(raw).strip())
        except ValueError:
            return DEFAULT_RETRY_AFTER_SECONDS
        return max(seconds, 0.0)


def fetch_all_pages(client: RateLimitedClient, path: str, records_key: str,
                    service_name: str) -> List[Any]:
    """Follow `next_page` from page 1 until it is null; return every record
    delivered along the way, in delivery order, duplicates included.
    """
    records: List[Any] = []
    page: Optional[int] = 1
    seen: Set[int] = set()
    while page is not None:
        if page in seen:
            raise SyncError("%s: %s page %s delivered twice"
                            % (service_name, path, page))
        seen.add(page)
        response = client.get(path, {"page": page})
        if response.status != 200:
            raise SyncError("%s: GET %s returned HTTP %s"
                            % (service_name, path, response.status))
        try:
            body = response.json()
        except ValueError as exc:
            raise SyncError("%s: GET %s page %s: body is not JSON: %s"
                            % (service_name, path, page, exc)) from exc
        if not isinstance(body, dict) or not isinstance(body.get(records_key), list):
            raise SyncError("%s: GET %s page %s: expected an object with a %r list"
                            % (service_name, path, page, records_key))
        records.extend(body[records_key])
        next_page = body.get("next_page")
        if next_page is not None and not _is_int(next_page):
            raise SyncError("%s: GET %s page %s: bad next_page %r"
                            % (service_name, path, page, next_page))
        page = next_page
    return records


# --------------------------------------------------------------------------
# Output files.
# --------------------------------------------------------------------------

def _dump(record: Any) -> str:
    return json.dumps(record, ensure_ascii=False)


def _write_jsonl(path: str, records: List[Any]) -> None:
    """Rewrite `path` with exactly `records`, one JSON line each."""
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(_dump(record) + "\n")


def _append_jsonl(path: str, records: List[Any]) -> None:
    """Add `records` to `path`, one JSON line each, leaving old lines be.

    Summary lines are a log of what this run posted; a run that posts
    nothing must leave yesterday's lines exactly as they were.
    """
    with open(path, "a", encoding="utf-8") as handle:
        for record in records:
            handle.write(_dump(record) + "\n")


# --------------------------------------------------------------------------
# The command itself.
# --------------------------------------------------------------------------

def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR,
             sleep_fn: Callable[[float], None] = time.sleep,
             monotonic_fn: Callable[[], float] = time.monotonic) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    directory = RateLimitedClient(HttpClient(config.directory_url),
                                  sleep_fn=sleep_fn, monotonic_fn=monotonic_fn)
    ledger = RateLimitedClient(HttpClient(config.ledger_url),
                               sleep_fn=sleep_fn, monotonic_fn=monotonic_fn)

    account_records = fetch_all_pages(ledger, "/v1/accounts", "accounts",
                                      LEDGER_SERVICE)
    item_records = fetch_all_pages(directory, "/v1/items", "items",
                                   DIRECTORY_SERVICE)
    summary_records = fetch_all_pages(ledger, "/v1/summaries", "summaries",
                                      LEDGER_SERVICE)

    errors: List[Dict[str, Any]] = []
    error_keys: Set[str] = set()

    def note_error(service: str, record: Any) -> None:
        key = service + "\x00" + json.dumps(record, sort_keys=True,
                                            ensure_ascii=False)
        if key not in error_keys:
            error_keys.add(key)
            errors.append({"service": service, "record": record})

    # Who counts: the owners of active, well-formed accounts. A malformed
    # account is not an account — its owner is not thereby active.
    active_owners: Set[str] = set()
    for record in account_records:
        account = parse_account(record)
        if account is None:
            note_error(LEDGER_SERVICE, record)
        elif account.active:
            active_owners.add(account.owner)

    # Per-owner arithmetic over the items, each well-formed item counted
    # once however many times it was delivered; anything whose owner is not
    # active is an orphan, kept as received.
    orphans: List[Any] = []
    seen_item_ids: Set[str] = set()
    totals: Dict[str, List[int]] = {owner: [0, 0, 0] for owner in active_owners}
    for record in item_records:
        item = parse_item(record)
        if item is None:
            note_error(DIRECTORY_SERVICE, record)
            continue
        if item.id in seen_item_ids:
            continue
        seen_item_ids.add(item.id)
        bucket = totals.get(item.owner)
        if bucket is None:
            orphans.append(record)
            continue
        bucket[0 if item.status == "open" else 1] += 1
        bucket[2] += item.points

    # What the ledger already has, so only what it is missing gets posted.
    existing_owners: Set[str] = set()
    for record in summary_records:
        owner = summary_owner(record)
        if owner is None:
            note_error(LEDGER_SERVICE, record)
        else:
            existing_owners.add(owner)

    required = {owner: Summary(owner=owner, open_items=open_items,
                               done_items=done_items, total_points=points)
                for owner, (open_items, done_items, points) in totals.items()}

    os.makedirs(out_dir, exist_ok=True)
    _write_jsonl(os.path.join(out_dir, ORPHANS_FILENAME), orphans)
    _write_jsonl(os.path.join(out_dir, ERRORS_FILENAME), errors)

    posted: List[Dict[str, Any]] = []
    for owner in sorted(required):
        if owner in existing_owners:
            continue
        body = required[owner].to_dict()
        response = ledger.post_json(
            "/v1/summaries", body,
            {"Idempotency-Key": IDEMPOTENCY_KEY_PREFIX + owner})
        if response.status not in (200, 201):
            raise SyncError("ledger returned HTTP %s for the summary of %r"
                            % (response.status, owner))
        posted.append(body)
    if posted:
        _append_jsonl(os.path.join(out_dir, SUMMARY_FILENAME), posted)
    return 0
