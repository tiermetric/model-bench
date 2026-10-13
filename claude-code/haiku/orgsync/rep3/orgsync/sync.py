"""The `sync` command: reconcile the Directory and the Ledger.

Everything else in the package is in place — config loading, the HTTP client,
the record types. What lives here is the part that has to make decisions, and
it is not written yet.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from .config import Config
from .http_client import HttpClient, HttpClientError
from .models import Account, Item, Summary

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

ORPHANS_FILE = "orphans.jsonl"
ERRORS_FILE = "errors.jsonl"
SUMMARIES_FILE = "summary.jsonl"


def _is_well_formed_account(record: Any) -> bool:
    """Check if a record is a well-formed account."""
    if not isinstance(record, dict):
        return False
    return (isinstance(record.get("id"), str) and
            isinstance(record.get("owner"), str) and
            isinstance(record.get("active"), bool))


def _is_well_formed_item(record: Any) -> bool:
    """Check if a record is a well-formed item."""
    if not isinstance(record, dict):
        return False
    status = record.get("status")
    points = record.get("points")
    return (isinstance(record.get("id"), str) and
            isinstance(record.get("owner"), str) and
            status in ("open", "done") and
            isinstance(points, int) and points >= 0)


def _fetch_paginated(client: HttpClient, path: str) -> Tuple[List[Any], List[Dict[str, Any]]]:
    """Fetch all pages from a paginated endpoint.

    Returns (list of records, list of error dicts for malformed records).
    """
    records = []
    errors = []
    page = 1

    while True:
        resp = client.get(path, params={"page": page})

        if resp.status == 429:
            retry_after = resp.headers.get("Retry-After")
            wait_time = int(retry_after) if retry_after else 1
            time.sleep(wait_time)
            continue

        if resp.status != 200:
            raise HttpClientError(f"Failed to fetch {path}: {resp.status}")

        try:
            data = resp.json()
        except ValueError as e:
            raise HttpClientError(f"Invalid JSON from {path}: {e}") from e

        if not isinstance(data, dict):
            raise HttpClientError(f"Expected object response from {path}")

        page_records = data.get("items") if "items" in data else data.get("accounts") if "accounts" in data else data.get("summaries")
        if not isinstance(page_records, list):
            raise HttpClientError(f"Expected list in response from {path}")

        records.extend(page_records)

        next_page = data.get("next_page")
        if next_page is None:
            break
        page = next_page

    return records, errors


def _fetch_accounts(client: HttpClient) -> Tuple[List[Account], List[Dict[str, Any]]]:
    """Fetch all accounts. Returns (accounts, errors)."""
    records, errors = _fetch_paginated(client, "/v1/accounts")
    accounts = []

    for record in records:
        if _is_well_formed_account(record):
            accounts.append(Account(
                id=record["id"],
                owner=record["owner"],
                active=record["active"]
            ))
        else:
            errors.append({"service": "ledger", "record": record})

    return accounts, errors


def _fetch_items(client: HttpClient) -> Tuple[List[Item], List[Dict[str, Any]]]:
    """Fetch all items. Returns (items, errors)."""
    records, errors = _fetch_paginated(client, "/v1/items")
    items = []

    for record in records:
        if _is_well_formed_item(record):
            items.append(Item(
                id=record["id"],
                owner=record["owner"],
                status=record["status"],
                points=record["points"]
            ))
        else:
            errors.append({"service": "directory", "record": record})

    return items, errors


def _fetch_summaries(client: HttpClient) -> List[Summary]:
    """Fetch all existing summaries."""
    records, _ = _fetch_paginated(client, "/v1/summaries")
    summaries = []

    for record in records:
        if (isinstance(record, dict) and
            isinstance(record.get("owner"), str) and
            isinstance(record.get("open_items"), int) and
            isinstance(record.get("done_items"), int) and
            isinstance(record.get("total_points"), int)):
            summaries.append(Summary(
                owner=record["owner"],
                open_items=record["open_items"],
                done_items=record["done_items"],
                total_points=record["total_points"]
            ))

    return summaries


def _compute_summaries(items: List[Item], active_owners: Set[str]) -> Dict[str, Summary]:
    """Compute summaries for all active owners."""
    summaries: Dict[str, Summary] = {}

    for owner in active_owners:
        summaries[owner] = Summary(owner=owner, open_items=0, done_items=0, total_points=0)

    seen_item_ids: Set[str] = set()

    for item in items:
        if item.owner not in active_owners or item.id in seen_item_ids:
            continue

        seen_item_ids.add(item.id)
        summary = summaries[item.owner]

        if item.status == "open":
            summary = Summary(
                owner=summary.owner,
                open_items=summary.open_items + 1,
                done_items=summary.done_items,
                total_points=summary.total_points + item.points
            )
        elif item.status == "done":
            summary = Summary(
                owner=summary.owner,
                open_items=summary.open_items,
                done_items=summary.done_items + 1,
                total_points=summary.total_points + item.points
            )

        summaries[item.owner] = summary

    return summaries


def _write_jsonl_file(path: str, records: List[Any]) -> None:
    """Write records as JSON lines."""
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


def _post_summary_with_retry(ledger_client: HttpClient, summary: Summary) -> bool:
    """Post a summary, handling rate limits. Returns True if successful."""
    key = f"sync-{summary.owner}"
    payload = summary.to_dict()

    while True:
        resp = ledger_client.post_json(
            "/v1/summaries",
            payload,
            headers={"Idempotency-Key": key}
        )

        if resp.status == 429:
            retry_after = resp.headers.get("Retry-After")
            wait_time = int(retry_after) if retry_after else 1
            time.sleep(wait_time)
            continue

        if resp.status in (201, 200):
            return True

        if resp.status in (400, 409, 422):
            return False

        raise HttpClientError(f"Unexpected status {resp.status} posting summary for {summary.owner}")


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    if not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    dir_client = HttpClient(config.directory_url)
    ledger_client = HttpClient(config.ledger_url)

    try:
        items, dir_errors = _fetch_items(dir_client)
        accounts, ledger_errors = _fetch_accounts(ledger_client)
        existing_summaries = _fetch_summaries(ledger_client)
    except HttpClientError as e:
        print(f"Error fetching data: {e}", file=__import__("sys").stderr)
        return 1

    all_errors = dir_errors + ledger_errors

    active_owners = {acc.owner for acc in accounts if acc.active}

    seen_item_ids: Set[str] = set()
    deduplicated_items = []
    orphans = []

    for item in items:
        if item.id not in seen_item_ids:
            deduplicated_items.append(item)
            seen_item_ids.add(item.id)

            if item.owner not in active_owners:
                orphans.append(item.to_dict())

    computed = _compute_summaries(deduplicated_items, active_owners)

    existing_owners = {s.owner for s in existing_summaries}
    posted_summaries = []

    for owner in sorted(active_owners):
        if owner not in existing_owners:
            summary = computed[owner]
            if _post_summary_with_retry(ledger_client, summary):
                posted_summaries.append(summary.to_dict())

    orphans_path = os.path.join(out_dir, ORPHANS_FILE)
    errors_path = os.path.join(out_dir, ERRORS_FILE)
    summaries_path = os.path.join(out_dir, SUMMARIES_FILE)

    _write_jsonl_file(orphans_path, orphans)
    _write_jsonl_file(errors_path, all_errors)
    _write_jsonl_file(summaries_path, posted_summaries)

    return 0
