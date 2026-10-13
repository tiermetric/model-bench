"""The `sync` command: reconcile the Directory and the Ledger.

One pass reads every page of items, accounts, and existing summaries; decides,
per active well-formed account, what the owner's summary must say; posts the
summaries the Ledger is missing (and only those); and writes what it saw to
`out/`: orphaned items, malformed records, and the summaries it posted.

Well-formedness is decided here and nowhere else. A malformed record is
skipped, recorded once in `errors.jsonl`, and never enters any count — a
malformed account in particular is not an account, so its owner is not
thereby active.

Rate limits are honored per service: after a 429 the service gets no further
request until its `Retry-After` has elapsed, then the same request is retried.
A 429 is never data and never abandons the work.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Set, Tuple

from .config import Config
from .http_client import HttpClient, Response

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

ORPHANS_FILENAME = "orphans.jsonl"
ERRORS_FILENAME = "errors.jsonl"
SUMMARY_FILENAME = "summary.jsonl"

#: Wait when a 429 carries no usable Retry-After. The spec promises the
#: header, so this is a safety net, not a tuning knob.
DEFAULT_RETRY_AFTER_SECONDS = 1.0

IDEMPOTENCY_KEY_PREFIX = "sync-"


class SyncError(Exception):
    """A service answered in a way sync cannot reconcile (bad page shape,
    unexpected status, or a rejected write)."""


# ---------------------------------------------------------------------------
# Well-formedness
# ---------------------------------------------------------------------------

def _is_int(value: Any) -> bool:
    """A JSON integer: bool is a subclass of int in Python but not an int
    in JSON, so it does not count."""
    return isinstance(value, int) and not isinstance(value, bool)


def is_well_formed_item(record: Any) -> bool:
    """`{"id": str, "owner": str, "status": "open"|"done", "points": int >= 0}`.
    Extra fields are ignored."""
    return (isinstance(record, dict)
            and isinstance(record.get("id"), str)
            and isinstance(record.get("owner"), str)
            and record.get("status") in ("open", "done")
            and _is_int(record.get("points"))
            and record["points"] >= 0)


def is_well_formed_account(record: Any) -> bool:
    """`{"id": str, "owner": str, "active": bool}`. Extra fields are ignored."""
    return (isinstance(record, dict)
            and isinstance(record.get("id"), str)
            and isinstance(record.get("owner"), str)
            and isinstance(record.get("active"), bool))


def is_well_formed_summary(record: Any) -> bool:
    """The shape the Ledger accepts on POST: `owner` a str and the three
    counters non-negative ints. Extra fields are ignored."""
    return (isinstance(record, dict)
            and isinstance(record.get("owner"), str)
            and all(_is_int(record.get(key)) and record[key] >= 0
                    for key in ("open_items", "done_items", "total_points")))


# ---------------------------------------------------------------------------
# Talking to a service
# ---------------------------------------------------------------------------

class ServiceReader:
    """Requests against one service, honoring its rate limit.

    On a 429 it waits the response's `Retry-After` and retries the same
    request; any other status is returned to the caller. `sleep` is
    injectable so tests need not wait in real time.
    """

    def __init__(self, client: HttpClient,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.client = client
        self._sleep = sleep

    def request(self, method: str, path: str, **kwargs: Any) -> Response:
        while True:
            response = self.client.request(method, path, **kwargs)
            if response.status != 429:
                return response
            self._sleep(_retry_after_seconds(response))

    def fetch_all(self, path: str, key: str) -> List[Any]:
        """Follow `next_page` from page 1 until null, returning every record
        under `key`, in delivery order."""
        records: List[Any] = []
        page: Any = 1
        while True:
            response = self.request("GET", path, params={"page": page})
            if response.status != 200:
                raise SyncError("GET %s?page=%s answered %d"
                                % (path, page, response.status))
            try:
                body = response.json()
            except ValueError as exc:
                raise SyncError("GET %s?page=%s: %s" % (path, page, exc)) from exc
            if not isinstance(body, dict) or not isinstance(body.get(key), list):
                raise SyncError("GET %s?page=%s: no %r list in response"
                                % (path, page, key))
            records.extend(body[key])
            page = body.get("next_page")
            if page is None:
                return records


def _retry_after_seconds(response: Response) -> float:
    """Seconds a 429 asks us to wait; never negative."""
    raw = response.headers.get("Retry-After") if response.headers else None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS
    return max(seconds, 0.0)


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def _canonical(record: Any) -> str:
    """A key that identifies a record by content, whatever its key order."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


