"""The `sync` command: reconcile the Directory and the Ledger.

Everything else in the package is in place — config loading, the HTTP client,
the record types. What lives here is the part that has to make decisions.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Set

from .config import Config
from .http_client import HttpClient, Response
from .models import Summary

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

#: Statuses that make an item well-formed, per the task contract.
_ITEM_STATUSES = ("open", "done")


def _dedup_key(record: Any) -> str:
    """A stable identity for a record delivered possibly more than once.

    The service contract guarantees that a repeated delivery is byte-identical
    (e.g. the same item id twice), so canonical JSON is a safe identity for
    any record shape — well-formed or not, item or account.
    """
    return json.dumps(record, sort_keys=True, default=str)


def _well_formed_account(record: Any) -> bool:
    """id: str, owner: str, active: JSON boolean. Extra fields are ignored."""
    return (
        isinstance(record, dict)
        and isinstance(record.get("id"), str)
        and isinstance(record.get("owner"), str)
        and isinstance(record.get("active"), bool)
    )


def _well_formed_item(record: Any) -> bool:
    """id: str, owner: str, status: "open"/"done", points: int >= 0."""
    if not isinstance(record, dict):
        return False
    if not isinstance(record.get("id"), str):
        return False
    if not isinstance(record.get("owner"), str):
        return False
    if record.get("status") not in _ITEM_STATUSES:
        return False
    points = record.get("points")
    # bool is a subclass of int in Python; a JSON `true`/`false` must not
    # pass as a point value even though isinstance(True, int) is True.
    if isinstance(points, bool) or not isinstance(points, int):
        return False
    return points >= 0


def _wait_for_retry(response: Response) -> None:
    """Sleep for the `Retry-After` seconds named on a 429 response."""
    try:
        seconds = float(response.headers.get("Retry-After"))
    except (TypeError, ValueError):
        seconds = 1.0
    if seconds > 0:
        time.sleep(seconds)


def _request_with_retry(client: HttpClient, method: str, path: str, **kwargs: Any) -> Response:
    """Issue one request, retrying after any 429 until it stops happening."""
    while True:
        if method == "GET":
            response = client.get(path, **kwargs)
        else:
            response = client.post_json(path, **kwargs)
        if response.status != 429:
            return response
        _wait_for_retry(response)


def _fetch_all(client: HttpClient, path: str, key: str) -> List[Any]:
    """Follow `next_page` from page 1 until it is null, returning `key`'s union."""
    records: List[Any] = []
    page = 1
    while page is not None:
        response = _request_with_retry(client, "GET", path, params={"page": page})
        if response.status != 200:
            raise RuntimeError(
                "unexpected status %d from GET %s" % (response.status, response.url))
        payload = response.json()
        records.extend(payload.get(key) or [])
        page = payload.get("next_page")
    return records


def _write_jsonl(path: str, records: List[Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR,
            directory_client: Optional[HttpClient] = None,
            ledger_client: Optional[HttpClient] = None) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success). `directory_client` and
    `ledger_client`, when given, are used instead of building clients from
    `config` — this is how tests exercise the reconciliation logic against an
    injected fake opener without touching a socket.
    """
    directory = directory_client if directory_client is not None else HttpClient(config.directory_url)
    ledger = ledger_client if ledger_client is not None else HttpClient(config.ledger_url)

    account_records = _fetch_all(ledger, "/v1/accounts", "accounts")
    item_records = _fetch_all(directory, "/v1/items", "items")
    summary_records = _fetch_all(ledger, "/v1/summaries", "summaries")

    errors: List[Dict[str, Any]] = []
    active_owners: Set[str] = set()
    seen: Set[str] = set()
    for record in account_records:
        key = _dedup_key(record)
        if key in seen:
            continue
        seen.add(key)
        if _well_formed_account(record):
            if record["active"]:
                active_owners.add(record["owner"])
        else:
            errors.append({"service": "ledger", "record": record})

    aggregates: Dict[str, Dict[str, int]] = {
        owner: {"open": 0, "done": 0, "points": 0} for owner in active_owners
    }
    orphans: List[Any] = []
    seen = set()
    for record in item_records:
        key = _dedup_key(record)
        if key in seen:
            continue
        seen.add(key)
        if not _well_formed_item(record):
            errors.append({"service": "directory", "record": record})
            continue
        owner = record["owner"]
        if owner not in active_owners:
            orphans.append(record)
            continue
        bucket = aggregates[owner]
        if record["status"] == "open":
            bucket["open"] += 1
        else:
            bucket["done"] += 1
        bucket["points"] += record["points"]

    existing_owners: Set[str] = {
        record["owner"] for record in summary_records
        if isinstance(record, dict) and isinstance(record.get("owner"), str)
    }

    output_summaries: List[Dict[str, Any]] = []
    for owner in sorted(active_owners):
        bucket = aggregates[owner]
        summary = Summary(owner=owner, open_items=bucket["open"],
                          done_items=bucket["done"], total_points=bucket["points"])
        body = summary.to_dict()
        if owner not in existing_owners:
            response = _request_with_retry(
                ledger, "POST", "/v1/summaries", payload=body,
                headers={"Idempotency-Key": "sync-%s" % owner})
            if response.status not in (200, 201):
                raise RuntimeError(
                    "ledger rejected summary for %r: %d %s"
                    % (owner, response.status, response.text))
        output_summaries.append(body)

    os.makedirs(out_dir, exist_ok=True)
    _write_jsonl(os.path.join(out_dir, "summary.jsonl"), output_summaries)
    _write_jsonl(os.path.join(out_dir, "orphans.jsonl"), orphans)
    _write_jsonl(os.path.join(out_dir, "errors.jsonl"), errors)
    return 0
