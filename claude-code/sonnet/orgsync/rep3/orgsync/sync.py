"""The `sync` command: reconcile the Directory and the Ledger.

Everything else in the package is in place — config loading, the HTTP client,
the record types. What lives here is the part that has to make decisions:
which records are usable, who counts, and what to post.

`run_sync` is the public entry point wired to the CLI; it only builds the two
`HttpClient`s and hands off to `_sync`, which takes clients directly so tests
can inject fakes without a socket.
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

#: Fallback wait when a 429 arrives without a usable Retry-After header. The
#: two services always send one per the task contract; this only guards
#: against a malformed or missing header.
_FALLBACK_RETRY_SECONDS = 1.0

#: POST statuses that mean the ledger *rejected* the write outright, as
#: opposed to 429 (rate limit, not a rejection — retried) or 200/201 (success).
_REJECTED_POST_STATUSES = (400, 409, 422)


def _is_well_formed_account(record: Any) -> bool:
    """True when `record` is `{"id": str, "owner": str, "active": bool}`.

    `active` is checked with `type(...) is bool`, not `isinstance`, because
    `isinstance(1, int)` would otherwise let a JSON integer through — Python's
    `bool` is a subclass of `int`. Extra fields are ignored.
    """
    return (
        isinstance(record, dict)
        and isinstance(record.get("id"), str)
        and isinstance(record.get("owner"), str)
        and type(record.get("active")) is bool
    )


def _is_well_formed_item(record: Any) -> bool:
    """True when `record` is `{"id": str, "owner": str, "status": "open"|"done",
    "points": int >= 0}`. `points` uses `type(...) is int` for the same reason
    `_is_well_formed_account` avoids `isinstance` for booleans."""
    if not isinstance(record, dict):
        return False
    if not isinstance(record.get("id"), str) or not isinstance(record.get("owner"), str):
        return False
    if record.get("status") not in ("open", "done"):
        return False
    points = record.get("points")
    return type(points) is int and points >= 0


def _retry_seconds(response: Response) -> float:
    """Parse `Retry-After` from a 429, falling back if it is absent or junk."""
    raw = response.headers.get("Retry-After")
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return _FALLBACK_RETRY_SECONDS
    return max(seconds, 0.0)


def _get_with_retry(client: HttpClient, path: str, params: Dict[str, Any]) -> Response:
    """GET `path`, waiting out any 429s. Raises on any other non-200 status —
    the two services never answer a well-formed paginated GET any other way."""
    while True:
        response = client.get(path, params)
        if response.status == 429:
            time.sleep(_retry_seconds(response))
            continue
        if response.status != 200:
            raise RuntimeError(
                "GET %s failed: %d %s" % (path, response.status, response.text))
        return response


def _post_with_retry(client: HttpClient, path: str, payload: Any,
                     headers: Dict[str, str]) -> Response:
    """POST `payload`, waiting out any 429s and retrying the identical POST
    afterward. Any other status (2xx or a rejection) is returned as-is for the
    caller to interpret."""
    while True:
        response = client.post_json(path, payload, headers)
        if response.status == 429:
            time.sleep(_retry_seconds(response))
            continue
        return response


def _fetch_all(client: HttpClient, path: str, key: str) -> List[Any]:
    """Collect every record across every page of a `{key: [...], "next_page":
    ...}` endpoint, starting at page 1 and following `next_page` to `None`."""
    records: List[Any] = []
    page: Optional[int] = 1
    while page is not None:
        response = _get_with_retry(client, path, {"page": page})
        data = response.json()
        records.extend(data.get(key) or [])
        page = data.get("next_page")
    return records


def _write_jsonl(path: str, records: List[Any]) -> None:
    """Write `records` to `path`, one JSON value per line."""
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def _sync(directory: HttpClient, ledger: HttpClient, out_dir: str) -> int:
    """Do the reconciliation against already-constructed clients.

    Split from `run_sync` so tests can pass clients built on a fake opener
    instead of a real socket.
    """
    accounts_raw = _fetch_all(ledger, "/v1/accounts", "accounts")
    items_raw = _fetch_all(directory, "/v1/items", "items")
    summaries_raw = _fetch_all(ledger, "/v1/summaries", "summaries")

    errors: List[Dict[str, Any]] = []
    seen_errors = set()

    def add_error(service: str, record: Any) -> None:
        # Dedupe on exact content: a malformed record without a usable `id`
        # has no other identity to key on, and the task promises well-formed
        # ids are unique but says nothing about malformed ones.
        dedupe_key = (service, json.dumps(record, sort_keys=True))
        if dedupe_key in seen_errors:
            return
        seen_errors.add(dedupe_key)
        errors.append({"service": service, "record": record})

    active_owners = set()
    for record in accounts_raw:
        if _is_well_formed_account(record):
            if record["active"]:
                active_owners.add(record["owner"])
        else:
            add_error("ledger", record)

    # Keyed by id so a duplicate delivery of the same item is counted once.
    items_by_id: Dict[str, Any] = {}
    for record in items_raw:
        if _is_well_formed_item(record):
            items_by_id[record["id"]] = record
        else:
            add_error("directory", record)

    counts = {owner: {"open": 0, "done": 0, "points": 0} for owner in active_owners}
    orphans: List[Any] = []
    for item in items_by_id.values():
        owner = item["owner"]
        if owner not in active_owners:
            orphans.append(item)
            continue
        bucket = counts[owner]
        if item["status"] == "open":
            bucket["open"] += 1
        else:
            bucket["done"] += 1
        bucket["points"] += item["points"]

    required = {
        owner: Summary(owner, counts[owner]["open"], counts[owner]["done"],
                       counts[owner]["points"])
        for owner in active_owners
    }

    existing_owners = {
        record["owner"] for record in summaries_raw
        if isinstance(record, dict) and isinstance(record.get("owner"), str)
    }

    for owner in sorted(active_owners):
        if owner in existing_owners:
            continue
        body = required[owner].to_dict()
        headers = {"Idempotency-Key": "sync-%s" % owner}
        response = _post_with_retry(ledger, "/v1/summaries", body, headers)
        if response.status in _REJECTED_POST_STATUSES:
            raise RuntimeError(
                "ledger rejected summary for %r: %d %s"
                % (owner, response.status, response.text))
        if response.status not in (200, 201):
            raise RuntimeError(
                "unexpected status posting summary for %r: %d %s"
                % (owner, response.status, response.text))

    # The output reflects the target state (every required summary), not just
    # what this particular run happened to POST, so a second run against
    # unchanged services reproduces the same file even though it posts nothing.
    all_summaries = [required[owner].to_dict() for owner in sorted(active_owners)]

    os.makedirs(out_dir, exist_ok=True)
    _write_jsonl(os.path.join(out_dir, "orphans.jsonl"), orphans)
    _write_jsonl(os.path.join(out_dir, "errors.jsonl"), errors)
    _write_jsonl(os.path.join(out_dir, "summary.jsonl"), all_summaries)

    return 0


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)
    return _sync(directory, ledger, out_dir)
