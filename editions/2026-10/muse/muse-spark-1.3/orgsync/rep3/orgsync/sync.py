"""The `sync` command: reconcile the Directory and the Ledger.

Everything else in the package is in place — config loading, the HTTP client,
the record types. This module holds the part that makes decisions: it reads
every page from both services (riding out 429 rate limits), decides which
per-owner summaries the ledger needs, writes what it found to `out/`, and
posts only the summaries the ledger is missing.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from .config import Config
from .http_client import HttpClient, Response
from .models import Summary

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

ERRORS_FILENAME = "errors.jsonl"
ORPHANS_FILENAME = "orphans.jsonl"
SUMMARIES_FILENAME = "summary.jsonl"

DIRECTORY_SERVICE = "directory"
LEDGER_SERVICE = "ledger"

#: Extra wait beyond `Retry-After` before retrying, so "at least N seconds"
#: holds on any clock even with scheduling jitter.
_RATE_LIMIT_BUFFER_SECONDS = 0.05
#: Wait when a 429 carries no parsable `Retry-After` header.
_DEFAULT_RETRY_AFTER_SECONDS = 1.0


def _is_json_int(value: Any) -> bool:
    """True for a JSON integer. `bool` is excluded: it subclasses int."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_well_formed_item(record: Any) -> bool:
    """A well-formed Directory item: str id/owner, open|done status, points int >= 0."""
    if not isinstance(record, dict):
        return False
    return (
        isinstance(record.get("id"), str)
        and isinstance(record.get("owner"), str)
        and record.get("status") in ("open", "done")
        and _is_json_int(record.get("points"))
        and record["points"] >= 0
    )


def _is_well_formed_account(record: Any) -> bool:
    """A well-formed Ledger account: str id/owner and a real JSON boolean `active`."""
    if not isinstance(record, dict):
        return False
    return (
        isinstance(record.get("id"), str)
        and isinstance(record.get("owner"), str)
        and isinstance(record.get("active"), bool)
    )


