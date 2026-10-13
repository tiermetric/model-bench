"""The `sync` command: reconcile the Directory and the Ledger.

One pass does four things, in order:

1. Read every page of accounts, items and summaries from the two services,
2. decide which records count (well-formed, and — for items — owned by an
   active, well-formed account) and which are orphans or malformed,
3. POST the summaries the ledger is missing, one per active account owner,
   under a deterministic idempotency key,
4. write `out/orphans.jsonl`, `out/errors.jsonl` and `out/summary.jsonl`.

Every request goes through `RateLimitedClient`, which honours `Retry-After`
after a 429 for the service it wraps, so a rate-limited run slows down but
never abandons its work and never counts a 429 as data.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional, Set

from .config import Config
from .http_client import HttpClient, Response
from .models import Account, Item, Summary

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

#: Output file names, relative to the out directory.
ORPHANS_FILE = "orphans.jsonl"
ERRORS_FILE = "errors.jsonl"
SUMMARY_FILE = "summary.jsonl"

#: A 429 is retried until it stops; this cap only exists so a service stuck
#: in rate-limit mode fails the run instead of hanging it forever.
MAX_ATTEMPTS = 100

#: Fallback pause when a 429 arrives without a usable `Retry-After` header.
DEFAULT_RETRY_AFTER_SECONDS = 1.0

ITEM_STATUSES = ("open", "done")


class SyncError(Exception):
    """A service answered in a way the reconciliation cannot proceed past."""


class RateLimitedClient:
    """An `HttpClient` for one service that honours 429 / `Retry-After`.

    The client keeps a per-service cooldown deadline. Before every request it
    sleeps until the deadline has passed, and a 429 response sets a new
    deadline `Retry-After` seconds into the future and the request is tried
    again. `sleep` and `monotonic` are injectable for testing.
    """

    def __init__(self, client: HttpClient,
                 sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._client = client
        self._sleep = sleep
        self._monotonic = monotonic
        self._not_before = monotonic()

    def _cooldown_remaining(self) -> float:
        return self._not_before - self._monotonic()

    def _apply_rate_limit(self, response: Response) -> float:
        raw = response.headers.get("Retry-After") if response.headers else None
        try:
            seconds = float(raw) if raw is not None else DEFAULT_RETRY_AFTER_SECONDS
        except (TypeError, ValueError):
            seconds = DEFAULT_RETRY_AFTER_SECONDS
        if seconds < 0:
            seconds = 0.0
        now = self._monotonic()
        self._not_before = max(self._not_before, now + seconds)
        return self._not_before - now

    def request(self, method: str, path: str, **kwargs: Any) -> Response:
        """Issue one request, retrying after 429s. See `HttpClient.request`."""
        for _ in range(MAX_ATTEMPTS):
            remaining = self._cooldown_remaining()
            if remaining > 0:
                self._sleep(remaining)
            response = self._client.request(method, path, **kwargs)
            if response.status != 429:
                return response
            self._apply_rate_limit(response)
        raise SyncError("gave up on %s %s after %d rate-limited attempts"
                        % (method, path, MAX_ATTEMPTS))

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Response:
        return self.request("GET", path, params=params)

    def post_json(self, path: str, payload: Any,
                  headers: Optional[Dict[str, str]] = None) -> Response:
        return self.request("POST", path, payload=payload, headers=headers)


def fetch_all_pages(client: RateLimitedClient, path: str, key: str) -> List[Any]:
    """GET `path` following `page=N` until `next_page` is null.

    Returns every record from the `key` list of every page. A `next_page`
    pointing at an already-visited page ends the walk, so a misbehaving
    service cannot loop the run forever.
    """
    records: List[Any] = []
    page: Optional[int] = 1
    visited: Set[int] = set()
    while page is not None and page not in visited:
        visited.add(page)
        response = client.get(path, {"page": page})
        if response.status != 200:
            raise SyncError("GET %s page %s returned status %d"
                            % (path, page, response.status))
        try:
            body = response.json()
        except ValueError as exc:
            raise SyncError("GET %s page %s returned a non-JSON body" % (path, page)) from exc
        if not isinstance(body, dict) or not isinstance(body.get(key), list):
            raise SyncError("GET %s page %s returned an unexpected shape" % (path, page))
        records.extend(body[key])
        next_page = body.get("next_page")
        if next_page is not None and not isinstance(next_page, int):
            raise SyncError("GET %s page %s returned a non-integer next_page" % (path, page))
        page = next_page
    return records


def _is_json_int(value: Any) -> bool:
    """True for a JSON integer — which in Python excludes booleans."""
    return isinstance(value, int) and not isinstance(value, bool)


def parse_account(record: Any) -> Optional[Account]:
    """An Account for a well-formed record, else None. Extra fields ignored."""
    if not isinstance(record, dict):
        return None
    if not isinstance(record.get("id"), str) or not isinstance(record.get("owner"), str):
        return None
    if not isinstance(record.get("active"), bool):
        return None
    return Account(id=record["id"], owner=record["owner"], active=record["active"])


def parse_item(record: Any) -> Optional[Item]:
    """An Item for a well-formed record, else None. Extra fields ignored."""
    if not isinstance(record, dict):
        return None
    if not isinstance(record.get("id"), str) or not isinstance(record.get("owner"), str):
        return None
    if record.get("status") not in ITEM_STATUSES:
        return None
    points = record.get("points")
    if not _is_json_int(points) or points < 0:
        return None
    return Item(id=record["id"], owner=record["owner"],
                status=record["status"], points=points)


def summarize(owner: str, items: List[Item]) -> Summary:
    """Fold one owner's (already deduplicated) items into their summary."""
    return Summary(
        owner=owner,
        open_items=sum(1 for item in items if item.status == "open"),
        done_items=sum(1 for item in items if item.status == "done"),
        total_points=sum(item.points for item in items),
    )


