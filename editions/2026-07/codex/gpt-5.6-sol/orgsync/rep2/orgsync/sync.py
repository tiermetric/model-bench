"""Implementation of the Directory-to-Ledger reconciliation command."""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Dict, Iterable, List, Mapping, Set

from .config import Config
from .http_client import HttpClient, Response

DEFAULT_OUT_DIR = "out"


class SyncError(RuntimeError):
    """A service returned a response with which reconciliation cannot continue."""


def _is_int(value: Any) -> bool:
    # bool is an int subclass in Python, but is a distinct JSON type.
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_account(record: Any) -> bool:
    return (
        isinstance(record, dict)
        and isinstance(record.get("id"), str)
        and isinstance(record.get("owner"), str)
        and type(record.get("active")) is bool
    )


def _valid_item(record: Any) -> bool:
    return (
        isinstance(record, dict)
        and isinstance(record.get("id"), str)
        and isinstance(record.get("owner"), str)
        and record.get("status") in ("open", "done")
        and isinstance(record.get("status"), str)
        and _is_int(record.get("points"))
        and record["points"] >= 0
    )


def _valid_summary(record: Any) -> bool:
    if not isinstance(record, dict) or not isinstance(record.get("owner"), str):
        return False
    return all(
        _is_int(record.get(field)) and record[field] >= 0
        for field in ("open_items", "done_items", "total_points")
    )


def _retry_after(response: Response) -> float:
    raw = response.headers.get("Retry-After")
    try:
        seconds = float(raw)
    except (TypeError, ValueError) as exc:
        raise SyncError("429 from %s has invalid Retry-After header" % response.url) from exc
    if seconds < 0:
        raise SyncError("429 from %s has negative Retry-After header" % response.url)
    return seconds


def _request_with_retry(client: HttpClient, method: str, path: str, **kwargs: Any) -> Response:
    while True:
        if method == "GET":
            response = client.get(path, params=kwargs.get("params"))
        else:
            response = client.post_json(
                path, kwargs["payload"], headers=kwargs.get("headers")
            )
        if response.status != 429:
            return response
        time.sleep(_retry_after(response))


def _pages(client: HttpClient, path: str, collection: str) -> Iterable[Any]:
    page: Any = 1
    seen_pages: Set[int] = set()
    while page is not None:
        if not _is_int(page) or page < 1 or page in seen_pages:
            raise SyncError("invalid pagination from %s: %r" % (path, page))
        seen_pages.add(page)
        response = _request_with_retry(client, "GET", path, params={"page": page})
        if response.status != 200:
            raise SyncError("GET %s returned HTTP %d" % (response.url, response.status))
        try:
            document = response.json()
        except ValueError as exc:
            raise SyncError("GET %s returned invalid JSON" % response.url) from exc
        if not isinstance(document, dict) or not isinstance(document.get(collection), list):
            raise SyncError("GET %s returned an invalid page" % response.url)
        next_page = document.get("next_page")
        if next_page is not None and (not _is_int(next_page) or next_page < 1):
            raise SyncError("GET %s returned invalid next_page" % response.url)
        yield from document[collection]
        page = next_page


def _key(value: Any) -> str:
    """Stable identity for arbitrary JSON data."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _unique(values: Iterable[Any]) -> List[Any]:
    result: List[Any] = []
    seen: Set[str] = set()
    for value in values:
        identity = _key(value)
        if identity not in seen:
            seen.add(identity)
            result.append(value)
    return result


def _read_jsonl(path: str) -> List[Any]:
    if not os.path.exists(path):
        return []
    values: List[Any] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    values.append(json.loads(line))
    except (OSError, ValueError):
        # The output is a cache/audit artifact, not an input authority. If it is
        # damaged, reconstruct it from this run instead of failing reconciliation.
        return []
    return values


def _write_jsonl(path: str, values: Iterable[Any]) -> None:
    directory = os.path.dirname(path) or "."
    fd, temporary = tempfile.mkstemp(prefix=".orgsync-", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for value in values:
                handle.write(json.dumps(value, separators=(",", ":"), ensure_ascii=False))
                handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Read both services, reconcile their records, and write audit JSONL."""
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)

    raw_accounts = list(_pages(ledger, "/v1/accounts", "accounts"))
    raw_items = list(_pages(directory, "/v1/items", "items"))
    raw_summaries = list(_pages(ledger, "/v1/summaries", "summaries"))

    errors: List[Dict[str, Any]] = []
    accounts = []
    for record in raw_accounts:
        if _valid_account(record):
            accounts.append(record)
        else:
            errors.append({"service": "ledger", "record": record})

    items_by_id: Dict[str, Mapping[str, Any]] = {}
    for record in raw_items:
        if _valid_item(record):
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
    totals: Dict[str, Dict[str, Any]] = {
        owner: {"owner": owner, "open_items": 0, "done_items": 0, "total_points": 0}
        for owner in active_owners
    }
    orphans: List[Mapping[str, Any]] = []
    for item in items_by_id.values():
        owner = item["owner"]
        if owner not in active_owners:
            orphans.append(item)
            continue
        totals[owner][item["status"] + "_items"] += 1
        totals[owner]["total_points"] += item["points"]

    os.makedirs(out_dir, exist_ok=True)
    summary_path = os.path.join(out_dir, "summary.jsonl")
    posted = _read_jsonl(summary_path)
    for owner in sorted(active_owners):
        if owner in existing_owners:
            continue
        body = totals[owner]
        response = _request_with_retry(
            ledger,
            "POST",
            "/v1/summaries",
            payload=body,
            headers={"Idempotency-Key": "sync-" + owner},
        )
        if response.status not in (200, 201):
            raise SyncError("POST %s returned HTTP %d" % (response.url, response.status))
        posted.append(body)

    _write_jsonl(os.path.join(out_dir, "errors.jsonl"), _unique(errors))
    _write_jsonl(os.path.join(out_dir, "orphans.jsonl"), _unique(orphans))
    _write_jsonl(summary_path, _unique(posted))
    return 0
