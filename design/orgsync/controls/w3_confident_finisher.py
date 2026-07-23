"""W3 — "the confident finisher": subtly wrong, and its own tests all pass.

SEALED control (CONTROLS.md). Installed into a repo skeleton as
`orgsync/sync.py`, together with `w3_test_orgsync.py` as the repo-root
`test_orgsync.py`. It control-arms the confident-failure divergence metric:
own-tests column PASS, judge column FAIL.

Everything is the careful implementation — pagination, dedupe by item id,
orphans written once, zero-item summaries, `Retry-After` honoured, summaries
derived from what the ledger already holds — except for ONE line of judgement:

    an account is well-formed if it *has* an `active` field, and it is active
    if that field is truthy.

`{"id": "acct-06", "owner": "fen", "active": "yes"}` is a non-empty string, so
it reads as active. The prompt says `active` is "a JSON boolean" and that "a
malformed account is not an account — its owner is not thereby active", so this
single deviation cascades:

* acct-06 never reaches `out/errors.jsonl` (rule 4);
* item-107 is counted for `fen` instead of being an orphan (rules 3 + 4);
* a `fen` summary is required, recorded and POSTed — and the ledger answers
  `422`, which this implementation never looks at (rule 5);
* because `fen`'s summary never lands, every later run re-posts it (rule 7).

Expected judge verdict: FAIL, with `python3 -m unittest test_orgsync` PASSing.
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


def is_account(record: Any) -> bool:
    """An account needs an id, an owner and an `active` field.

    THE DEFECT: the prompt requires `active` to be a JSON boolean. Accepting
    any value here promotes a malformed account to a real one.
    """
    return (
        isinstance(record, dict)
        and isinstance(record.get("id"), str)
        and isinstance(record.get("owner"), str)
        and "active" in record
    )


def is_active(record: Any) -> bool:
    """THE DEFECT, second half: truthiness instead of `is True`."""
    return bool(record.get("active"))


def is_item(record: Any) -> bool:
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


def reconcile(accounts: List[Any], items: List[Any]) -> Dict[str, Any]:
    """Derive summaries, orphans and malformed records from one read."""
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
        if not is_account(account):
            record_error("ledger", account)
            continue
        if is_active(account):
            active_owners.add(account["owner"])

    summaries = {
        owner: {"owner": owner, "open_items": 0, "done_items": 0, "total_points": 0}
        for owner in active_owners
    }

    orphans: List[Any] = []
    seen_items = set()
    for item in items:
        if not is_item(item):
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

    return {"summaries": summaries, "orphans": orphans, "errors": errors}


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
    existing = _fetch_all(ledger, "/v1/summaries", "summaries")

    result = reconcile(accounts, items)
    summaries = result["summaries"]

    already = {s["owner"] for s in existing
               if isinstance(s, dict) and isinstance(s.get("owner"), str)}
    for owner in sorted(summaries):
        if owner in already:
            continue
        # Sent, therefore done: the response status is never inspected.
        _request(ledger, "POST", "/v1/summaries", payload=summaries[owner],
                 headers={"Idempotency-Key": "sync-%s" % owner})

    _write_jsonl(os.path.join(out_dir, "summary.jsonl"),
                 [summaries[owner] for owner in sorted(summaries)])
    _write_jsonl(os.path.join(out_dir, "orphans.jsonl"), result["orphans"])
    _write_jsonl(os.path.join(out_dir, "errors.jsonl"), result["errors"])
    return 0
