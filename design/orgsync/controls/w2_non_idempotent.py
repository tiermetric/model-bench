"""W2 — "careful but non-idempotent": one axis wrong, everything else right.

SEALED control (CONTROLS.md). Installed into a repo skeleton as
`orgsync/sync.py`. It must FAIL the judge on the IDEMPOTENCY assertion and on
essentially nothing else — a control that fails everything proves nothing about
which assertion bites.

What it gets right (all of it, deliberately): every page of all three
collections is read (rule 1); malformed records are skipped, recorded once and
excluded from every count (rule 4); a duplicate delivery is counted once (rule
2); inactive and malformed-account owners produce orphans, written once (rule
3); active accounts with no items get an all-zero summary (rule 2); every 429
is waited out for `Retry-After` and the request retried (rule 6); the output
files are rewritten, not appended, so their content is stable across runs
(rule 7, file half).

The single defect: it fetches `GET /v1/summaries` — and then throws the result
away. Nothing is ever derived from what the ledger already holds, so every run
re-POSTs the full set. The ledger answers `200` replay (same key, same body),
so nothing is *rejected* and no output file changes; the only visible symptom
is a write request on the second run.

Expected judge verdict: FAIL, on rule 7 ("must issue no write requests").
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

from .config import Config
from .http_client import HttpClient

DEFAULT_OUT_DIR = "out"

MAX_RATE_LIMIT_RETRIES = 20
MAX_PAGES = 1000
RETRY_MARGIN_SECONDS = 0.02


def _retry_after(headers: Any, default: float = 1.0) -> float:
    try:
        raw = headers.get("Retry-After")
    except AttributeError:
        return default
    if raw is None:
        return default
    try:
        return max(0.0, float(str(raw).strip()))
    except ValueError:
        return default


def _request(client: HttpClient, method: str, path: str, **kwargs: Any):
    """One request, waiting out any 429 and retrying it (prompt rule 6)."""
    for _ in range(MAX_RATE_LIMIT_RETRIES + 1):
        response = client.request(method, path, **kwargs)
        if response.status != 429:
            return response
        time.sleep(_retry_after(response.headers) + RETRY_MARGIN_SECONDS)
    raise RuntimeError("rate limited repeatedly on %s %s" % (method, path))


def _fetch_all(client: HttpClient, path: str, key: str) -> List[Any]:
    """Follow `next_page` from page 1 until it is null (prompt rule 1)."""
    records: List[Any] = []
    page = 1
    for _ in range(MAX_PAGES):
        response = _request(client, "GET", path, params={"page": page})
        if response.status != 200:
            raise RuntimeError("GET %s?page=%d -> %d" % (path, page, response.status))
        payload = response.json()
        records.extend(payload.get(key) or [])
        nxt = payload.get("next_page")
        if nxt is None:
            return records
        page = nxt
    raise RuntimeError("GET %s: too many pages" % path)


def _well_formed_account(record: Any) -> bool:
    return (
        isinstance(record, dict)
        and isinstance(record.get("id"), str)
        and isinstance(record.get("owner"), str)
        and type(record.get("active")) is bool
    )


def _well_formed_item(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    points = record.get("points")
    return (
        isinstance(record.get("id"), str)
        and isinstance(record.get("owner"), str)
        and record.get("status") in ("open", "done")
        and isinstance(points, int)
        and not isinstance(points, bool)
        and points >= 0
    )


def _write_jsonl(path: str, records: List[Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)

    accounts = _fetch_all(ledger, "/v1/accounts", "accounts")
    items = _fetch_all(directory, "/v1/items", "items")
    # Read (rule 1 says so) — and then never used. THIS is the defect.
    _fetch_all(ledger, "/v1/summaries", "summaries")

    errors: List[Dict[str, Any]] = []
    seen_errors = set()

    def record_error(service: str, record: Any) -> None:
        entry = {"service": service, "record": record}
        marker = json.dumps(entry, sort_keys=True)
        if marker not in seen_errors:
            seen_errors.add(marker)
            errors.append(entry)

    active_owners = set()
    for account in accounts:
        if not _well_formed_account(account):
            record_error("ledger", account)
            continue
        if account["active"]:
            active_owners.add(account["owner"])

    summaries = {
        owner: {"owner": owner, "open_items": 0, "done_items": 0, "total_points": 0}
        for owner in active_owners
    }

    orphans: List[Any] = []
    seen_items = set()
    for item in items:
        if not _well_formed_item(item):
            record_error("directory", item)
            continue
        if item["id"] in seen_items:
            continue
        seen_items.add(item["id"])
        owner = item["owner"]
        if owner not in active_owners:
            orphans.append(item)
            continue
        summary = summaries[owner]
        summary["open_items" if item["status"] == "open" else "done_items"] += 1
        summary["total_points"] += item["points"]

    for owner in sorted(summaries):
        # No check against what the ledger already holds: post it every time.
        response = _request(ledger, "POST", "/v1/summaries",
                            payload=summaries[owner],
                            headers={"Idempotency-Key": "sync-%s" % owner})
        if response.status not in (200, 201):
            raise RuntimeError("POST for %r -> %d" % (owner, response.status))

    _write_jsonl(os.path.join(out_dir, "summary.jsonl"),
                 [summaries[owner] for owner in sorted(summaries)])
    _write_jsonl(os.path.join(out_dir, "orphans.jsonl"), orphans)
    _write_jsonl(os.path.join(out_dir, "errors.jsonl"), errors)
    return 0
