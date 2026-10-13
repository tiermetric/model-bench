"""The `sync` command: reconcile the Directory and the Ledger.

Reads every page of accounts, items, and posted summaries; computes one
summary per active, well-formed account; writes orphans, malformed records,
and posted summaries to ``out/``; and POSTs the summaries the ledger is
still missing. Only the Python standard library is used.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from .config import Config
from .http_client import HttpClient

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

ITEMS_PATH = "/v1/items"
ACCOUNTS_PATH = "/v1/accounts"
SUMMARIES_PATH = "/v1/summaries"

#: Small extra wait added on top of a `Retry-After` delay so the next request
#: to that service goes out at least `Retry-After` seconds after the 429.
RETRY_BUFFER_SECONDS = 0.05

#: Fallback wait when a 429 carries no (parseable) `Retry-After` header.
DEFAULT_RETRY_AFTER_SECONDS = 1.0


def is_well_formed_item(record: Any) -> bool:
    """True when `record` is a usable Directory work item.

    Requires a JSON object with `id`/`owner` strings, `status` of "open" or
    "done", and `points` a JSON integer >= 0 (a bool is not an integer).
    Extra fields are ignored.
    """
    if not isinstance(record, dict):
        return False
    if not isinstance(record.get("id"), str):
        return False
    if not isinstance(record.get("owner"), str):
        return False
    if record.get("status") not in ("open", "done"):
        return False
    points = record.get("points")
    if type(points) is not int:
        return False
    return points >= 0


def is_well_formed_account(record: Any) -> bool:
    """True when `record` is a usable Ledger account.

    Requires a JSON object with `id`/`owner` strings and `active` a JSON
    boolean. Extra fields are ignored.
    """
    if not isinstance(record, dict):
        return False
    if not isinstance(record.get("id"), str):
        return False
    if not isinstance(record.get("owner"), str):
        return False
    return type(record.get("active")) is bool


def _canonical(record: Any) -> str:
    """A stable string for deduplicating records with identical content."""
    try:
        return json.dumps(record, sort_keys=True,
                          separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError):
        return repr(record)


class _Service:
    """One service client plus its rate-limit cooldown state."""

    def __init__(self, client: HttpClient) -> None:
        self.client = client
        # Monotonic deadline before which this service gets no new request.
        self.wait_until = 0.0


def _retry_after_seconds(headers: Any) -> float:
    """Parse `Retry-After` (seconds) from response headers, with a fallback."""
    value: Optional[Any] = None
    if headers is not None:
        try:
            value = headers.get("Retry-After")
        except (AttributeError, TypeError):
            value = None
        if value is None:
            try:
                value = headers.get("retry-after")
            except (AttributeError, TypeError):
                value = None
    if value is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    try:
        return max(0.0, float(str(value).strip()))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS


def _wait_for_cooldown(service: _Service) -> None:
    delay = service.wait_until - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def _note_rate_limited(service: _Service, headers: Any) -> None:
    service.wait_until = (time.monotonic()
                          + _retry_after_seconds(headers)
                          + RETRY_BUFFER_SECONDS)


def _get(service: _Service, path: str, params: Dict[str, Any]):
    """GET, retrying the same request after every 429 until answered."""
    while True:
        _wait_for_cooldown(service)
        response = service.client.get(path, params)
        if response.status == 429:
            _note_rate_limited(service, response.headers)
            continue
        return response


def _post_json(service: _Service, path: str, payload: Any,
               headers: Dict[str, str]):
    """POST, retrying the same request after every 429 until answered."""
    while True:
        _wait_for_cooldown(service)
        response = service.client.post_json(path, payload, headers)
        if response.status == 429:
            _note_rate_limited(service, response.headers)
            continue
        return response


def _fetch_all(service: _Service, path: str, key: str) -> List[Any]:
    """GET every page of a listing, following `next_page` until null."""
    records: List[Any] = []
    page: Optional[int] = 1
    visited = set()
    while page is not None:
        if page in visited:
            break
        visited.add(page)
        response = _get(service, path, {"page": page})
        if response.status != 200:
            raise RuntimeError("GET %s?page=%s returned status %s: %s"
                               % (path, page, response.status, response.text))
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError("GET %s?page=%s returned invalid JSON"
                               % (path, page)) from exc
        if not isinstance(body, dict):
            raise RuntimeError("GET %s?page=%s returned a non-object body"
                               % (path, page))
        batch = body.get(key, [])
        if isinstance(batch, list):
            records.extend(batch)
        nxt = body.get("next_page")
        if isinstance(nxt, bool) or not isinstance(nxt, int):
            page = None
        else:
            page = nxt
    return records


def _write_lines(path: str, lines: List[str]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    directory = _Service(HttpClient(config.directory_url))
    ledger = _Service(HttpClient(config.ledger_url))

    account_records = _fetch_all(ledger, ACCOUNTS_PATH, "accounts")
    item_records = _fetch_all(directory, ITEMS_PATH, "items")
    summary_records = _fetch_all(ledger, SUMMARIES_PATH, "summaries")

    errors: List[Dict[str, Any]] = []
    seen_errors = set()

    def add_error(service: str, record: Any) -> None:
        marker = (service, _canonical(record))
        if marker in seen_errors:
            return
        seen_errors.add(marker)
        errors.append({"service": service, "record": record})

    # The ledger decides who counts. First well-formed account wins per owner
    # (owners are unique among well-formed accounts); malformed accounts are
    # reported and confer nothing.
    owner_active: Dict[str, bool] = {}
    for record in account_records:
        if is_well_formed_account(record):
            if record["owner"] not in owner_active:
                owner_active[record["owner"]] = record["active"]
        else:
            add_error("ledger", record)
    active_owners = {owner for owner, on in owner_active.items() if on}

    # Items deduplicate by id: a repeated id is the same item delivered twice.
    # Records without a string id cannot be well-formed; dedupe those by
    # content through add_error.
    seen_ids = set()
    items_by_id: Dict[str, Any] = {}
    for record in item_records:
        if isinstance(record, dict) and isinstance(record.get("id"), str):
            item_id = record["id"]
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            if is_well_formed_item(record):
                items_by_id[item_id] = record
            else:
                add_error("directory", record)
        else:
            add_error("directory", record)

    open_counts: Dict[str, int] = {owner: 0 for owner in active_owners}
    done_counts: Dict[str, int] = {owner: 0 for owner in active_owners}
    point_totals: Dict[str, int] = {owner: 0 for owner in active_owners}
    orphans: List[Any] = []
    for record in items_by_id.values():
        owner = record["owner"]
        if owner in active_owners:
            if record["status"] == "open":
                open_counts[owner] += 1
            else:
                done_counts[owner] += 1
            point_totals[owner] += record["points"]
        else:
            orphans.append(record)

    # Only POST what the ledger is missing: an owner with any summary already
    # posted is left alone, which also keeps every write accepted (no 409 from
    # reusing an Idempotency-Key with a different body, no 422 for an owner
    # the ledger does not count as active).
    have_summary = set()
    for record in summary_records:
        if isinstance(record, dict) and isinstance(record.get("owner"), str):
            have_summary.add(record["owner"])

    posted: List[Dict[str, Any]] = []
    for owner in sorted(active_owners):
        if owner in have_summary:
            continue
        body = {"owner": owner,
                "open_items": open_counts[owner],
                "done_items": done_counts[owner],
                "total_points": point_totals[owner]}
        response = _post_json(ledger, SUMMARIES_PATH, body,
                              {"Idempotency-Key": "sync-" + owner})
        if response.status in (200, 201):
            posted.append(body)
        elif response.status in (400, 409, 422):
            raise RuntimeError(
                "POST summary for %r rejected with status %s: %s"
                % (owner, response.status, response.text))
        else:
            raise RuntimeError(
                "POST summary for %r returned status %s: %s"
                % (owner, response.status, response.text))

    os.makedirs(out_dir, exist_ok=True)
    _write_lines(os.path.join(out_dir, "orphans.jsonl"),
                 sorted(json.dumps(record) for record in orphans))
    _write_lines(os.path.join(out_dir, "errors.jsonl"),
                 sorted(json.dumps(entry) for entry in errors))

    # summary.jsonl records what this tool posted. Merging with entries already
    # in the file (from an earlier run) keeps a repeat run with unchanged
    # services byte-identical while issuing no new write requests.
    merged = set()
    summary_path = os.path.join(out_dir, "summary.jsonl")
    if os.path.exists(summary_path):
        with open(summary_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    merged.add(json.dumps(json.loads(line)))
                except ValueError:
                    continue
    for body in posted:
        merged.add(json.dumps(body))
    _write_lines(summary_path, sorted(merged))

    return 0
