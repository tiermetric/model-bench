"""The `sync` command: reconcile the Directory and the Ledger.

Everything else in the package is in place — config loading, the HTTP client,
the record types. What lives here is the part that has to make decisions, and
it is not written yet.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, List, Set

from .config import Config
from .http_client import HttpClient, Response

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    os.makedirs(out_dir, exist_ok=True)

    directory = _RateLimitedClient(HttpClient(config.directory_url))
    ledger = _RateLimitedClient(HttpClient(config.ledger_url))

    accounts = _read_pages(ledger, "/v1/accounts", "accounts")
    items = _read_pages(directory, "/v1/items", "items")
    summaries = _read_pages(ledger, "/v1/summaries", "summaries")

    errors: List[Dict[str, Any]] = []
    orphans: List[Any] = []
    active_owners: Set[str] = set()

    for record in accounts:
        if _valid_account(record):
            if record["active"]:
                active_owners.add(record["owner"])
        else:
            errors.append({"service": "ledger", "record": record})

    counts: Dict[str, List[int]] = {
        owner: [0, 0, 0] for owner in active_owners
    }
    seen_item_ids: Set[str] = set()
    for record in items:
        # Item ids are the identity supplied by the Directory, including for
        # malformed deliveries that still carry a string id.
        if isinstance(record, dict) and isinstance(record.get("id"), str):
            if record["id"] in seen_item_ids:
                continue
            seen_item_ids.add(record["id"])
        elif not _valid_item(record):
            errors.append({"service": "directory", "record": record})
            continue

        if not _valid_item(record):
            errors.append({"service": "directory", "record": record})
            continue
        if record["owner"] not in active_owners:
            orphans.append(record)
            continue
        bucket = counts[record["owner"]]
        bucket[0 if record["status"] == "open" else 1] += 1
        bucket[2] += record["points"]

    required: Dict[str, Dict[str, Any]] = {}
    for owner in active_owners:
        open_items, done_items, total_points = counts[owner]
        required[owner] = {
            "owner": owner,
            "open_items": open_items,
            "done_items": done_items,
            "total_points": total_points,
        }

    existing_owners = {
        record.get("owner") for record in summaries
        if isinstance(record, dict) and isinstance(record.get("owner"), str)
    }
    posted: List[Dict[str, Any]] = []
    for owner in sorted(required):
        if owner in existing_owners:
            continue
        body = required[owner]
        response = ledger.post_json(
            "/v1/summaries", body,
            headers={"Idempotency-Key": "sync-" + owner},
        )
        if response.status not in (200, 201):
            raise RuntimeError("Ledger rejected summary for %r: HTTP %d" %
                               (owner, response.status))
        posted.append(body)

    _write_jsonl(os.path.join(out_dir, "errors.jsonl"), _unique_jsonl(errors))
    _write_jsonl(os.path.join(out_dir, "orphans.jsonl"), _unique_jsonl(orphans))
    _merge_summary_log(os.path.join(out_dir, "summary.jsonl"), posted)
    return 0


class _RateLimitedClient:
    """A client that retries 429 responses and throttles only its own service."""

    def __init__(self, client: HttpClient) -> None:
        self.client = client
        self._available_at = 0.0

    def _request(self, operation: Any) -> Response:
        while True:
            delay = self._available_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            response = operation()
            if response.status != 429:
                return response
            retry_after = response.headers.get("Retry-After")
            try:
                seconds = float(retry_after)
            except (TypeError, ValueError):
                seconds = 0.0
            self._available_at = max(self._available_at, time.monotonic() + max(0.0, seconds))

    def get(self, path: str, params: Dict[str, Any]) -> Response:
        return self._request(lambda: self.client.get(path, params=params))

    def post_json(self, path: str, body: Dict[str, Any],
                  headers: Dict[str, str]) -> Response:
        return self._request(lambda: self.client.post_json(path, body, headers=headers))


def _read_pages(client: _RateLimitedClient, path: str, key: str) -> List[Any]:
    records: List[Any] = []
    page = 1
    while True:
        response = client.get(path, {"page": page})
        if response.status != 200:
            raise RuntimeError("GET %s failed: HTTP %d" % (path, response.status))
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
            raise RuntimeError("invalid response from %s" % path)
        records.extend(payload[key])
        next_page = payload.get("next_page")
        if next_page is None:
            return records
        if isinstance(next_page, bool) or not isinstance(next_page, int) or next_page < 1:
            raise RuntimeError("invalid next_page from %s" % path)
        page = next_page


def _valid_account(record: Any) -> bool:
    return (isinstance(record, dict) and isinstance(record.get("id"), str) and
            isinstance(record.get("owner"), str) and
            type(record.get("active")) is bool)


def _valid_item(record: Any) -> bool:
    return (isinstance(record, dict) and isinstance(record.get("id"), str) and
            isinstance(record.get("owner"), str) and
            record.get("status") in ("open", "done") and
            type(record.get("points")) is int and record["points"] >= 0)


def _json_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _unique_jsonl(records: Iterable[Any]) -> List[Any]:
    result: List[Any] = []
    seen: Set[str] = set()
    for record in records:
        key = _json_key(record)
        if key not in seen:
            seen.add(key)
            result.append(record)
    return result


def _write_jsonl(path: str, records: Iterable[Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def _merge_summary_log(path: str, posted: Iterable[Dict[str, Any]]) -> None:
    existing: List[Any] = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    try:
                        existing.append(json.loads(line))
                    except ValueError:
                        # The tool only creates valid lines; retain unrelated
                        # existing content rather than destroying user data.
                        existing.append(line.rstrip("\n"))
    merged = _unique_jsonl(existing + list(posted))
    _write_jsonl(path, merged)
