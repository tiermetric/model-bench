"""Reconcile Directory items into Ledger summaries."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, List, Set, Tuple

from .config import Config
from .http_client import HttpClient

DEFAULT_OUT_DIR = "out"


def _retry_request(client: HttpClient, method: str, path: str, **kwargs: Any):
    """Issue a request, honoring this service's Retry-After interval."""
    while True:
        response = client.request(method, path, **kwargs)
        if response.status != 429:
            return response
        raw = response.headers.get("Retry-After", "0")
        try:
            delay = max(0.0, float(raw))
        except (TypeError, ValueError):
            delay = 0.0
        time.sleep(delay)


def _read_pages(client: HttpClient, path: str, key: str) -> List[Any]:
    result: List[Any] = []
    page = 1
    seen_pages: Set[int] = set()
    while page is not None:
        if page in seen_pages:
            raise RuntimeError("pagination loop at %s page %s" % (path, page))
        seen_pages.add(page)
        response = _retry_request(client, "GET", path, params={"page": page})
        if response.status != 200:
            raise RuntimeError("GET %s failed with HTTP %s" % (path, response.status))
        data = response.json()
        records = data.get(key) if isinstance(data, dict) else None
        if not isinstance(records, list):
            raise RuntimeError("invalid %s response" % path)
        result.extend(records)
        next_page = data.get("next_page")
        if next_page is not None and (type(next_page) is not int or next_page < 1):
            raise RuntimeError("invalid next_page from %s" % path)
        page = next_page
    return result


def _well_formed_account(record: Any) -> bool:
    return (isinstance(record, dict) and isinstance(record.get("id"), str)
            and isinstance(record.get("owner"), str) and type(record.get("active")) is bool)


def _well_formed_item(record: Any) -> bool:
    return (isinstance(record, dict) and isinstance(record.get("id"), str)
            and isinstance(record.get("owner"), str)
            and record.get("status") in ("open", "done")
            and type(record.get("points")) is int and record["points"] >= 0)


def _line_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _read_existing(path: str) -> List[Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    except FileNotFoundError:
        return []


def _write_lines(path: str, entries: Iterable[Any]) -> None:
    unique = {_line_key(entry): entry for entry in entries}
    with open(path, "w", encoding="utf-8") as handle:
        for key in sorted(unique):
            handle.write(key + "\n")


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    os.makedirs(out_dir, exist_ok=True)
    errors_path = os.path.join(out_dir, "errors.jsonl")
    orphans_path = os.path.join(out_dir, "orphans.jsonl")
    summaries_path = os.path.join(out_dir, "summary.jsonl")
    errors = _read_existing(errors_path)
    orphans = _read_existing(orphans_path)
    posted = _read_existing(summaries_path)
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)

    # Read all source data before deciding what can safely be posted.
    accounts_raw = _read_pages(ledger, "/v1/accounts", "accounts")
    items_raw = _read_pages(directory, "/v1/items", "items")
    summaries_raw = _read_pages(ledger, "/v1/summaries", "summaries")

    accounts: Dict[str, bool] = {}
    for record in accounts_raw:
        if not _well_formed_account(record):
            errors.append({"service": "ledger", "record": record})
            continue
        # Owner uniqueness is guaranteed by the service among valid accounts.
        accounts[record["owner"]] = record["active"]
    active = {owner for owner, enabled in accounts.items() if enabled}

    item_by_id: Dict[str, Any] = {}
    for record in items_raw:
        if not _well_formed_item(record):
            errors.append({"service": "directory", "record": record})
            continue
        item_by_id.setdefault(record["id"], record)

    required: Dict[str, Dict[str, Any]] = {
        owner: {"owner": owner, "open_items": 0, "done_items": 0, "total_points": 0}
        for owner in active
    }
    for item in item_by_id.values():
        if item["owner"] not in active:
            orphans.append(item)
            continue
        summary = required[item["owner"]]
        summary["%s_items" % item["status"]] += 1
        summary["total_points"] += item["points"]

    existing_owners = set()
    for summary in summaries_raw:
        if isinstance(summary, dict) and isinstance(summary.get("owner"), str):
            existing_owners.add(summary["owner"])

    # Only POST when the owner is absent from the ledger's summaries. In
    # particular, a 409/422 is treated as a failed run rather than ignored.
    for owner in sorted(required):
        if owner in existing_owners:
            continue
        body = required[owner]
        response = _retry_request(ledger, "POST", "/v1/summaries", payload=body,
                                  headers={"Idempotency-Key": "sync-" + owner})
        if response.status not in (200, 201):
            raise RuntimeError("POST summary for %s failed with HTTP %s" % (owner, response.status))
        posted.append(body)

    _write_lines(errors_path, errors)
    _write_lines(orphans_path, orphans)
    _write_lines(summaries_path, posted)
    return 0
