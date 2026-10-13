"""The ``sync`` command: reconcile Directory work items and Ledger accounts."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, List, Set

from .config import Config
from .http_client import HttpClient, Response
from .models import Account, Item, Summary

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"


def _is_int(value: Any) -> bool:
    """JSON booleans are not JSON integers for this protocol."""
    return isinstance(value, int) and not isinstance(value, bool)


def _account(record: Any) -> Account | None:
    if not isinstance(record, dict):
        return None
    if (not isinstance(record.get("id"), str)
            or not isinstance(record.get("owner"), str)
            or type(record.get("active")) is not bool):
        return None
    return Account(record["id"], record["owner"], record["active"])


def _item(record: Any) -> Item | None:
    if not isinstance(record, dict):
        return None
    if (not isinstance(record.get("id"), str)
            or not isinstance(record.get("owner"), str)
            or record.get("status") not in ("open", "done")
            or not _is_int(record.get("points")) or record["points"] < 0):
        return None
    return Item(record["id"], record["owner"], record["status"], record["points"])


def _summary(record: Any) -> Summary | None:
    """Validate a summary returned by the Ledger before treating it as posted."""
    if not isinstance(record, dict) or not isinstance(record.get("owner"), str):
        return None
    fields = ("open_items", "done_items", "total_points")
    if any(not _is_int(record.get(field)) or record[field] < 0 for field in fields):
        return None
    return Summary(record["owner"], record["open_items"], record["done_items"],
                   record["total_points"])


def _key(value: Any) -> str:
    """A stable key for arbitrary JSON values, including dicts and lists."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class _Service:
    """A client wrapper which makes the service-wide 429 pause explicit."""

    def __init__(self, base_url: str) -> None:
        self.client = HttpClient(base_url)
        self.not_before = 0.0

    def request(self, method: str, path: str, *, params: Dict[str, Any] | None = None,
                payload: Any = None, headers: Dict[str, str] | None = None) -> Response:
        while True:
            delay = self.not_before - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            response = self.client.request(method, path, params=params, payload=payload,
                                           headers=headers)
            if response.status != 429:
                return response
            try:
                retry_after = float(response.headers.get("Retry-After", "0"))
            except (TypeError, ValueError):
                retry_after = 0.0
            # A malformed header must not cause a busy loop, while valid values
            # retain the exact service-directed delay.
            self.not_before = time.monotonic() + max(0.0, retry_after)


def _pages(service: _Service, path: str, collection: str) -> Iterable[Any]:
    page = 1
    while True:
        response = service.request("GET", path, params={"page": page})
        if response.status != 200:
            raise RuntimeError("GET %s returned HTTP %d" % (path, response.status))
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError("GET %s returned invalid JSON" % path) from exc
        if not isinstance(body, dict) or not isinstance(body.get(collection), list):
            raise RuntimeError("GET %s returned an invalid page" % path)
        yield from body[collection]
        next_page = body.get("next_page")
        if next_page is None:
            return
        if not _is_int(next_page) or next_page < 1:
            raise RuntimeError("GET %s returned an invalid next_page" % path)
        page = next_page


def _read_jsonl(path: str) -> List[Any]:
    if not os.path.exists(path):
        return []
    values: List[Any] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                try:
                    values.append(json.loads(line))
                except ValueError:
                    # Replace a damaged old line rather than allowing it to
                    # prevent a future successful synchronization.
                    continue
    return values


def _write_unique_jsonl(path: str, values: Iterable[Any]) -> None:
    unique: List[Any] = []
    seen: Set[str] = set()
    for value in values:
        value_key = _key(value)
        if value_key not in seen:
            seen.add(value_key)
            unique.append(value)
    with open(path, "w", encoding="utf-8") as handle:
        for value in unique:
            handle.write(json.dumps(value, separators=(",", ":"), ensure_ascii=False) + "\n")


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run a complete, rate-limit-aware reconciliation pass and return zero."""
    os.makedirs(out_dir, exist_ok=True)
    errors_path = os.path.join(out_dir, "errors.jsonl")
    orphans_path = os.path.join(out_dir, "orphans.jsonl")
    summaries_path = os.path.join(out_dir, "summary.jsonl")
    errors, orphans, posted_output = (_read_jsonl(errors_path), _read_jsonl(orphans_path),
                                      _read_jsonl(summaries_path))

    directory, ledger = _Service(config.directory_url), _Service(config.ledger_url)
    accounts: List[Account] = []
    for raw in _pages(ledger, "/v1/accounts", "accounts"):
        account = _account(raw)
        if account is None:
            errors.append({"service": "ledger", "record": raw})
        else:
            accounts.append(account)

    items: Dict[str, Any] = {}
    parsed_items: Dict[str, Item] = {}
    for raw in _pages(directory, "/v1/items", "items"):
        item = _item(raw)
        if item is None:
            errors.append({"service": "directory", "record": raw})
        elif item.id not in items:  # first delivery is the item's received form
            items[item.id], parsed_items[item.id] = raw, item

    existing_owners: Set[str] = set()
    for raw in _pages(ledger, "/v1/summaries", "summaries"):
        summary = _summary(raw)
        if summary is None:
            errors.append({"service": "ledger", "record": raw})
        else:
            existing_owners.add(summary.owner)

    active_owners = {account.owner for account in accounts if account.active}
    counts = {owner: [0, 0, 0] for owner in active_owners}
    for item_id, item in parsed_items.items():
        if item.owner not in counts:
            orphans.append(items[item_id])
            continue
        if item.status == "open":
            counts[item.owner][0] += 1
        else:
            counts[item.owner][1] += 1
        counts[item.owner][2] += item.points

    for owner in sorted(active_owners):
        if owner in existing_owners:
            continue
        values = counts[owner]
        body = Summary(owner, values[0], values[1], values[2]).to_dict()
        response = ledger.request("POST", "/v1/summaries", payload=body,
                                  headers={"Idempotency-Key": "sync-" + owner})
        if response.status not in (200, 201):
            raise RuntimeError("POST /v1/summaries returned HTTP %d" % response.status)
        posted_output.append(body)

    _write_unique_jsonl(errors_path, errors)
    _write_unique_jsonl(orphans_path, orphans)
    _write_unique_jsonl(summaries_path, posted_output)
    return 0
