"""Reconcile Directory items with Ledger accounts."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Set

from .config import Config
from .http_client import HttpClient

DEFAULT_OUT_DIR = "out"


def _json_line(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _pages(client: HttpClient, path: str, field: str) -> List[Any]:
    """Read every page, respecting a service-wide Retry-After delay."""
    page: Optional[int] = 1
    records: List[Any] = []
    while page is not None:
        while True:
            response = client.get(path, params={"page": page})
            if response.status == 429:
                try:
                    delay = max(0.0, float(response.headers.get("Retry-After", "0")))
                except (TypeError, ValueError):
                    delay = 0.0
                time.sleep(delay)
                continue
            if response.status != 200:
                raise RuntimeError("GET %s failed with HTTP %d" % (path, response.status))
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get(field), list):
                raise RuntimeError("invalid page response from %s" % path)
            records.extend(data[field])
            next_page = data.get("next_page")
            if next_page is not None and (type(next_page) is not int or next_page < 1):
                raise RuntimeError("invalid next_page from %s" % path)
            page = next_page
            break
    return records


def _post(client: HttpClient, owner: str, body: Dict[str, Any]) -> None:
    while True:
        response = client.post_json("/v1/summaries", body,
                                    headers={"Idempotency-Key": "sync-" + owner})
        if response.status == 429:
            try:
                delay = max(0.0, float(response.headers.get("Retry-After", "0")))
            except (TypeError, ValueError):
                delay = 0.0
            time.sleep(delay)
            continue
        if response.status not in (200, 201):
            raise RuntimeError("POST summary for %s failed with HTTP %d" %
                               (owner, response.status))
        return


def _write_lines(path: str, lines: Iterable[str]) -> None:
    # Preserve first-seen order while ensuring each output entry appears once.
    unique = list(dict.fromkeys(lines))
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        if unique:
            handle.write("\n".join(unique) + "\n")


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    os.makedirs(out_dir, exist_ok=True)
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)

    account_records = _pages(ledger, "/v1/accounts", "accounts")
    item_records = _pages(directory, "/v1/items", "items")
    existing_summaries = _pages(ledger, "/v1/summaries", "summaries")

    errors: List[str] = []
    active: Set[str] = set()
    seen_accounts: Set[str] = set()
    for record in account_records:
        valid = (isinstance(record, dict) and isinstance(record.get("id"), str)
                 and isinstance(record.get("owner"), str)
                 and type(record.get("active")) is bool)
        if not valid:
            errors.append(_json_line({"service": "ledger", "record": record}))
            continue
        owner = record["owner"]
        if owner in seen_accounts:
            continue
        seen_accounts.add(owner)
        if record["active"]:
            active.add(owner)

    counts: Dict[str, Dict[str, int]] = {
        owner: {"open_items": 0, "done_items": 0, "total_points": 0}
        for owner in active
    }
    orphans: List[str] = []
    seen_item_ids: Set[str] = set()
    seen_bad_items: Set[str] = set()
    for record in item_records:
        valid = (isinstance(record, dict) and isinstance(record.get("id"), str)
                 and isinstance(record.get("owner"), str)
                 and record.get("status") in ("open", "done")
                 and type(record.get("points")) is int and record["points"] >= 0)
        if not valid:
            line = _json_line({"service": "directory", "record": record})
            if line not in seen_bad_items:
                errors.append(line)
                seen_bad_items.add(line)
            continue
        item_id = record["id"]
        if item_id in seen_item_ids:
            continue
        seen_item_ids.add(item_id)
        owner = record["owner"]
        if owner not in active:
            orphans.append(_json_line(record))
            continue
        counts[owner]["%s_items" % record["status"]] += 1
        counts[owner]["total_points"] += record["points"]

    present: Set[str] = set()
    for summary in existing_summaries:
        if isinstance(summary, dict) and isinstance(summary.get("owner"), str):
            present.add(summary["owner"])

    post_lines: List[str] = []
    for owner in sorted(active):
        if owner in present:
            continue
        body = {"owner": owner, **counts[owner]}
        _post(ledger, owner, body)
        post_lines.append(_json_line(body))

    summary_path = os.path.join(out_dir, "summary.jsonl")
    # This audit file spans invocations: retain earlier accepted posts so a
    # no-op second sync leaves all output files unchanged.
    previous: List[str] = []
    try:
        with open(summary_path, "r", encoding="utf-8") as handle:
            previous = [line.rstrip("\n") for line in handle if line.strip()]
    except FileNotFoundError:
        pass
    _write_lines(os.path.join(out_dir, "errors.jsonl"), errors)
    _write_lines(os.path.join(out_dir, "orphans.jsonl"), orphans)
    _write_lines(summary_path, previous + post_lines)
    return 0
