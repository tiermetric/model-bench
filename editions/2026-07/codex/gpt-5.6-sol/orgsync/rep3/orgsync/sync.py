"""Reconcile Directory items with Ledger accounts and summaries."""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Dict, Iterable, List, Set, Tuple

from .config import Config
from .http_client import HttpClient, Response

DEFAULT_OUT_DIR = "out"


class SyncError(RuntimeError):
    """A service returned a response that the sync cannot safely use."""


def _is_string(value: Any) -> bool:
    return isinstance(value, str)


def _is_integer(value: Any) -> bool:
    # bool subclasses int in Python, but it is a distinct JSON type.
    return type(value) is int


def _valid_account(record: Any) -> bool:
    return (
        isinstance(record, dict)
        and _is_string(record.get("id"))
        and _is_string(record.get("owner"))
        and type(record.get("active")) is bool
    )


def _valid_item(record: Any) -> bool:
    return (
        isinstance(record, dict)
        and _is_string(record.get("id"))
        and _is_string(record.get("owner"))
        and record.get("status") in ("open", "done")
        and _is_integer(record.get("points"))
        and record["points"] >= 0
    )


def _valid_summary(record: Any) -> bool:
    if not isinstance(record, dict) or not _is_string(record.get("owner")):
        return False
    return all(
        _is_integer(record.get(field)) and record[field] >= 0
        for field in ("open_items", "done_items", "total_points")
    )


def _record_key(record: Any) -> str:
    """Stable identity for an arbitrary JSON value."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _request_with_rate_limit(client: HttpClient, method: str, path: str,
                             **kwargs: Any) -> Response:
    while True:
        if method == "GET":
            response = client.get(path, params=kwargs.get("params"))
        else:
            response = client.post_json(
                path, kwargs["payload"], headers=kwargs.get("headers"))
        if response.status != 429:
            return response
        raw_delay = response.headers.get("Retry-After")
        try:
            delay = float(raw_delay)
        except (TypeError, ValueError):
            raise SyncError("429 from %s has invalid Retry-After header" % response.url)
        if delay < 0:
            raise SyncError("429 from %s has negative Retry-After header" % response.url)
        time.sleep(delay)


def _pages(client: HttpClient, path: str, collection: str) -> Iterable[Any]:
    page = 1
    seen_pages: Set[int] = set()
    while True:
        if page in seen_pages:
            raise SyncError("pagination cycle at %s page %d" % (path, page))
        seen_pages.add(page)
        response = _request_with_rate_limit(
            client, "GET", path, params={"page": page})
        if response.status != 200:
            raise SyncError("GET %s returned HTTP %d" % (response.url, response.status))
        try:
            body = response.json()
        except ValueError as exc:
            raise SyncError("GET %s returned invalid JSON" % response.url) from exc
        if not isinstance(body, dict) or not isinstance(body.get(collection), list):
            raise SyncError("GET %s returned an invalid page" % response.url)
        yield from body[collection]
        next_page = body.get("next_page")
        if next_page is None:
            return
        if type(next_page) is not int or next_page < 1:
            raise SyncError("GET %s returned invalid next_page" % response.url)
        page = next_page


def _unique(values: Iterable[Any]) -> List[Any]:
    result: List[Any] = []
    seen: Set[str] = set()
    for value in values:
        key = _record_key(value)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _read_json_lines(path: str) -> List[Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    except (OSError, ValueError):
        # Output is a report, not input authority. A damaged old report must
        # not influence service writes.
        return []


def _write_json_lines(path: str, records: Iterable[Any]) -> None:
    directory = os.path.dirname(path) or "."
    fd, temporary = tempfile.mkstemp(prefix=".orgsync-", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run a complete, rate-limit-aware reconciliation pass."""
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)

    raw_accounts = list(_pages(ledger, "/v1/accounts", "accounts"))
    raw_items = list(_pages(directory, "/v1/items", "items"))
    raw_summaries = list(_pages(ledger, "/v1/summaries", "summaries"))

    malformed: List[Dict[str, Any]] = []
    accounts = []
    for record in raw_accounts:
        if _valid_account(record):
            accounts.append(record)
        else:
            malformed.append({"service": "ledger", "record": record})

    items_by_id: Dict[str, Dict[str, Any]] = {}
    for record in raw_items:
        if _valid_item(record):
            items_by_id.setdefault(record["id"], record)
        else:
            malformed.append({"service": "directory", "record": record})

    existing_owners: Set[str] = set()
    for record in raw_summaries:
        if _valid_summary(record):
            existing_owners.add(record["owner"])
        else:
            malformed.append({"service": "ledger", "record": record})

    active_owners = {record["owner"] for record in accounts if record["active"]}
    totals: Dict[str, Dict[str, Any]] = {
        owner: {"owner": owner, "open_items": 0, "done_items": 0,
                "total_points": 0}
        for owner in active_owners
    }
    orphans: List[Dict[str, Any]] = []
    for item in items_by_id.values():
        if item["owner"] not in active_owners:
            orphans.append(item)
            continue
        summary = totals[item["owner"]]
        summary[item["status"] + "_items"] += 1
        summary["total_points"] += item["points"]

    os.makedirs(out_dir, exist_ok=True)
    summary_path = os.path.join(out_dir, "summary.jsonl")
    posted = _read_json_lines(summary_path)
    for owner in sorted(active_owners):
        if owner in existing_owners:
            continue
        body = totals[owner]
        response = _request_with_rate_limit(
            ledger, "POST", "/v1/summaries", payload=body,
            headers={"Idempotency-Key": "sync-" + owner})
        if response.status not in (200, 201):
            raise SyncError(
                "POST %s returned HTTP %d" % (response.url, response.status))
        posted.append(body)
        existing_owners.add(owner)

    _write_json_lines(os.path.join(out_dir, "orphans.jsonl"), _unique(orphans))
    _write_json_lines(os.path.join(out_dir, "errors.jsonl"), _unique(malformed))
    _write_json_lines(summary_path, _unique(posted))
    return 0
