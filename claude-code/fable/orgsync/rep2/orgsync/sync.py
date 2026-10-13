"""The `sync` command: reconcile the Directory and the Ledger.

One pass reads every page of accounts, items, and existing summaries; decides
which records are well-formed; tallies each active owner's items; posts the
summaries the Ledger is missing (idempotently, keyed `sync-<owner>`); and
writes what it saw to `out/`: orphaned items, malformed records, and the
summaries it posted. A 429 from either service pauses requests to that service
for the advertised `Retry-After` and the request is retried — rate limiting is
never data and never abandons the work.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

from .config import Config
from .http_client import HttpClient
from .models import Summary

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

ORPHANS_FILENAME = "orphans.jsonl"
ERRORS_FILENAME = "errors.jsonl"
SUMMARY_FILENAME = "summary.jsonl"

#: Idempotency keys are derived from the owner, so re-posting the same summary
#: is always a replay, never a duplicate.
IDEMPOTENCY_KEY_PREFIX = "sync-"

#: Used when a 429 carries no parseable Retry-After header.
DEFAULT_RETRY_AFTER_SECONDS = 1.0

#: Small pause added on top of Retry-After so clock granularity on either side
#: can never make a retry arrive early.
RETRY_AFTER_SLACK_SECONDS = 0.05


class SyncError(Exception):
    """A service answered in a way sync cannot reconcile (bad page shape,
    unexpected status). Malformed *records* are not errors — they are data."""


def _retry_after_seconds(headers: Any) -> float:
    """Seconds to wait, from a 429's Retry-After header."""
    value = headers.get("Retry-After") if headers is not None else None
    if value is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    try:
        return max(float(str(value).strip()), 0.0)
    except ValueError:
        return DEFAULT_RETRY_AFTER_SECONDS


class _Service:
    """One service plus its rate-limit state.

    Every request funnels through here so a 429's Retry-After holds back the
    *next* request to this service, whatever it is, not just the retry.
    """

    def __init__(self, name: str, client: HttpClient,
                 sleep: Callable[[float], None], clock: Callable[[], float]) -> None:
        self.name = name
        self._client = client
        self._sleep = sleep
        self._clock = clock
        self._resume_at: Optional[float] = None

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        while True:
            if self._resume_at is not None:
                delay = self._resume_at - self._clock()
                if delay > 0:
                    self._sleep(delay)
                self._resume_at = None
            response = self._client.request(method, path, **kwargs)
            if response.status != 429:
                return response
            wait = _retry_after_seconds(response.headers) + RETRY_AFTER_SLACK_SECONDS
            self._resume_at = self._clock() + wait


def _fetch_all(service: _Service, path: str, key: str) -> List[Any]:
    """Follow `next_page` from page 1 until null, returning every record."""
    records: List[Any] = []
    page: Any = 1
    visited = set()
    while page is not None:
        if page in visited:
            raise SyncError("%s: GET %s revisits page %r; refusing to loop"
                            % (service.name, path, page))
        visited.add(page)
        response = service.request("GET", path, params={"page": page})
        if response.status != 200:
            raise SyncError("%s: GET %s?page=%s returned HTTP %d"
                            % (service.name, path, page, response.status))
        payload = response.json()
        batch = payload.get(key) if isinstance(payload, dict) else None
        if not isinstance(batch, list):
            raise SyncError("%s: GET %s?page=%s carried no %r list"
                            % (service.name, path, page, key))
        records.extend(batch)
        page = payload.get("next_page")
    return records


