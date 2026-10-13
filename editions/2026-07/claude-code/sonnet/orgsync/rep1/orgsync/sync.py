"""The `sync` command: reconcile the Directory and the Ledger.

Everything else in the package is in place - config loading, the HTTP client,
the record types. What lives here is the part that has to make decisions:
which records are usable, who counts, and what to write and post.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from .config import Config
from .http_client import HttpClient, Response
from .models import Summary

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

#: Statuses a well-formed item may have.
_ITEM_STATUSES = ("open", "done")

#: Used when a 429 response is missing or has an unusable Retry-After header.
_DEFAULT_RETRY_AFTER_SECONDS = 1.0


class SyncError(Exception):
    """Raised when a service responds in a way sync cannot recover from."""


# ---------------------------------------------------------------------------
# Record validation
# ---------------------------------------------------------------------------

def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_nonneg_int(value: Any) -> bool:
    # bool is a subclass of int in Python; a JSON true/false must not pass as
    # a JSON integer.
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def is_well_formed_item(record: Any) -> bool:
    """True if `record` is `{"id": str, "owner": str, "status": open|done,
    "points": int >= 0}`. Extra fields are allowed."""
    if not isinstance(record, dict):
        return False
    return (_is_str(record.get("id")) and _is_str(record.get("owner"))
            and record.get("status") in _ITEM_STATUSES
            and _is_nonneg_int(record.get("points")))


def is_well_formed_account(record: Any) -> bool:
    """True if `record` is `{"id": str, "owner": str, "active": bool}`. Extra
    fields are allowed."""
    if not isinstance(record, dict):
        return False
    return (_is_str(record.get("id")) and _is_str(record.get("owner"))
            and _is_bool(record.get("active")))


def _record_key(record: Any) -> Tuple[str, str]:
    """A key identifying `record` for de-duplication across pages.

    Records carrying a string `id` de-duplicate on that id, per "item ids are
    unique: the same id delivered twice is the same item delivered twice".
    A record without a usable id (which only happens for a malformed record)
    falls back to its full contents, so an identical re-delivery still
    collapses to one entry while distinct malformed records both survive.
    """
    if isinstance(record, dict) and _is_str(record.get("id")):
        return ("id", record["id"])
    return ("raw", json.dumps(record, sort_keys=True, default=str))


# ---------------------------------------------------------------------------
# HTTP: pagination and rate-limit handling
# ---------------------------------------------------------------------------

def _retry_after_seconds(response: Response) -> float:
    raw = response.headers.get("Retry-After") if response.headers else None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_RETRY_AFTER_SECONDS
    return seconds if seconds > 0 else 0.0


def _get_with_retry(client: HttpClient, path: str, params: Dict[str, Any],
                     sleep=time.sleep) -> Response:
    """GET `path`, retrying on 429 until a non-429 response comes back.

    A 429 never counts as data: it is not returned, it triggers a wait for
    `Retry-After` seconds and then the same request is sent again.
    """
    while True:
        response = client.get(path, params=params)
        if response.status != 429:
            return response
        sleep(_retry_after_seconds(response))


def _fetch_all_pages(client: HttpClient, path: str, list_key: str,
                      sleep=time.sleep) -> List[Any]:
    """Follow `next_page` from page 1 until it is `null`, returning every raw
    record from `list_key` across every page, in delivery order."""
    records: List[Any] = []
    page = 1
    while page is not None:
        response = _get_with_retry(client, path, {"page": page}, sleep=sleep)
        if response.status != 200:
            raise SyncError("GET %s?page=%s: unexpected status %d"
                             % (path, page, response.status))
        body = response.json()
        records.extend(body.get(list_key) or [])
        page = body.get("next_page")
    return records


def _dedupe(records: List[Any]) -> List[Any]:
    """Keep only the first delivery of each record, per `_record_key`."""
    seen = set()
    result = []
    for record in records:
        key = _record_key(record)
        if key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def _split_items(items: List[Any]) -> Tuple[List[Dict[str, Any]], List[Any]]:
    """Return (well_formed, malformed), each de-duplicated."""
    well_formed, malformed = [], []
    for item in _dedupe(items):
        (well_formed if is_well_formed_item(item) else malformed).append(item)
    return well_formed, malformed


def _split_accounts(accounts: List[Any]) -> Tuple[List[Dict[str, Any]], List[Any]]:
    well_formed, malformed = [], []
    for account in _dedupe(accounts):
        (well_formed if is_well_formed_account(account) else malformed).append(account)
    return well_formed, malformed


def _active_owners(well_formed_accounts: List[Dict[str, Any]]) -> set:
    return {account["owner"] for account in well_formed_accounts if account["active"] is True}


def _required_summaries(well_formed_items: List[Dict[str, Any]], active_owners: set) -> Dict[str, Summary]:
    """One `Summary` per active owner, aggregating that owner's well-formed
    items; an active owner with no items still gets an all-zero `Summary`."""
    counters = {owner: {"open_items": 0, "done_items": 0, "total_points": 0}
                for owner in active_owners}
    for item in well_formed_items:
        owner = item["owner"]
        if owner not in counters:
            continue
        counters[owner]["%s_items" % item["status"]] += 1
        counters[owner]["total_points"] += item["points"]
    return {owner: Summary(owner=owner, **values) for owner, values in counters.items()}


def _orphan_items(well_formed_items: List[Dict[str, Any]], active_owners: set) -> List[Dict[str, Any]]:
    return [item for item in well_formed_items if item["owner"] not in active_owners]


def _existing_summary_owners(summaries: List[Any]) -> Dict[str, Dict[str, Any]]:
    """Map owner -> the summary already posted for them, for owners named by
    a well-formed summary record. Used only to decide what still needs
    posting and what to report as already present."""
    result: Dict[str, Dict[str, Any]] = {}
    for summary in summaries:
        if isinstance(summary, dict) and _is_str(summary.get("owner")):
            result.setdefault(summary["owner"], summary)
    return result


def _normalize_summary(record: Dict[str, Any], owner: str) -> Dict[str, Any]:
    """Pull the four summary fields out of a raw ledger record, defaulting
    anything missing/invalid to 0 so a stray extra field never leaks into
    what we record as "the summary" for this owner."""
    def _int(value: Any) -> int:
        return value if _is_nonneg_int(value) else 0
    return {"owner": owner,
            "open_items": _int(record.get("open_items")),
            "done_items": _int(record.get("done_items")),
            "total_points": _int(record.get("total_points"))}


# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------

#: Statuses that mean the ledger rejected the write outright.
_REJECTED_STATUSES = (400, 409, 422)


def _post_summary(client: HttpClient, summary: Summary, sleep=time.sleep) -> Dict[str, Any]:
    """POST `summary`, retrying on 429. Returns the body that is now on
    record for this owner (the POST body itself: both 201 and 200 confirm
    the ledger holds exactly what we sent). Raises `SyncError` on rejection
    or any other unexpected response."""
    body = summary.to_dict()
    headers = {"Idempotency-Key": "sync-%s" % summary.owner}
    while True:
        response = client.post_json("/v1/summaries", body, headers=headers)
        if response.status == 429:
            sleep(_retry_after_seconds(response))
            continue
        if response.status in (200, 201):
            return body
        if response.status in _REJECTED_STATUSES:
            raise SyncError("POST /v1/summaries for owner %r was rejected: %d %s"
                             % (summary.owner, response.status, response.text))
        raise SyncError("POST /v1/summaries for owner %r: unexpected status %d"
                         % (summary.owner, response.status))


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _write_jsonl(path: str, records: List[Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record))
            handle.write("\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR, sleep=time.sleep) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)

    raw_items = _fetch_all_pages(directory, "/v1/items", "items", sleep=sleep)
    raw_accounts = _fetch_all_pages(ledger, "/v1/accounts", "accounts", sleep=sleep)
    raw_summaries = _fetch_all_pages(ledger, "/v1/summaries", "summaries", sleep=sleep)

    well_formed_items, malformed_items = _split_items(raw_items)
    well_formed_accounts, malformed_accounts = _split_accounts(raw_accounts)

    active_owners = _active_owners(well_formed_accounts)
    required = _required_summaries(well_formed_items, active_owners)
    orphans = _orphan_items(well_formed_items, active_owners)
    existing = _existing_summary_owners(raw_summaries)

    posted_or_existing: List[Dict[str, Any]] = []
    for owner, summary in required.items():
        if owner in existing:
            posted_or_existing.append(_normalize_summary(existing[owner], owner))
        else:
            posted_or_existing.append(_post_summary(ledger, summary, sleep=sleep))
            existing[owner] = posted_or_existing[-1]

    errors = ([{"service": "directory", "record": record} for record in malformed_items]
              + [{"service": "ledger", "record": record} for record in malformed_accounts])

    os.makedirs(out_dir, exist_ok=True)
    _write_jsonl(os.path.join(out_dir, "orphans.jsonl"), orphans)
    _write_jsonl(os.path.join(out_dir, "errors.jsonl"), errors)
    _write_jsonl(os.path.join(out_dir, "summary.jsonl"), posted_or_existing)

    return 0