def _retry_after_seconds(response: Response) -> float:
    """Seconds to wait after a 429, taken from its `Retry-After` header."""
    try:
        raw = response.headers.get("Retry-After")
    except Exception:
        raw = None
    if raw is None:
        return _DEFAULT_RETRY_AFTER_SECONDS
    try:
        delay = float(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_RETRY_AFTER_SECONDS
    if delay != delay or delay in (float("inf"), float("-inf")):
        return _DEFAULT_RETRY_AFTER_SECONDS
    return max(0.0, delay)


def _request_with_retry(client: HttpClient, gates: Dict[str, float], service: str,
                        method: str, path: str, params: Optional[Dict[str, Any]] = None,
                        payload: Optional[Any] = None,
                        headers: Optional[Dict[str, str]] = None) -> Response:
    """Issue one request, transparently riding out 429 rate limits.

    After a 429 from a service, that service gets no further request until
    its `Retry-After` has elapsed; then the same request is retried. A 429
    never counts as data and never abandons the work.
    """
    while True:
        wait = gates.get(service, 0.0) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        response = client.request(method, path, params=params,
                                  payload=payload, headers=headers)
        if response.status != 429:
            return response
        gates[service] = (time.monotonic() + _retry_after_seconds(response)
                          + _RATE_LIMIT_BUFFER_SECONDS)


def _fetch_all_pages(client: HttpClient, gates: Dict[str, float], service: str,
                     path: str, records_key: str) -> List[Any]:
    """GET every page of a listing, following `next_page` until it is null."""
    records: List[Any] = []
    page: Optional[int] = 1
    visited = set()
    while page is not None:
        if page in visited:
            break
        visited.add(page)
        response = _request_with_retry(client, gates, service, "GET", path,
                                       params={"page": page})
        if response.status != 200:
            raise RuntimeError("GET %s?page=%s returned status %s: %s"
                               % (path, page, response.status, response.text[:200]))
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError("GET %s?page=%s returned invalid JSON" % (path, page)) from exc
        if not isinstance(body, dict):
            raise RuntimeError("GET %s?page=%s returned a non-object" % (path, page))
        batch = body.get(records_key, [])
        if not isinstance(batch, list):
            raise RuntimeError("GET %s?page=%s has a non-list %r" % (path, page, records_key))
        records.extend(batch)
        nxt = body.get("next_page")
        if nxt is None:
            page = None
        elif _is_json_int(nxt):
            page = nxt
        else:
            page = None
    return records


def _canonical(record: Any) -> str:
    """Stable string key for a record, used to deduplicate deliveries."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


def _write_jsonl(path: str, rows: List[Any]) -> None:
    """Write rows as JSON lines, creating an empty file when there are none."""
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)
    gates: Dict[str, float] = {}

    raw_items = _fetch_all_pages(directory, gates, DIRECTORY_SERVICE,
                                 "/v1/items", "items")
    raw_accounts = _fetch_all_pages(ledger, gates, LEDGER_SERVICE,
                                    "/v1/accounts", "accounts")
    raw_summaries = _fetch_all_pages(ledger, gates, LEDGER_SERVICE,
                                     "/v1/summaries", "summaries")

    errors: List[Dict[str, Any]] = []
    seen_errors = set()

    def note_error(service: str, record: Any) -> None:
        key = service + "\x00" + _canonical(record)
        if key not in seen_errors:
            seen_errors.add(key)
            errors.append({"service": service, "record": record})

    # Well-formed items, deduplicated by id: a repeated id is one item twice.
    seen_item_ids = set()
    valid_items: List[Dict[str, Any]] = []
    for record in raw_items:
        if _is_well_formed_item(record):
            if record["id"] not in seen_item_ids:
                seen_item_ids.add(record["id"])
                valid_items.append(record)
        else:
            note_error(DIRECTORY_SERVICE, record)

    # The ledger decides who counts: first well-formed account wins per owner.
    active_by_owner: Dict[str, bool] = {}
    for record in raw_accounts:
        if _is_well_formed_account(record):
            if record["owner"] not in active_by_owner:
                active_by_owner[record["owner"]] = record["active"]
        else:
            note_error(LEDGER_SERVICE, record)
    active_owners = {owner for owner, active in active_by_owner.items() if active}

    # Owners that already have a summary need no POST.
    existing_owners = set()
    for record in raw_summaries:
        if isinstance(record, dict) and isinstance(record.get("owner"), str):
            existing_owners.add(record["owner"])
        else:
            note_error(LEDGER_SERVICE, record)

    open_counts: Dict[str, int] = {}
    done_counts: Dict[str, int] = {}
    point_totals: Dict[str, int] = {}
    orphans: List[Dict[str, Any]] = []
    for item in valid_items:
        owner = item["owner"]
        if owner not in active_owners:
            orphans.append(item)
            continue
        if item["status"] == "open":
            open_counts[owner] = open_counts.get(owner, 0) + 1
        else:
            done_counts[owner] = done_counts.get(owner, 0) + 1
        point_totals[owner] = point_totals.get(owner, 0) + item["points"]

    required: List[Dict[str, Any]] = []
    for owner in sorted(active_owners):
        required.append(Summary(owner=owner,
                                open_items=open_counts.get(owner, 0),
                                done_items=done_counts.get(owner, 0),
                                total_points=point_totals.get(owner, 0)).to_dict())

    # Post what the ledger is missing, and only that. Owners with a summary
    # already are skipped, so no write can be rejected (400/409/422); a 429
    # is just the rate limit and is retried above.
    for body in required:
        owner = body["owner"]
        if owner in existing_owners:
            continue
        response = _request_with_retry(ledger, gates, LEDGER_SERVICE,
                                       "POST", "/v1/summaries", payload=body,
                                       headers={"Idempotency-Key": "sync-" + owner})
        if response.status not in (200, 201):
            raise RuntimeError("POST /v1/summaries for %r rejected with status %s: %s"
                               % (owner, response.status, response.text[:200]))

    orphans.sort(key=lambda item: item["id"])
    errors.sort(key=lambda entry: (entry["service"], _canonical(entry["record"])))

    os.makedirs(out_dir, exist_ok=True)
    _write_jsonl(os.path.join(out_dir, SUMMARIES_FILENAME), required)
    _write_jsonl(os.path.join(out_dir, ORPHANS_FILENAME), orphans)
    _write_jsonl(os.path.join(out_dir, ERRORS_FILENAME), errors)
    return 0