def _is_plain_int(value: Any) -> bool:
    """True for a JSON integer. JSON booleans parse to bool, a subclass of
    int, and are not integers here."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_well_formed_item(record: Any) -> bool:
    return (isinstance(record, dict)
            and isinstance(record.get("id"), str)
            and isinstance(record.get("owner"), str)
            and record.get("status") in ("open", "done")
            and _is_plain_int(record.get("points"))
            and record["points"] >= 0)


def _is_well_formed_account(record: Any) -> bool:
    return (isinstance(record, dict)
            and isinstance(record.get("id"), str)
            and isinstance(record.get("owner"), str)
            and isinstance(record.get("active"), bool))


def _canonical(value: Any) -> str:
    """A key for exactly-once bookkeeping: same JSON content, same key."""
    return json.dumps(value, sort_keys=True)


def _write_jsonl(path: str, records: List[Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def _read_jsonl(path: str) -> List[Any]:
    """Records from an earlier run's file; absent or unparsable lines are
    simply not records."""
    records: List[Any] = []
    if not os.path.exists(path):
        return records
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    return records


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR,
             sleep: Callable[[float], None] = time.sleep,
             clock: Callable[[], float] = time.monotonic) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success). `sleep` and `clock` exist for
    tests; production callers take the defaults.
    """
    directory = _Service("directory", HttpClient(config.directory_url), sleep, clock)
    ledger = _Service("ledger", HttpClient(config.ledger_url), sleep, clock)

    raw_accounts = _fetch_all(ledger, "/v1/accounts", "accounts")
    raw_items = _fetch_all(directory, "/v1/items", "items")
    raw_summaries = _fetch_all(ledger, "/v1/summaries", "summaries")

    errors: List[Dict[str, Any]] = []

    # The ledger decides who counts: only owners of active, well-formed
    # accounts get summaries. A malformed account is not an account.
    active_owners = set()
    for record in raw_accounts:
        if _is_well_formed_account(record):
            if record["active"]:
                active_owners.add(record["owner"])
        else:
            errors.append({"service": "ledger", "record": record})

    # Item ids are unique, so a repeated id is the same item delivered twice:
    # keep the first delivery, count it once.
    items_by_id: Dict[str, Dict[str, Any]] = {}
    for record in raw_items:
        if _is_well_formed_item(record):
            items_by_id.setdefault(record["id"], record)
        else:
            errors.append({"service": "directory", "record": record})

    # counts: owner -> [open_items, done_items, total_points]; an active
    # account with no items keeps its zeros.
    counts: Dict[str, List[int]] = {owner: [0, 0, 0] for owner in active_owners}
    orphans: List[Dict[str, Any]] = []
    for record in items_by_id.values():
        tally = counts.get(record["owner"])
        if tally is None:
            orphans.append(record)
            continue
        tally[0 if record["status"] == "open" else 1] += 1
        tally[2] += record["points"]

    # An owner with any summary already in the ledger needs nothing posted.
    owners_with_summary = set()
    for record in raw_summaries:
        if isinstance(record, dict) and isinstance(record.get("owner"), str):
            owners_with_summary.add(record["owner"])

    posted: List[Dict[str, Any]] = []
    for owner in sorted(counts):
        if owner in owners_with_summary:
            continue
        body = Summary(owner, *counts[owner]).to_dict()
        response = ledger.request(
            "POST", "/v1/summaries", payload=body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY_PREFIX + owner})
        if response.status not in (200, 201):
            raise SyncError("ledger rejected summary for %r: HTTP %d %s"
                            % (owner, response.status, response.text.strip()))
        posted.append(body)

    os.makedirs(out_dir, exist_ok=True)

    # summary.jsonl accumulates every summary this tool has posted: a rerun
    # that posts nothing must leave the file as it was.
    summary_path = os.path.join(out_dir, SUMMARY_FILENAME)
    recorded = _read_jsonl(summary_path)
    seen = {_canonical(record) for record in recorded}
    for body in posted:
        key = _canonical(body)
        if key not in seen:
            seen.add(key)
            recorded.append(body)
    _write_jsonl(summary_path, recorded)

    # Malformed records exactly once each, however often they were delivered.
    unique_errors: List[Dict[str, Any]] = []
    seen = set()
    for entry in errors:
        key = _canonical(entry)
        if key not in seen:
            seen.add(key)
            unique_errors.append(entry)

    _write_jsonl(os.path.join(out_dir, ORPHANS_FILENAME), orphans)
    _write_jsonl(os.path.join(out_dir, ERRORS_FILENAME), unique_errors)
    return 0
