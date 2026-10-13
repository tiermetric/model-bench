"""The `sync` command: reconcile the Directory and the Ledger."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, List, Mapping, Set

from .config import Config
from .http_client import HttpClient, Response

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"


class SyncError(Exception):
    """The services returned a response that cannot be reconciled safely."""


class _RateLimitedClient:
    """An HTTP client that waits out 429 responses for one service."""

    def __init__(self, client: HttpClient) -> None:
        self.client = client
        self._allowed_at = 0.0

    def _wait(self) -> None:
        delay = self._allowed_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def _request(self, method: str, path: str, **kwargs: Any) -> Response:
        while True:
            self._wait()
            response = self.client.request(method, path, **kwargs)
            if response.status != 429:
                return response
            try:
                seconds = float(response.headers.get("Retry-After", "0"))
            except (TypeError, ValueError):
                seconds = 0.0
            self._allowed_at = time.monotonic() + max(0.0, seconds)

    def get(self, path: str, params: Mapping[str, Any]) -> Response:
        return self._request("GET", path, params=params)

    def post(self, path: str, payload: Any, headers: Mapping[str, str]) -> Response:
        return self._request("POST", path, payload=payload, headers=headers)


def _json_response(response: Response) -> Any:
    if response.status != 200:
        raise SyncError("GET %s returned HTTP %d" % (response.url, response.status))
    try:
        return response.json()
    except ValueError as exc:
        raise SyncError("GET %s did not return JSON" % response.url) from exc


def _pages(client: _RateLimitedClient, path: str, key: str) -> List[Any]:
    page = 1
    records: List[Any] = []
    while page is not None:
        payload = _json_response(client.get(path, {"page": page}))
        if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
            raise SyncError("invalid page from %s" % path)
        records.extend(payload[key])
        next_page = payload.get("next_page")
        if next_page is not None and (isinstance(next_page, bool) or
                                      not isinstance(next_page, int) or next_page < 1):
            raise SyncError("invalid next_page from %s" % path)
        page = next_page
    return records


def _is_account(record: Any) -> bool:
    return (isinstance(record, dict) and isinstance(record.get("id"), str) and
            isinstance(record.get("owner"), str) and
            type(record.get("active")) is bool)


def _is_item(record: Any) -> bool:
    points = record.get("points") if isinstance(record, dict) else None
    return (isinstance(record, dict) and isinstance(record.get("id"), str) and
            isinstance(record.get("owner"), str) and
            record.get("status") in ("open", "done") and
            type(points) is int and points >= 0)


def _entry_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _write_unique(path: str, entries: Iterable[Any]) -> None:
    """Append unseen entries, retaining the original line on later runs."""
    existing: Set[str] = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    existing.add(_entry_key(json.loads(line)))
                except (ValueError, TypeError):
                    # An unrelated damaged line is left untouched.
                    continue
    with open(path, "a", encoding="utf-8") as handle:
        for entry in entries:
            marker = _entry_key(entry)
            if marker in existing:
                continue
            handle.write(json.dumps(entry, ensure_ascii=False,
                                    separators=(",", ":")) + "\n")
            existing.add(marker)


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    os.makedirs(out_dir, exist_ok=True)
    directory = _RateLimitedClient(HttpClient(config.directory_url))
    ledger = _RateLimitedClient(HttpClient(config.ledger_url))

    raw_accounts = _pages(ledger, "/v1/accounts", "accounts")
    raw_items = _pages(directory, "/v1/items", "items")
    raw_summaries = _pages(ledger, "/v1/summaries", "summaries")

    account_errors = [
        {"service": "ledger", "record": record}
        for record in raw_accounts if not _is_account(record)
    ]
    item_errors = [
        {"service": "directory", "record": record}
        for record in raw_items if not _is_item(record)
    ]

    accounts = [record for record in raw_accounts if _is_account(record)]
    active_owners = {record["owner"] for record in accounts if record["active"]}

    # A repeated well-formed item is the same item, identified by its id.
    items_by_id: Dict[str, dict] = {}
    for record in raw_items:
        if _is_item(record):
            items_by_id.setdefault(record["id"], record)
    counts: Dict[str, List[int]] = {owner: [0, 0, 0] for owner in active_owners}
    orphans: List[Any] = []
    orphan_keys: Set[str] = set()
    for item in items_by_id.values():
        if item["owner"] not in active_owners:
            marker = _entry_key(item)
            if marker not in orphan_keys:
                orphans.append(item)
                orphan_keys.add(marker)
            continue
        values = counts[item["owner"]]
        values[0 if item["status"] == "open" else 1] += 1
        values[2] += item["points"]

    _write_unique(os.path.join(out_dir, "errors.jsonl"), account_errors + item_errors)
    _write_unique(os.path.join(out_dir, "orphans.jsonl"), orphans)

    required = {
        owner: {"owner": owner, "open_items": values[0],
                "done_items": values[1], "total_points": values[2]}
        for owner, values in counts.items()
    }
    existing_owners = {
        record.get("owner") for record in raw_summaries
        if isinstance(record, dict) and isinstance(record.get("owner"), str)
    }
    posted: List[dict] = []
    for owner, body in required.items():
        if owner in existing_owners:
            continue
        response = ledger.post("/v1/summaries", body,
                               {"Idempotency-Key": "sync-" + owner})
        if response.status not in (200, 201):
            raise SyncError("POST summary for %s returned HTTP %d" % (owner, response.status))
        posted.append(body)
    _write_unique(os.path.join(out_dir, "summary.jsonl"), posted)
    return 0
