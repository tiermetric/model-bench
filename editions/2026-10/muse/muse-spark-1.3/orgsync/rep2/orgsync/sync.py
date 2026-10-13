"""The `sync` command: reconcile the Directory and the Ledger.

Everything else in the package is in place — config loading, the HTTP client,
the record types. What lives here is the part that has to make decisions:

* read every page of items, accounts, and existing summaries,
* decide which records are well-formed (malformed ones are reported, never
  counted, and never stop the run),
* aggregate per-owner summaries for active accounts only,
* POST the summaries the ledger is missing (and only those), and
* honor per-service rate limits (`429` + `Retry-After`) throughout.

Only the Python standard library is used.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Set

from .config import Config
from .http_client import HttpClient, Response

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

ORPHANS_FILENAME = "orphans.jsonl"
ERRORS_FILENAME = "errors.jsonl"
SUMMARY_FILENAME = "summary.jsonl"

#: Statuses a well-formed work item may carry.
VALID_STATUSES = ("open", "done")

#: Wait applied when a `429` carries no parseable `Retry-After` header.
DEFAULT_RETRY_AFTER_SECONDS = 1.0


def is_well_formed_item(record: Any) -> bool:
    """True when `record` is a well-formed Directory work item.

    That is a JSON object with a string `id`, a string `owner`, a `status`
    of `"open"` or `"done"`, and an `points` that is a JSON integer
    (a Python `int`, but not a `bool`) of at least 0. Extra fields are
    ignored.
    """
    if not isinstance(record, dict):
        return False
    if not isinstance(record.get("id"), str):
        return False
    if not isinstance(record.get("owner"), str):
        return False
    if record.get("status") not in VALID_STATUSES:
        return False
    points = record.get("points")
    if isinstance(points, bool) or not isinstance(points, int):
        return False
    return points >= 0


def is_well_formed_account(record: Any) -> bool:
    """True when `record` is a well-formed Ledger account.

    That is a JSON object with a string `id`, a string `owner`, and an
    `active` that is a JSON boolean. Extra fields are ignored.
    """
    if not isinstance(record, dict):
        return False
    if not isinstance(record.get("id"), str):
        return False
    if not isinstance(record.get("owner"), str):
        return False
    return isinstance(record.get("active"), bool)


def retry_after_seconds(response: Response) -> float:
    """Seconds to wait after a `429`, from its `Retry-After` header."""
    try:
        raw = response.headers.get("Retry-After") if response.headers is not None else None
    except AttributeError:
        raw = None
    if raw is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    try:
        return max(0.0, float(str(raw).strip()))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS


class _ServiceClient:
    """One service plus its rate-limit state.

    After a `429` from the service, no further request goes to that service
    until at least `Retry-After` seconds have elapsed; the failed request is
    then retried. State is per service, so waiting on one never throttles
    the other.
    """

    def __init__(self, base_url: str) -> None:
        self._client = HttpClient(base_url)
        self._not_before = 0.0

    def _pace(self) -> None:
        wait = self._not_before - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def _note_rate_limited(self, response: Response) -> None:
        self._not_before = time.monotonic() + retry_after_seconds(response)

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Response:
        """GET `path`, transparently retrying rate limits."""
        while True:
            self._pace()
            response = self._client.get(path, params)
            if response.status != 429:
                return response
            self._note_rate_limited(response)

    def post(self, path: str, payload: Any,
             headers: Optional[Dict[str, str]] = None) -> Response:
        """POST `payload` to `path`, transparently retrying rate limits."""
        while True:
            self._pace()
            response = self._client.post_json(path, payload, headers)
            if response.status != 429:
                return response
            self._note_rate_limited(response)


def _fetch_all_pages(service: _ServiceClient, path: str, key: str) -> List[Any]:
    """GET every page of a paginated collection, following `next_page`.

    A `429` never counts as data: it is waited out and the page retried.
    """
    records: List[Any] = []
    page = 1
    seen_pages = {1}
    while True:
        response = service.get(path, {"page": page})
        if response.status != 200:
            raise RuntimeError(
                "GET %s?page=%d returned status %d" % (path, page, response.status))
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError(
                "GET %s?page=%d returned invalid JSON" % (path, page)) from exc
        if not isinstance(body, dict):
            raise RuntimeError(
                "GET %s?page=%d returned a non-object page" % (path, page))
        batch = body.get(key, [])
        if not isinstance(batch, list):
            raise RuntimeError(
                "GET %s?page=%d has a non-list %r" % (path, page, key))
        records.extend(batch)
        nxt = body.get("next_page")
        if nxt is None:
            return records
        if isinstance(nxt, bool) or not isinstance(nxt, int):
            raise RuntimeError(
                "GET %s?page=%d has a non-integer next_page: %r" % (path, page, nxt))
        if nxt in seen_pages:
            raise RuntimeError(
                "GET %s?page=%d repeats page %d" % (path, page, nxt))
        seen_pages.add(nxt)
        page = nxt


def _write_jsonl(path: str, lines: List[str]) -> None:
    """Write `lines` to `path`, one per line, in a deterministic order."""
    with open(path, "w", encoding="utf-8") as handle:
        for line in sorted(lines):
            handle.write(line + "\n")


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    directory = _ServiceClient(config.directory_url)
    ledger = _ServiceClient(config.ledger_url)

    # 1. Read everything.
    raw_items = _fetch_all_pages(directory, "/v1/items", "items")
    raw_accounts = _fetch_all_pages(ledger, "/v1/accounts", "accounts")
    raw_summaries = _fetch_all_pages(ledger, "/v1/summaries", "summaries")

    errors: List[Dict[str, Any]] = []
    seen_error_keys: Set[Any] = set()

    def note_error(service: str, record: Any) -> None:
        try:
            fingerprint = json.dumps(record, sort_keys=True)
        except (TypeError, ValueError):
            fingerprint = repr(record)
        key = (service, fingerprint)
        if key in seen_error_keys:
            return
        seen_error_keys.add(key)
        errors.append({"service": service, "record": record})

    # 2. The ledger decides who counts. A repeated delivery of an account
    # (same string id) is the same account delivered twice.
    active_owners: Set[str] = set()
    seen_account_ids: Set[str] = set()
    for record in raw_accounts:
        rid = record.get("id") if isinstance(record, dict) else None
        if isinstance(rid, str):
            if rid in seen_account_ids:
                continue
            seen_account_ids.add(rid)
        if not is_well_formed_account(record):
            note_error("ledger", record)
            continue
        if record["active"]:
            active_owners.add(record["owner"])

    # 2-3. Aggregate items (each id counted once) or file orphans.
    counters: Dict[str, List[int]] = {}
    orphans: Dict[str, Any] = {}
    seen_item_ids: Set[str] = set()
    for record in raw_items:
        rid = record.get("id") if isinstance(record, dict) else None
        if isinstance(rid, str):
            if rid in seen_item_ids:
                continue
            seen_item_ids.add(rid)
        if not is_well_formed_item(record):
            note_error("directory", record)
            continue
        owner = record["owner"]
        if owner in active_owners:
            counter = counters.setdefault(owner, [0, 0, 0])
            if record["status"] == "open":
                counter[0] += 1
            else:
                counter[1] += 1
            counter[2] += record["points"]
        else:
            # Well-formed items have string ids, already de-duplicated above.
            orphans.setdefault(rid, record)

    existing_owners: Set[str] = set()
    for record in raw_summaries:
        if isinstance(record, dict) and isinstance(record.get("owner"), str):
            existing_owners.add(record["owner"])

    required: Dict[str, Dict[str, Any]] = {}
    for owner in active_owners:
        opened, done, points = counters.get(owner, [0, 0, 0])
        required[owner] = {"owner": owner, "open_items": opened,
                           "done_items": done, "total_points": points}

    # 5. Post what the ledger is missing, and only that. Anything the ledger
    # rejects (400/409/422) is a bug in the selection above, so fail loudly
    # rather than misreporting success.
    for owner in sorted(required):
        if owner in existing_owners:
            continue
        body = required[owner]
        response = ledger.post("/v1/summaries", body,
                               {"Idempotency-Key": "sync-%s" % owner})
        if response.status in (200, 201):
            existing_owners.add(owner)
        else:
            raise RuntimeError(
                "POST /v1/summaries for owner %r rejected with status %d"
                % (owner, response.status))

    os.makedirs(out_dir, exist_ok=True)
    _write_jsonl(os.path.join(out_dir, ORPHANS_FILENAME),
                 [json.dumps(record) for record in orphans.values()])
    _write_jsonl(os.path.join(out_dir, ERRORS_FILENAME),
                 [json.dumps(entry) for entry in errors])
    _write_jsonl(os.path.join(out_dir, SUMMARY_FILENAME),
                 [json.dumps(required[owner]) for owner in sorted(required)])
    return 0
