"""Reconcile Directory items with Ledger accounts and summaries."""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from .config import Config
from .http_client import HttpClient, Response

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"


class SyncError(RuntimeError):
    """A service returned a response with which reconciliation cannot continue."""


def _is_int(value: Any) -> bool:
    """JSON booleans are Python ints, but are not JSON integers for this API."""
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_account(record: Any) -> bool:
    return (
        isinstance(record, dict)
        and isinstance(record.get("id"), str)
        and isinstance(record.get("owner"), str)
        and isinstance(record.get("active"), bool)
    )


def _valid_item(record: Any) -> bool:
    return (
        isinstance(record, dict)
        and isinstance(record.get("id"), str)
        and isinstance(record.get("owner"), str)
        and record.get("status") in ("open", "done")
        and _is_int(record.get("points"))
        and record["points"] >= 0
    )


def _valid_summary(record: Any) -> bool:
    if not isinstance(record, dict) or not isinstance(record.get("owner"), str):
        return False
    for field in ("open_items", "done_items", "total_points"):
        if not _is_int(record.get(field)) or record[field] < 0:
            return False
    return True


def _retry_after(response: Response) -> float:
    value = response.headers.get("Retry-After")
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        raise SyncError("429 response from %s has invalid Retry-After header" %
                        response.url)


def _request_with_retry(client: HttpClient, method: str, path: str,
                        params: Optional[Mapping[str, Any]] = None,
                        payload: Optional[Any] = None,
                        headers: Optional[Mapping[str, str]] = None) -> Response:
    while True:
        if method == "GET":
            response = client.get(path, params=params, headers=headers)
        else:
            response = client.post_json(path, payload, headers=headers)
        if response.status != 429:
            return response
        time.sleep(_retry_after(response))


def _pages(client: HttpClient, path: str, field: str) -> Iterable[Any]:
    """Yield raw records from every page, rejecting malformed page envelopes."""
    page: Optional[int] = 1
    seen_pages: Set[int] = set()
    while page is not None:
        if not _is_int(page) or page < 1 or page in seen_pages:
            raise SyncError("invalid pagination for %s: %r" % (path, page))
        seen_pages.add(page)
        response = _request_with_retry(client, "GET", path, params={"page": page})
        if response.status != 200:
            raise SyncError("GET %s returned HTTP %d" % (response.url, response.status))
        try:
            body = response.json()
        except ValueError as exc:
            raise SyncError("GET %s returned invalid JSON" % response.url) from exc
        if not isinstance(body, dict) or not isinstance(body.get(field), list):
            raise SyncError("GET %s returned an invalid page" % response.url)
        next_page = body.get("next_page")
        if next_page is not None and (not _is_int(next_page) or next_page < 1):
            raise SyncError("GET %s returned invalid next_page" % response.url)
        yield from body[field]
        page = next_page


def _identity(record: Any) -> str:
    """A stable identity for arbitrary JSON records, including malformed ones."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _unique(records: Iterable[Any]) -> List[Any]:
    result: List[Any] = []
    seen: Set[str] = set()
    for record in records:
        key = _identity(record)
        if key not in seen:
            seen.add(key)
            result.append(record)
    return result


def _read_json_lines(path: str) -> List[Any]:
    if not os.path.exists(path):
        return []
    records: List[Any] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if line.strip():
                    records.append(json.loads(line))
    except (OSError, ValueError) as exc:
        raise SyncError("could not read %s: %s" % (path, exc)) from exc
    return records


def _write_json_lines(path: str, records: Iterable[Any]) -> None:
    """Atomically replace one JSONL output, avoiding partial run output."""
    directory = os.path.dirname(path) or "."
    fd, temporary = tempfile.mkstemp(prefix=".orgsync-", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for record in _unique(records):
                handle.write(json.dumps(record, separators=(",", ":"),
                                        ensure_ascii=False) + "\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one complete reconciliation pass and return zero on success."""
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)

    raw_accounts = list(_pages(ledger, "/v1/accounts", "accounts"))
    raw_items = list(_pages(directory, "/v1/items", "items"))
    raw_summaries = list(_pages(ledger, "/v1/summaries", "summaries"))

    errors: List[Dict[str, Any]] = []
    accounts: List[Dict[str, Any]] = []
    for record in raw_accounts:
        if _valid_account(record):
            accounts.append(record)
        else:
            errors.append({"service": "ledger", "record": record})

    items_by_id: Dict[str, Dict[str, Any]] = {}
    for record in raw_items:
        if _valid_item(record):
            # The service contract says repeated ids are the same item.
            items_by_id.setdefault(record["id"], record)
        else:
            errors.append({"service": "directory", "record": record})

    existing_owners: Set[str] = set()
    for record in raw_summaries:
        if _valid_summary(record):
            existing_owners.add(record["owner"])
        else:
            errors.append({"service": "ledger", "record": record})

    active_owners = {record["owner"] for record in accounts if record["active"]}
    required: Dict[str, Dict[str, Any]] = {
        owner: {"owner": owner, "open_items": 0, "done_items": 0, "total_points": 0}
        for owner in active_owners
    }
    orphans: List[Dict[str, Any]] = []
    for item in items_by_id.values():
        owner = item["owner"]
        if owner not in active_owners:
            orphans.append(item)
            continue
        summary = required[owner]
        summary[item["status"] + "_items"] += 1
        summary["total_points"] += item["points"]

    os.makedirs(out_dir, exist_ok=True)
    summary_path = os.path.join(out_dir, "summary.jsonl")
    posted = _read_json_lines(summary_path)
    for owner in sorted(active_owners):
        if owner in existing_owners:
            continue
        body = required[owner]
        response = _request_with_retry(
            ledger, "POST", "/v1/summaries", payload=body,
            headers={"Idempotency-Key": "sync-" + owner})
        if response.status not in (200, 201):
            raise SyncError("POST %s returned HTTP %d" %
                            (response.url, response.status))
        posted.append(body)

    _write_json_lines(os.path.join(out_dir, "orphans.jsonl"), orphans)
    _write_json_lines(os.path.join(out_dir, "errors.jsonl"), errors)
    _write_json_lines(summary_path, posted)
    return 0
