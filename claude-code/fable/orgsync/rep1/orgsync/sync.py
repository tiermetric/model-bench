"""The `sync` command: reconcile the Directory and the Ledger.

One pass reads every page of items, accounts, and existing summaries; decides
which records are well-formed; tallies the well-formed items of each active
account's owner; posts the summaries the Ledger is missing (idempotently, and
only those); and writes three JSONL files under the output directory:

- ``errors.jsonl`` — every malformed record, tagged with the service it came
  from, exactly once.
- ``orphans.jsonl`` — every well-formed item whose owner is not the owner of
  an active, well-formed account, exactly once, as received.
- ``summary.jsonl`` — every summary body this run posted, appended, so a
  repeat run that posts nothing leaves the file untouched.

A 429 from either service is honored by waiting out its ``Retry-After`` and
retrying the same request; it never counts as data and never abandons work.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional, Set

from .config import Config
from .http_client import HttpClient, Response
from .models import Summary

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

ERRORS_FILENAME = "errors.jsonl"
ORPHANS_FILENAME = "orphans.jsonl"
SUMMARY_FILENAME = "summary.jsonl"

#: Idempotency key sent with the summary POST for `owner`.
IDEMPOTENCY_KEY_FORMAT = "sync-%s"

#: Wait applied when a 429 arrives without a usable Retry-After header.
DEFAULT_RETRY_AFTER_SECONDS = 1.0
#: Margin added to every Retry-After wait so clock granularity on either side
#: can never make the retry arrive early.
RETRY_AFTER_MARGIN_SECONDS = 0.05

STATUS_OPEN = "open"
STATUS_DONE = "done"


class SyncError(Exception):
    """A service answered in a way one reconciliation pass cannot absorb."""


def _is_plain_int(value: Any) -> bool:
    """True for a JSON integer: an int that is not a bool."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_wellformed_item(record: Any) -> bool:
    return (isinstance(record, dict)
            and isinstance(record.get("id"), str)
            and isinstance(record.get("owner"), str)
            and record.get("status") in (STATUS_OPEN, STATUS_DONE)
            and _is_plain_int(record.get("points"))
            and record["points"] >= 0)


def _is_wellformed_account(record: Any) -> bool:
    return (isinstance(record, dict)
            and isinstance(record.get("id"), str)
            and isinstance(record.get("owner"), str)
            and isinstance(record.get("active"), bool))


def _is_wellformed_summary(record: Any) -> bool:
    return (isinstance(record, dict)
            and isinstance(record.get("owner"), str)
            and _is_plain_int(record.get("open_items"))
            and _is_plain_int(record.get("done_items"))
            and _is_plain_int(record.get("total_points")))


def _retry_after_seconds(response: Response) -> float:
    value = response.headers.get("Retry-After") if response.headers else None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS
    return max(seconds, 0.0)


def _request_honoring_429(client: HttpClient, method: str, path: str,
                          params: Optional[Dict[str, Any]] = None,
                          payload: Optional[Any] = None,
                          headers: Optional[Dict[str, str]] = None,
                          sleep: Callable[[float], None] = time.sleep) -> Response:
    """Issue one request, waiting out any number of 429s before returning.

    Requests here are sequential, so sleeping before the retry also keeps
    every later request to this service outside the Retry-After window.
    """
    while True:
        response = client.request(method, path, params=params,
                                  payload=payload, headers=headers)
        if response.status != 429:
            return response
        sleep(_retry_after_seconds(response) + RETRY_AFTER_MARGIN_SECONDS)


def _fetch_all(client: HttpClient, path: str, list_key: str,
               sleep: Callable[[float], None]) -> List[Any]:
    """Follow `next_page` from page 1 until null, concatenating `list_key`."""
    records: List[Any] = []
    page: Any = 1
    while True:
        response = _request_honoring_429(client, "GET", path,
                                         params={"page": page}, sleep=sleep)
        if response.status != 200:
            raise SyncError("GET %s?page=%s answered HTTP %d: %s"
                            % (response.url, page, response.status, response.text))
        try:
            body = response.json()
        except ValueError as exc:
            raise SyncError("GET %s: %s" % (response.url, exc)) from exc
        page_records = body.get(list_key) if isinstance(body, dict) else None
        if not isinstance(page_records, list):
            raise SyncError("GET %s: no %r list in response" % (response.url, list_key))
        records.extend(page_records)
        next_page = body.get("next_page")
        if next_page is None:
            return records
        page = next_page


