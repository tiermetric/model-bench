"""The `sync` command: reconcile the Directory and the Ledger.

Everything else in the package is in place — config loading, the HTTP client,
the record types. What lives here is the part that has to make decisions, and
it is not written yet.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from .config import Config
from .http_client import HttpClient, HttpClientError
from .models import Account, Item, Summary

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"


def _validate_account(record: Any) -> Optional[Account]:
    """Validate and parse a record as an Account. Return None if malformed."""
    if not isinstance(record, dict):
        return None
    if not isinstance(record.get("id"), str):
        return None
    if not isinstance(record.get("owner"), str):
        return None
    if not isinstance(record.get("active"), bool):
        return None
    return Account(id=record["id"], owner=record["owner"], active=record["active"])


def _validate_item(record: Any) -> Optional[Item]:
    """Validate and parse a record as an Item. Return None if malformed."""
    if not isinstance(record, dict):
        return None
    if not isinstance(record.get("id"), str):
        return None
    if not isinstance(record.get("owner"), str):
        return None
    if not isinstance(record.get("status"), str):
        return None
    if not isinstance(record.get("points"), int) or record.get("points") < 0:
        return None
    return Item(id=record["id"], owner=record["owner"],
                status=record["status"], points=record["points"])


def _fetch_paginated(client: HttpClient, endpoint: str) -> Tuple[List[Any], List[Any]]:
    """Fetch all pages from an endpoint. Return (valid_records, malformed_records).

    Handles 429 rate limiting by waiting for Retry-After.
    """
    valid = []
    malformed = []
    page = 1

    while True:
        while True:
            resp = client.get(endpoint, params={"page": page})
            if resp.status == 429:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    wait_seconds = float(retry_after)
                    time.sleep(wait_seconds)
                    continue
                else:
                    time.sleep(1)
                    continue
            break

        if resp.status != 200:
            raise HttpClientError("Failed to fetch %s page %d: %d" % (endpoint, page, resp.status))

        try:
            data = resp.json()
        except ValueError as exc:
            raise HttpClientError("Invalid JSON from %s: %s" % (endpoint, exc))

        if not isinstance(data, dict):
            raise HttpClientError("Expected JSON object from %s" % endpoint)

        records = data.get("items") or data.get("accounts") or data.get("summaries")
        if records is None:
            raise HttpClientError("Missing data field in %s" % endpoint)

        if not isinstance(records, list):
            raise HttpClientError("Expected list for records in %s" % endpoint)

        for record in records:
            valid.append(record)

        next_page = data.get("next_page")
        if next_page is None:
            break
        page = next_page

    return valid, malformed


def _fetch_accounts(client: HttpClient) -> Tuple[List[Account], List[Dict[str, Any]]]:
    """Fetch all accounts. Return (valid_accounts, malformed_records)."""
    raw_accounts, _ = _fetch_paginated(client, "/v1/accounts")
    valid_accounts = []
    malformed_records = []

    for record in raw_accounts:
        account = _validate_account(record)
        if account is None:
            malformed_records.append(record)
        else:
            valid_accounts.append(account)

    return valid_accounts, malformed_records


def _fetch_items(client: HttpClient) -> Tuple[List[Item], List[Dict[str, Any]]]:
    """Fetch all items. Return (valid_items, malformed_records)."""
    raw_items, _ = _fetch_paginated(client, "/v1/items")
    valid_items = []
    malformed_records = []
    seen_ids = set()

    for record in raw_items:
        item = _validate_item(record)
        if item is None:
            malformed_records.append(record)
        elif item.id not in seen_ids:
            valid_items.append(item)
            seen_ids.add(item.id)

    return valid_items, malformed_records


def _fetch_summaries(client: HttpClient) -> List[Summary]:
    """Fetch all existing summaries."""
    raw_summaries, _ = _fetch_paginated(client, "/v1/summaries")
    summaries = []

    for record in raw_summaries:
        if not isinstance(record, dict):
            continue
        if not isinstance(record.get("owner"), str):
            continue
        if not isinstance(record.get("open_items"), int):
            continue
        if not isinstance(record.get("done_items"), int):
            continue
        if not isinstance(record.get("total_points"), int):
            continue

        summaries.append(Summary(
            owner=record["owner"],
            open_items=record["open_items"],
            done_items=record["done_items"],
            total_points=record["total_points"]
        ))

    return summaries


def _post_summary(ledger_client: HttpClient, summary: Summary) -> bool:
    """Post a summary to the ledger. Return True if posted successfully."""
    idempotency_key = "sync-" + summary.owner
    headers = {"Idempotency-Key": idempotency_key}
    payload = summary.to_dict()

    while True:
        resp = ledger_client.post_json("/v1/summaries", payload, headers=headers)
        if resp.status == 429:
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                wait_seconds = float(retry_after)
                time.sleep(wait_seconds)
                continue
            else:
                time.sleep(1)
                continue

        if resp.status in (201, 200):
            return True
        if resp.status in (400, 409, 422):
            return False

        raise HttpClientError("Unexpected status %d posting summary for %s" % (resp.status, summary.owner))


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    try:
        directory_client = HttpClient(config.directory_url)
        ledger_client = HttpClient(config.ledger_url)

        accounts, account_errors = _fetch_accounts(ledger_client)
        items, item_errors = _fetch_items(directory_client)
        existing_summaries = _fetch_summaries(ledger_client)

        all_errors = []
        for record in account_errors:
            all_errors.append({"service": "ledger", "record": record})
        for record in item_errors:
            all_errors.append({"service": "directory", "record": record})

        active_owners: Set[str] = set()
        for account in accounts:
            if account.active:
                active_owners.add(account.owner)

        items_by_owner: Dict[str, List[Item]] = {}
        orphan_items = []
        seen_item_ids: Set[str] = set()

        for item in items:
            if item.id not in seen_item_ids:
                if item.owner in active_owners:
                    if item.owner not in items_by_owner:
                        items_by_owner[item.owner] = []
                    items_by_owner[item.owner].append(item)
                else:
                    orphan_items.append(item)
                seen_item_ids.add(item.id)

        summaries_to_post = []
        existing_owners: Set[str] = set()
        for summary in existing_summaries:
            existing_owners.add(summary.owner)

        for owner in active_owners:
            if owner not in existing_owners:
                owner_items = items_by_owner.get(owner, [])
                open_count = sum(1 for item in owner_items if item.status == "open")
                done_count = sum(1 for item in owner_items if item.status == "done")
                total_points = sum(item.points for item in owner_items)

                summaries_to_post.append(Summary(
                    owner=owner,
                    open_items=open_count,
                    done_items=done_count,
                    total_points=total_points
                ))

        os.makedirs(out_dir, exist_ok=True)

        posted_summaries = []
        for summary in summaries_to_post:
            if _post_summary(ledger_client, summary):
                posted_summaries.append(summary)

        errors_file = os.path.join(out_dir, "errors.jsonl")
        with open(errors_file, "w", encoding="utf-8") as f:
            for error in all_errors:
                f.write(json.dumps(error) + "\n")

        orphans_file = os.path.join(out_dir, "orphans.jsonl")
        with open(orphans_file, "w", encoding="utf-8") as f:
            for orphan in orphan_items:
                f.write(json.dumps(orphan.to_dict()) + "\n")

        summaries_file = os.path.join(out_dir, "summary.jsonl")
        with open(summaries_file, "w", encoding="utf-8") as f:
            for summary in posted_summaries:
                f.write(json.dumps(summary.to_dict()) + "\n")

        return 0

    except (HttpClientError, OSError) as exc:
        print("Error during sync: %s" % exc, file=sys.stderr)
        return 1
