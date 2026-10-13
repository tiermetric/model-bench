"""Implementation of the ``orgsync sync`` reconciliation command."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, List, Mapping, Set, Tuple

from .config import Config
from .http_client import HttpClient, Response
from .models import Summary

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"


def _is_int(value: Any) -> bool:
    """JSON booleans are not integers for this API, despite Python's subclassing."""
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_item(record: Any) -> bool:
    return (isinstance(record, dict) and isinstance(record.get("id"), str)
            and isinstance(record.get("owner"), str)
            and record.get("status") in ("open", "done")
            and _is_int(record.get("points")) and record["points"] >= 0)


def _valid_account(record: Any) -> bool:
    return (isinstance(record, dict) and isinstance(record.get("id"), str)
            and isinstance(record.get("owner"), str)
            and isinstance(record.get("active"), bool))


def _key(value: Any) -> str:
    """A stable, type-preserving key for de-duplicating JSON records."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _request_with_retry(client: HttpClient, method: str, path: str,
                        *, params: Mapping[str, Any] | None = None,
                        payload: Any = None,
                        headers: Mapping[str, str] | None = None) -> Response:
    """Make a request, retrying 429s only after the advertised delay."""
    while True:
        if method == "GET":
            response = client.get(path, params=params, headers=headers)
        else:
            response = client.post_json(path, payload, headers=headers)
        if response.status != 429:
            return response
        try:
            delay = float(response.headers.get("Retry-After", "0"))
        except (TypeError, ValueError):
            delay = 0.0
        time.sleep(max(0.0, delay))


def _read_pages(client: HttpClient, path: str, collection_name: str) -> List[Any]:
    """Read a normal paginated endpoint and return every delivered record."""
    page = 1
    records: List[Any] = []
    while True:
        response = _request_with_retry(client, "GET", path, params={"page": page})
        if response.status != 200:
            raise RuntimeError("GET %s returned HTTP %d" % (response.url, response.status))
        try:
            document = response.json()
            batch = document[collection_name]
            next_page = document["next_page"]
        except (TypeError, KeyError, ValueError) as exc:
            raise RuntimeError("invalid page from %s" % response.url) from exc
        if not isinstance(document, dict) or not isinstance(batch, list):
            raise RuntimeError("invalid page from %s" % response.url)
        records.extend(batch)
        if next_page is None:
            return records
        # The services specify an integer page number. Reject nonsensical page
        # links instead of looping indefinitely on a broken response.
        if not _is_int(next_page) or next_page < 1:
            raise RuntimeError("invalid next_page from %s" % response.url)
        page = next_page


def _read_json_lines(path: str) -> Tuple[List[Any], Set[str]]:
    """Read prior output, retaining only unique valid JSON lines.

    Output files are append-only records of observed events.  This makes a
    second unchanged run byte-for-byte stable while allowing new events to be
    added on a later run.
    """
    if not os.path.exists(path):
        return [], set()
    values: List[Any] = []
    seen: Set[str] = set()
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
                marker = _key(value)
            except (TypeError, ValueError):
                continue
            if marker not in seen:
                seen.add(marker)
                values.append(value)
    return values, seen


def _append_unique_json_lines(path: str, new_values: Iterable[Any]) -> None:
    old_values, seen = _read_json_lines(path)
    additions = []
    for value in new_values:
        marker = _key(value)
        if marker not in seen:
            seen.add(marker)
            additions.append(value)
    if not additions and os.path.exists(path):
        return
    with open(path, "w", encoding="utf-8") as handle:
        for value in old_values + additions:
            handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass and write its durable JSONL observations."""
    os.makedirs(out_dir, exist_ok=True)
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)

    # Read all sources before writing anything, so account validity and current
    # ledger state decide the entire reconciliation pass.
    accounts_raw = _read_pages(ledger, "/v1/accounts", "accounts")
    items_raw = _read_pages(directory, "/v1/items", "items")
    summaries_raw = _read_pages(ledger, "/v1/summaries", "summaries")

    errors: List[Dict[str, Any]] = []
    error_seen: Set[str] = set()

    def record_error(service: str, record: Any) -> None:
        value = {"service": service, "record": record}
        marker = _key(value)
        if marker not in error_seen:
            error_seen.add(marker)
            errors.append(value)

    active_owners: Set[str] = set()
    for account in accounts_raw:
        if not _valid_account(account):
            record_error("ledger", account)
        elif account["active"]:
            active_owners.add(account["owner"])

    # Item IDs identify deliveries of the same work item.  Keep the first
    # valid delivery; duplicate deliveries do not affect counts or orphans.
    items: Dict[str, Dict[str, Any]] = {}
    for item in items_raw:
        if not _valid_item(item):
            record_error("directory", item)
        elif item["id"] not in items:
            items[item["id"]] = item

    counts: Dict[str, Summary] = {owner: Summary(owner) for owner in active_owners}
    orphans: List[Dict[str, Any]] = []
    for item in items.values():
        owner = item["owner"]
        if owner not in active_owners:
            orphans.append(item)
            continue
        current = counts[owner]
        counts[owner] = Summary(
            owner=owner,
            open_items=current.open_items + (item["status"] == "open"),
            done_items=current.done_items + (item["status"] == "done"),
            total_points=current.total_points + item["points"],
        )

    # A summary response is only used to answer whether its owner is already
    # represented. Ledger-generated summaries have a string owner; malformed
    # summary shapes cannot safely suppress a needed post.
    existing_owners = {record["owner"] for record in summaries_raw
                       if isinstance(record, dict) and isinstance(record.get("owner"), str)}

    posted: List[Dict[str, Any]] = []
    for owner in sorted(active_owners):
        if owner in existing_owners:
            continue
        body = counts[owner].to_dict()
        response = _request_with_retry(
            ledger, "POST", "/v1/summaries", payload=body,
            headers={"Idempotency-Key": "sync-" + owner},
        )
        if response.status not in (200, 201):
            # The inputs and ledger state were read first, so a normal run
            # cannot reach a rejected POST. Surface an unexpected state change.
            raise RuntimeError("POST %s returned HTTP %d" % (response.url, response.status))
        posted.append(body)

    _append_unique_json_lines(os.path.join(out_dir, "errors.jsonl"), errors)
    _append_unique_json_lines(os.path.join(out_dir, "orphans.jsonl"), orphans)
    _append_unique_json_lines(os.path.join(out_dir, "summary.jsonl"), posted)
    return 0
