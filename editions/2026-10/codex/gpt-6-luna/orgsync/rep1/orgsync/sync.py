"""Reconcile Directory work items with Ledger accounts."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from .config import Config
from .http_client import HttpClient

DEFAULT_OUT_DIR = "out"


def _request(client: HttpClient, method: str, path: str, **kwargs: Any):
    """Retry a rate-limited request only after the service's requested delay."""
    while True:
        response = client.request(method, path, **kwargs)
        if response.status != 429:
            return response
        try:
            delay = max(0.0, float(response.headers.get("Retry-After", "0")))
        except (TypeError, ValueError):
            delay = 0.0
        time.sleep(delay)


def _pages(client: HttpClient, endpoint: str, key: str) -> List[Any]:
    records: List[Any] = []
    page: Optional[int] = 1
    seen_pages: Set[int] = set()
    while page is not None:
        if page in seen_pages:
            raise RuntimeError("pagination loop at %s page %s" % (endpoint, page))
        seen_pages.add(page)
        response = _request(client, "GET", endpoint, params={"page": page})
        if response.status != 200:
            raise RuntimeError("GET %s returned HTTP %s" % (endpoint, response.status))
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
            raise RuntimeError("invalid page response from %s" % endpoint)
        records.extend(payload[key])
        nxt = payload.get("next_page")
        if nxt is not None and (type(nxt) is not int or nxt < 1):
            raise RuntimeError("invalid next_page from %s" % endpoint)
        page = nxt
    return records


def _account(record: Any) -> bool:
    return (isinstance(record, dict) and isinstance(record.get("id"), str)
            and isinstance(record.get("owner"), str)
            and type(record.get("active")) is bool)


def _item(record: Any) -> bool:
    return (isinstance(record, dict) and isinstance(record.get("id"), str)
            and isinstance(record.get("owner"), str)
            and record.get("status") in ("open", "done")
            and isinstance(record.get("status"), str)
            and type(record.get("points")) is int and record["points"] >= 0)


def _json_line(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_lines(path: str, lines: Set[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for line in sorted(lines):
            handle.write(line + "\n")


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)

    account_records = _pages(ledger, "/v1/accounts", "accounts")
    item_records = _pages(directory, "/v1/items", "items")
    summary_records = _pages(ledger, "/v1/summaries", "summaries")

    errors: Set[str] = set()
    active_owners: Set[str] = set()
    for record in account_records:
        if not _account(record):
            errors.add(_json_line({"service": "ledger", "record": record}))
        elif record["active"]:
            active_owners.add(record["owner"])

    # A repeated delivery of an item id is the same item, regardless of page.
    seen_ids: Set[str] = set()
    valid_items: List[Dict[str, Any]] = []
    for record in item_records:
        if not _item(record):
            errors.add(_json_line({"service": "directory", "record": record}))
            continue
        if record["id"] in seen_ids:
            continue
        seen_ids.add(record["id"])
        valid_items.append(record)

    orphans: Set[str] = set()
    totals: Dict[str, Dict[str, int]] = {
        owner: {"open_items": 0, "done_items": 0, "total_points": 0}
        for owner in active_owners
    }
    for item in valid_items:
        if item["owner"] not in active_owners:
            orphans.add(_json_line(item))
            continue
        total = totals[item["owner"]]
        total["%s_items" % item["status"]] += 1
        total["total_points"] += item["points"]

    existing: Set[str] = set()
    for record in summary_records:
        if isinstance(record, dict) and isinstance(record.get("owner"), str):
            existing.add(record["owner"])

    os.makedirs(out_dir, exist_ok=True)
    summary_path = os.path.join(out_dir, "summary.jsonl")
    # This file is an append-only record of successful posts, so the second
    # identical sync leaves it unchanged while a fresh run starts empty.
    posted: Set[str] = set()
    try:
        with open(summary_path, "r", encoding="utf-8") as handle:
            posted = {line.rstrip("\r\n") for line in handle if line.strip()}
    except FileNotFoundError:
        pass

    for owner in sorted(active_owners - existing):
        body = {"owner": owner, **totals[owner]}
        response = _request(ledger, "POST", "/v1/summaries", payload=body,
                            headers={"Idempotency-Key": "sync-" + owner})
        if response.status not in (200, 201):
            raise RuntimeError("POST summary for %r returned HTTP %s" % (owner, response.status))
        posted.add(_json_line(body))

    _write_lines(os.path.join(out_dir, "orphans.jsonl"), orphans)
    _write_lines(os.path.join(out_dir, "errors.jsonl"), errors)
    _write_lines(summary_path, posted)
    return 0