def _write_jsonl(path: str, lines: List[str]) -> None:
    """Replace `path` with `lines`, sorted so identical data means identical
    bytes regardless of the order the services delivered it in."""
    with open(path, "w", encoding="utf-8") as handle:
        for line in sorted(lines):
            handle.write(line + "\n")


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR,
             opener: Optional[Any] = None,
             sleep: Callable[[float], None] = time.sleep) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success). `opener` and `sleep` are
    seams for tests: the opener is handed to both HTTP clients, and `sleep`
    is what waits out a 429's Retry-After.
    """
    directory = HttpClient(config.directory_url, opener=opener)
    ledger = HttpClient(config.ledger_url, opener=opener)

    raw_items = _fetch_all(directory, "/v1/items", "items", sleep)
    raw_accounts = _fetch_all(ledger, "/v1/accounts", "accounts", sleep)
    raw_summaries = _fetch_all(ledger, "/v1/summaries", "summaries", sleep)

    error_lines: List[str] = []
    seen_error_keys: Set[str] = set()

    def record_error(service: str, record: Any) -> None:
        entry = {"service": service, "record": record}
        key = json.dumps(entry, sort_keys=True)
        if key not in seen_error_keys:
            seen_error_keys.add(key)
            error_lines.append(json.dumps(entry))

    # The ledger decides who counts: owners of active, well-formed accounts.
    active_owners: Set[str] = set()
    for record in raw_accounts:
        if _is_wellformed_account(record):
            if record["active"]:
                active_owners.add(record["owner"])
        else:
            record_error("ledger", record)

    # Item ids are unique, so a repeated id is the same item delivered twice.
    items_by_id: Dict[str, Dict[str, Any]] = {}
    for record in raw_items:
        if _is_wellformed_item(record):
            items_by_id.setdefault(record["id"], record)
        else:
            record_error("directory", record)

    existing_summary_owners: Set[str] = set()
    for record in raw_summaries:
        if _is_wellformed_summary(record):
            existing_summary_owners.add(record["owner"])
        else:
            record_error("ledger", record)

    # Tally each active owner's items; anything else well-formed is an orphan.
    tallies: Dict[str, List[int]] = {owner: [0, 0, 0] for owner in active_owners}
    orphan_lines: List[str] = []
    for item in items_by_id.values():
        tally = tallies.get(item["owner"])
        if tally is None:
            orphan_lines.append(json.dumps(item))
        else:
            tally[0 if item["status"] == STATUS_OPEN else 1] += 1
            tally[2] += item["points"]

    required = [Summary(owner, opened, done, points).to_dict()
                for owner, (opened, done, points) in sorted(tallies.items())]

    os.makedirs(out_dir, exist_ok=True)
    _write_jsonl(os.path.join(out_dir, ERRORS_FILENAME), error_lines)
    _write_jsonl(os.path.join(out_dir, ORPHANS_FILENAME), orphan_lines)

    # Post only what the ledger is missing; append each accepted body as it
    # lands so a rerun that posts nothing leaves summary.jsonl unchanged.
    with open(os.path.join(out_dir, SUMMARY_FILENAME), "a", encoding="utf-8") as handle:
        for summary in required:
            if summary["owner"] in existing_summary_owners:
                continue
            response = _request_honoring_429(
                ledger, "POST", "/v1/summaries", payload=summary,
                headers={"Idempotency-Key": IDEMPOTENCY_KEY_FORMAT % summary["owner"]},
                sleep=sleep)
            if response.status not in (200, 201):
                raise SyncError("POST /v1/summaries for %r answered HTTP %d: %s"
                                % (summary["owner"], response.status, response.text))
            handle.write(json.dumps(summary) + "\n")
            handle.flush()
    return 0
