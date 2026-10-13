"""The `sync` command: reconcile the Directory and the Ledger.

Everything else in the package is in place — config loading, the HTTP client,
the record types. What lives here is the part that has to make decisions, and
it is not written yet.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Set

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

    errors: List[Dict[str, Any]] = []
    error_keys: Set[str] = set()
    orphans: List[Any] = []
    orphan_keys: Set[str] = set()

    accounts = _read_pages(ledger, "/v1/accounts", "accounts")
    valid_accounts: Dict[str, Dict[str, Any]] = {}
    for record in accounts:
        if _valid_account(record):
            # The service promises owner uniqueness among valid accounts.
            valid_accounts[record["owner"]] = record
        else:
            _add_unique(errors, error_keys, {"service": "ledger", "record": record})

    items = _read_pages(directory, "/v1/items", "items")
    counts: Dict[str, List[int]] = {
        owner: [0, 0, 0] for owner, account in valid_accounts.items()
        if account["active"]
    }
    seen_item_ids: Set[str] = set()
    for record in items:
        if not _valid_item(record):
            _add_unique(errors, error_keys, {"service": "directory", "record": record})
            continue
        if record["id"] in seen_item_ids:
            continue
        seen_item_ids.add(record["id"])
        owner = record["owner"]
        if owner not in counts:
            _add_unique(orphans, orphan_keys, record)
            continue
        if record["status"] == "open":
            counts[owner][0] += 1
        else:
            counts[owner][1] += 1
        counts[owner][2] += record["points"]

    existing = _read_pages(ledger, "/v1/summaries", "summaries")
    existing_owners = {
        record["owner"] for record in existing
        if isinstance(record, dict) and isinstance(record.get("owner"), str)
    }

    posted: List[Dict[str, Any]] = []
    for owner, values in counts.items():
        if owner in existing_owners:
            continue
        body = {"owner": owner, "open_items": values[0],
                "done_items": values[1], "total_points": values[2]}
        response = ledger.request("POST", "/v1/summaries", payload=body,
                                  headers={"Idempotency-Key": "sync-" + owner})
        if response.status == 429:
            # request() handles 429 itself, so this is defensive only.
            raise RuntimeError("unexpected unhandled rate limit")
        if response.status not in (200, 201):
            raise RuntimeError("ledger rejected summary for %s with HTTP %d" %
                               (owner, response.status))
        posted.append(body)

    _write_jsonl(os.path.join(out_dir, "errors.jsonl"), errors)
    _write_jsonl(os.path.join(out_dir, "orphans.jsonl"), orphans)
    _append_jsonl(os.path.join(out_dir, "summary.jsonl"), posted)
    return 0


class _RateLimitedClient:
    """An HttpClient that retries 429 responses and throttles its own service."""

    def __init__(self, client: HttpClient) -> None:
        self.client = client
        self._not_before = 0.0

    def request(self, method: str, path: str, **kwargs: Any) -> Response:
        while True:
            delay = self._not_before - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            response = self.client.request(method, path, **kwargs)
            if response.status != 429:
                return response
            retry_after = _retry_after(response)
            self._not_before = max(self._not_before,
                                   time.monotonic() + retry_after)

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Response:
        return self.request("GET", path, params=params)


def _retry_after(response: Response) -> float:
    value = response.headers.get("Retry-After")
    try:
        return max(0.0, float(value)) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _read_pages(client: _RateLimitedClient, path: str, key: str) -> List[Any]:
    page = 1
    records: List[Any] = []
    while page is not None:
        response = client.get(path, {"page": page})
        if response.status != 200:
            raise RuntimeError("GET %s failed with HTTP %d" % (path, response.status))
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
            raise RuntimeError("invalid response from %s" % path)
        records.extend(payload[key])
        page = payload.get("next_page")
        if page is not None and (isinstance(page, bool) or
                                 not isinstance(page, int) or page < 1):
            raise RuntimeError("invalid pagination response from %s" % path)
    return records


def _valid_account(record: Any) -> bool:
    return (isinstance(record, dict) and isinstance(record.get("id"), str) and
            isinstance(record.get("owner"), str) and
            isinstance(record.get("active"), bool))


def _valid_item(record: Any) -> bool:
    return (isinstance(record, dict) and isinstance(record.get("id"), str) and
            isinstance(record.get("owner"), str) and
            record.get("status") in ("open", "done") and
            isinstance(record.get("points"), int) and
            not isinstance(record.get("points"), bool) and record["points"] >= 0)


def _json_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _add_unique(entries: List[Any], keys: Set[str], value: Any) -> None:
    key = _json_key(value)
    if key not in keys:
        keys.add(key)
        entries.append(value)


def _write_jsonl(path: str, entries: Iterable[Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def _append_jsonl(path: str, entries: Iterable[Any]) -> None:
    entries = list(entries)
    if not entries:
        # Creating the empty file is needed on a first run, while avoiding a
        # rewrite preserves the exact bytes from a completed earlier run.
        if not os.path.exists(path):
            _write_jsonl(path, ())
        return
    with open(path, "a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