def reconcile(items: List[Any], accounts: List[Any], summaries: List[Any]
              ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Pure decision step: from the three record streams, compute

    - the summaries to POST (owners the Ledger is missing, sorted by owner),
    - the orphaned items (each once, as received),
    - the malformed records (each once, tagged with its service).
    """
    errors: List[Dict[str, Any]] = []
    seen_errors: Set[str] = set()

    def record_error(service: str, record: Any) -> None:
        key = service + ":" + _canonical(record)
        if key not in seen_errors:
            seen_errors.add(key)
            errors.append({"service": service, "record": record})

    active_owners: Set[str] = set()
    for record in accounts:
        if is_well_formed_account(record):
            if record["active"]:
                active_owners.add(record["owner"])
        else:
            record_error("ledger", record)

    # Items: first delivery of an id wins; later deliveries are the same item.
    items_by_id: Dict[str, Dict[str, Any]] = {}
    for record in items:
        if is_well_formed_item(record):
            items_by_id.setdefault(record["id"], record)
        else:
            record_error("directory", record)

    owners_with_summary: Set[str] = set()
    for record in summaries:
        if is_well_formed_summary(record):
            owners_with_summary.add(record["owner"])
        else:
            record_error("ledger", record)

    counts: Dict[str, Dict[str, int]] = {
        owner: {"open_items": 0, "done_items": 0, "total_points": 0}
        for owner in active_owners}
    orphans: List[Dict[str, Any]] = []
    for item_id in sorted(items_by_id):
        item = items_by_id[item_id]
        tally = counts.get(item["owner"])
        if tally is None:
            orphans.append(item)
            continue
        tally["open_items" if item["status"] == "open" else "done_items"] += 1
        tally["total_points"] += item["points"]

    to_post = [
        {"owner": owner, "open_items": tally["open_items"],
         "done_items": tally["done_items"], "total_points": tally["total_points"]}
        for owner, tally in sorted(counts.items())
        if owner not in owners_with_summary]
    return to_post, orphans, errors


# ---------------------------------------------------------------------------
# Output files
# ---------------------------------------------------------------------------

def _write_jsonl(path: str, records: List[Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def _read_jsonl(path: str) -> List[Any]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR,
             sleep: Callable[[float], None] = time.sleep) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    directory = ServiceReader(HttpClient(config.directory_url), sleep)
    ledger = ServiceReader(HttpClient(config.ledger_url), sleep)

    items = directory.fetch_all("/v1/items", "items")
    accounts = ledger.fetch_all("/v1/accounts", "accounts")
    summaries = ledger.fetch_all("/v1/summaries", "summaries")

    to_post, orphans, errors = reconcile(items, accounts, summaries)

    posted: List[Dict[str, Any]] = []
    for body in to_post:
        response = ledger.request(
            "POST", "/v1/summaries", payload=body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY_PREFIX + body["owner"]})
        if response.status not in (200, 201):
            raise SyncError("POST /v1/summaries for %r rejected with %d: %s"
                            % (body["owner"], response.status, response.text))
        posted.append(body)

    os.makedirs(out_dir, exist_ok=True)
    _write_jsonl(os.path.join(out_dir, ORPHANS_FILENAME), orphans)
    _write_jsonl(os.path.join(out_dir, ERRORS_FILENAME), errors)

    # summary.jsonl accumulates what this tool has posted, so a re-run that
    # posts nothing leaves the file's content unchanged (idempotency). A
    # fresh post for an owner supersedes any earlier recorded line.
    summary_path = os.path.join(out_dir, SUMMARY_FILENAME)
    recorded = {entry["owner"]: entry for entry in _read_jsonl(summary_path)
                if isinstance(entry, dict) and isinstance(entry.get("owner"), str)}
    for body in posted:
        recorded[body["owner"]] = body
    _write_jsonl(summary_path, [recorded[owner] for owner in sorted(recorded)])
    return 0