def _record_key(record: Any) -> str:
    return json.dumps(record, sort_keys=True, default=str)


def _write_jsonl(path: str, records: List[Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def _read_jsonl(path: str) -> List[Any]:
    if not os.path.exists(path):
        return []
    records = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    return records


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    directory = RateLimitedClient(HttpClient(config.directory_url))
    ledger = RateLimitedClient(HttpClient(config.ledger_url))
    return run_sync_with_clients(directory, ledger, out_dir)


def run_sync_with_clients(directory: RateLimitedClient, ledger: RateLimitedClient,
                          out_dir: str) -> int:
    """The body of `run_sync`, with the two service clients injected."""
    raw_accounts = fetch_all_pages(ledger, "/v1/accounts", "accounts")
    raw_items = fetch_all_pages(directory, "/v1/items", "items")
    raw_summaries = fetch_all_pages(ledger, "/v1/summaries", "summaries")

    # Rule 2 starts from the ledger: only active, well-formed accounts count.
    active_owners: Set[str] = set()
    error_records: List[Dict[str, Any]] = []
    error_keys: Set[str] = set()
    for record in raw_accounts:
        account = parse_account(record)
        if account is None:
            key = _record_key(("ledger", record))
            if key not in error_keys:
                error_keys.add(key)
                error_records.append({"service": "ledger", "record": record})
        elif account.active:
            active_owners.add(account.owner)

    # Rule 3 and 4 for items: malformed ones are errors, well-formed ones
    # count once per id and belong to a summary or to the orphan file.
    items_by_owner: Dict[str, List[Item]] = {owner: [] for owner in active_owners}
    seen_item_ids: Set[str] = set()
    orphan_records: List[Any] = []
    for record in raw_items:
        item = parse_item(record)
        if item is None:
            key = _record_key(("directory", record))
            if key not in error_keys:
                error_keys.add(key)
                error_records.append({"service": "directory", "record": record})
            continue
        if item.id in seen_item_ids:
            continue
        seen_item_ids.add(item.id)
        if item.owner in items_by_owner:
            items_by_owner[item.owner].append(item)
        else:
            orphan_records.append(record)

    existing_summary_owners = {
        record.get("owner") for record in raw_summaries
        if isinstance(record, dict) and isinstance(record.get("owner"), str)
    }

    # Rule 5: post only what the ledger is missing. `existing_summary_owners`
    # already covers every owner this run or an earlier one posted for, so a
    # repeated run with unchanged services sends no write request at all.
    posted: List[Dict[str, Any]] = []
    for owner in sorted(active_owners):
        if owner in existing_summary_owners:
            continue
        body = summarize(owner, items_by_owner[owner]).to_dict()
        response = ledger.post_json(
            "/v1/summaries", body, {"Idempotency-Key": "sync-%s" % owner})
        if response.status not in (200, 201):
            raise SyncError("POST summary for %r returned status %d (body %s)"
                            % (owner, response.status, response.text[:200]))
        posted.append(body)

    os.makedirs(out_dir, exist_ok=True)

    _write_jsonl(os.path.join(out_dir, ORPHANS_FILE), orphan_records)
    _write_jsonl(os.path.join(out_dir, ERRORS_FILE), error_records)

    # summary.jsonl is a run's posts merged with what earlier runs posted, so
    # that rerunning with unchanged services rewrites the very same content.
    recorded = {
        record.get("owner"): record
        for record in _read_jsonl(os.path.join(out_dir, SUMMARY_FILE))
        if isinstance(record, dict) and isinstance(record.get("owner"), str)
    }
    for body in posted:
        recorded[body["owner"]] = body
    _write_jsonl(os.path.join(out_dir, SUMMARY_FILE),
                 [recorded[owner] for owner in sorted(recorded)])
    return 0
