"""The `sync` command: reconcile the Directory and the Ledger.

Everything else in the package is in place — config loading, the HTTP client,
the record types. What lives here is the part that has to make decisions, and
it is not written yet.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Set

from .config import Config
from .http_client import HttpClient, Response
from .models import Account, Item, Summary

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"


def _is_well_formed_account(record: Any) -> bool:
    """Check if a record is a well-formed account."""
    if not isinstance(record, dict):
        return False
    if not isinstance(record.get("id"), str):
        return False
    if not isinstance(record.get("owner"), str):
        return False
    if not isinstance(record.get("active"), bool):
        return False
    return True


def _is_well_formed_item(record: Any) -> bool:
    """Check if a record is a well-formed item."""
    if not isinstance(record, dict):
        return False
    if not isinstance(record.get("id"), str):
        return False
    if not isinstance(record.get("owner"), str):
        return False
    if not isinstance(record.get("status"), str):
        return False
    if not isinstance(record.get("points"), int):
        return False
    points = record.get("points")
    if points < 0:
        return False
    return True


def _fetch_paginated(client: HttpClient, path: str) -> tuple[List[Any], List[Any]]:
    """Fetch all pages from an endpoint, respecting rate limits.

    Returns (records, errors) where records are the valid JSON objects
    received and errors are malformed/unparseable records.
    """
    records = []
    errors = []
    page = 1
    service_name = "directory" if "directory" in client.base_url else "ledger"
    retry_after = 0

    while True:
        # Wait if rate-limited
        if retry_after > 0:
            time.sleep(retry_after)
            retry_after = 0

        response = client.get(path, {"page": page})

        # Handle rate limit
        if response.status == 429:
            retry_after_header = response.headers.get("Retry-After")
            if retry_after_header:
                retry_after = float(retry_after_header)
            else:
                retry_after = 1.0
            continue

        # Other errors are fatal
        if response.status != 200:
            raise RuntimeError(f"Failed to fetch {service_name} {path} page {page}: "
                             f"status {response.status}")

        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Invalid JSON from {service_name} {path} page {page}: {exc}")

        if not isinstance(data, dict):
            raise RuntimeError(f"Expected dict from {service_name} {path} page {page}, "
                             f"got {type(data).__name__}")

        # Extract records from the appropriate field
        if "items" in data:
            page_records = data.get("items", [])
        elif "accounts" in data:
            page_records = data.get("accounts", [])
        elif "summaries" in data:
            page_records = data.get("summaries", [])
        else:
            raise RuntimeError(f"No records field in {service_name} response at {path} page {page}")

        if not isinstance(page_records, list):
            raise RuntimeError(f"Expected list of records from {service_name} {path} page {page}")

        for record in page_records:
            records.append(record)

        next_page = data.get("next_page")
        if next_page is None:
            break

        page = next_page

    return records, errors


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    # Create output directory
    os.makedirs(out_dir, exist_ok=True)

    # Initialize HTTP clients
    directory_client = HttpClient(config.directory_url)
    ledger_client = HttpClient(config.ledger_url)

    # Fetch all data from both services
    accounts_raw, _ = _fetch_paginated(ledger_client, "/v1/accounts")
    items_raw, _ = _fetch_paginated(directory_client, "/v1/items")
    summaries_raw, _ = _fetch_paginated(ledger_client, "/v1/summaries")

    # Validate and build maps
    accounts: Dict[str, Account] = {}
    errors: List[Dict[str, Any]] = []

    for record in accounts_raw:
        if _is_well_formed_account(record):
            account = Account(id=record["id"], owner=record["owner"], active=record["active"])
            accounts[account.owner] = account
        else:
            errors.append({"service": "ledger", "record": record})

    # Build set of active owners
    active_owners: Set[str] = set()
    for account in accounts.values():
        if account.active:
            active_owners.add(account.owner)

    # Process items
    items_by_owner: Dict[str, List[Item]] = {}
    orphans: List[Dict[str, Any]] = []

    seen_item_ids: Set[str] = set()

    for record in items_raw:
        if _is_well_formed_item(record):
            item = Item(id=record["id"], owner=record["owner"],
                       status=record["status"], points=record["points"])

            # Skip if we've already seen this item ID (deduplication)
            if item.id in seen_item_ids:
                continue
            seen_item_ids.add(item.id)

            if item.owner in active_owners:
                if item.owner not in items_by_owner:
                    items_by_owner[item.owner] = []
                items_by_owner[item.owner].append(item)
            else:
                orphans.append(record)
        else:
            errors.append({"service": "directory", "record": record})

    # Compute summaries for all active owners
    computed_summaries: Dict[str, Summary] = {}
    for owner in active_owners:
        open_count = 0
        done_count = 0
        total_points = 0

        for item in items_by_owner.get(owner, []):
            if item.status == "open":
                open_count += 1
            elif item.status == "done":
                done_count += 1
            total_points += item.points

        computed_summaries[owner] = Summary(
            owner=owner,
            open_items=open_count,
            done_items=done_count,
            total_points=total_points
        )

    # Fetch existing summaries from ledger
    existing_summaries: Set[str] = set()
    for record in summaries_raw:
        if isinstance(record, dict) and isinstance(record.get("owner"), str):
            existing_summaries.add(record["owner"])

    # Write output files
    with open(os.path.join(out_dir, "errors.jsonl"), "w") as f:
        for error in errors:
            f.write(json.dumps(error) + "\n")

    with open(os.path.join(out_dir, "orphans.jsonl"), "w") as f:
        for orphan in orphans:
            f.write(json.dumps(orphan) + "\n")

    # Track per-service rate limits
    ledger_retry_after = 0

    # Post missing summaries
    posted_summaries: List[Dict[str, Any]] = []
    for owner, summary in computed_summaries.items():
        if owner not in existing_summaries:
            idempotency_key = f"sync-{owner}"
            payload = summary.to_dict()

            # Wait if rate-limited
            if ledger_retry_after > 0:
                time.sleep(ledger_retry_after)
                ledger_retry_after = 0

            while True:
                response = ledger_client.post_json(
                    "/v1/summaries",
                    payload,
                    {"Idempotency-Key": idempotency_key}
                )

                # Handle rate limit
                if response.status == 429:
                    retry_after_header = response.headers.get("Retry-After")
                    if retry_after_header:
                        ledger_retry_after = float(retry_after_header)
                    else:
                        ledger_retry_after = 1.0
                    time.sleep(ledger_retry_after)
                    ledger_retry_after = 0
                    continue

                # Handle rejection
                if response.status not in (200, 201):
                    raise RuntimeError(f"Failed to POST summary for {owner}: "
                                     f"status {response.status}, body: {response.text}")

                posted_summaries.append(payload)
                break

    with open(os.path.join(out_dir, "summary.jsonl"), "w") as f:
        for summary in posted_summaries:
            f.write(json.dumps(summary) + "\n")

    return 0
