"""The `sync` command: reconcile the Directory and the Ledger.

One pass reads every page of accounts, items and existing summaries, decides
what each record means in `reconcile` (a pure function, so the rules can be read
and tested without a network), POSTs the summaries the Ledger is missing, and
writes the output files. Malformed records are skipped and reported; they never
enter a count.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Set, Tuple

from .config import Config
from .http_client import HttpClient
from .models import Summary
from .service import ServiceClient, ServiceError, fetch_all
from .validate import (OPEN, is_well_formed_account, is_well_formed_item,
                       is_well_formed_summary)

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

ORPHANS_FILE = "orphans.jsonl"
ERRORS_FILE = "errors.jsonl"
SUMMARY_FILE = "summary.jsonl"

#: Exit code for a sync that could not finish: a service stayed unusable, or
#: the Ledger rejected a write. 0 is success; see also __main__.EXIT_CONFIG_ERROR.
EXIT_SYNC_FAILED = 4

IDEMPOTENCY_HEADER = "Idempotency-Key"
#: 201 is a new acceptance; 200 is a replay of an earlier accepted post. Both
#: mean the Ledger holds this summary.
ACCEPTED_STATUSES = (200, 201)


@dataclass
class Reconciliation:
    """What one pass decided, before anything is sent or written."""

    #: One summary per active, well-formed account, sorted by owner.
    required: List[Summary]
    #: The required summaries whose owner has no summary in the Ledger yet.
    missing: List[Summary]
    #: Well-formed items with no active owner, each as received, once per id.
    orphans: List[Dict[str, Any]]
    #: `{"service": ..., "record": ...}` entries, each malformed record once.
    errors: List[Dict[str, Any]]


def reconcile(accounts: Iterable[Any], items: Iterable[Any],
              summaries: Iterable[Any]) -> Reconciliation:
    """Decide the summaries, orphans and errors for one pass.

    The Ledger decides who counts: only active, well-formed accounts get a
    summary. An item is counted once however many times it was delivered
    (keyed by id), and only when its owner is such an account; otherwise it is
    an orphan. A summary already in the Ledger for an owner, even one that is
    malformed, means that owner is not missing one.
    """
    errors: List[Dict[str, Any]] = []
    seen_errors: Set[str] = set()

    def reject(service: str, record: Any) -> None:
        entry = {"service": service, "record": record}
        key = _canonical(entry)
        if key not in seen_errors:
            seen_errors.add(key)
            errors.append(entry)

    active: Set[str] = set()
    for record in accounts:
        if not is_well_formed_account(record):
            reject("ledger", record)
        elif record["active"]:
            active.add(record["owner"])

    items_by_id: Dict[str, Dict[str, Any]] = {}
    for record in items:
        if not is_well_formed_item(record):
            reject("directory", record)
        else:
            items_by_id.setdefault(record["id"], record)

    owners_with_summary: Set[str] = set()
    for record in summaries:
        if not isinstance(record, dict) or not isinstance(record.get("owner"), str):
            reject("ledger", record)
            continue
        owners_with_summary.add(record["owner"])
        if not is_well_formed_summary(record):
            reject("ledger", record)

    # Per owner: [open_items, done_items, total_points].
    tallies: Dict[str, List[int]] = {owner: [0, 0, 0] for owner in active}
    orphans: List[Dict[str, Any]] = []
    for record in items_by_id.values():
        if record["owner"] not in active:
            orphans.append(record)
            continue
        tally = tallies[record["owner"]]
        tally[0 if record["status"] == OPEN else 1] += 1
        tally[2] += record["points"]

    required = [Summary(owner=owner, open_items=t[0], done_items=t[1], total_points=t[2])
                for owner, t in sorted(tallies.items())]
    missing = [s for s in required if s.owner not in owners_with_summary]
    return Reconciliation(required=required, missing=missing,
                          orphans=orphans, errors=errors)


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code: 0 on success, `EXIT_SYNC_FAILED` if a service
    stayed unusable or the Ledger rejected a write.
    """
    directory = ServiceClient("directory", HttpClient(config.directory_url))
    ledger = ServiceClient("ledger", HttpClient(config.ledger_url))
    try:
        accounts = fetch_all(ledger, "/v1/accounts", "accounts")
        items = fetch_all(directory, "/v1/items", "items")
        summaries = fetch_all(ledger, "/v1/summaries", "summaries")
    except ServiceError as exc:
        print("orgsync: %s" % exc, file=sys.stderr)
        return EXIT_SYNC_FAILED

    plan = reconcile(accounts, items, summaries)
    posted, problems = post_missing(ledger, plan.missing)

    os.makedirs(out_dir, exist_ok=True)
    write_outputs(out_dir, plan, posted)

    for problem in problems:
        print("orgsync: %s" % problem, file=sys.stderr)
    print("orgsync: %d active owners, %d summaries posted, %d orphans, %d malformed records"
          % (len(plan.required), len(posted), len(plan.orphans), len(plan.errors)))
    return EXIT_SYNC_FAILED if problems else 0


def post_missing(ledger: ServiceClient,
                 missing: Iterable[Summary]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """POST each missing summary. Returns (the bodies the Ledger accepted, problems).

    A rejected POST (400/409/422) is recorded as a problem and the loop moves
    on. A service that stays unusable stops the loop, because nothing after it
    can succeed either. Partial progress is returned, so the caller can still
    record what was posted.
    """
    posted: List[Dict[str, Any]] = []
    problems: List[str] = []
    for summary in missing:
        body = summary.to_dict()
        try:
            response = ledger.request("POST", "/v1/summaries", payload=body,
                                      headers={IDEMPOTENCY_HEADER: "sync-" + summary.owner})
        except ServiceError as exc:
            problems.append(str(exc))
            break
        if response.status in ACCEPTED_STATUSES:
            posted.append(body)
        else:
            problems.append("ledger rejected the summary for %r with HTTP %d"
                            % (summary.owner, response.status))
    return posted, problems


def write_outputs(out_dir: str, plan: Reconciliation, posted: List[Dict[str, Any]]) -> None:
    """Write the three output files under `out_dir`.

    orphans.jsonl and errors.jsonl describe the current state, so they are
    rewritten. summary.jsonl records the summaries this tool has posted, so new
    posts are merged into what is already there (one line per owner). A run that
    posts nothing therefore leaves it unchanged.
    """
    _write_lines(os.path.join(out_dir, ORPHANS_FILE), plan.orphans)
    _write_lines(os.path.join(out_dir, ERRORS_FILE), plan.errors)
    _merge_summary_lines(os.path.join(out_dir, SUMMARY_FILE), posted)


def _merge_summary_lines(path: str, posted: List[Dict[str, Any]]) -> None:
    by_owner: Dict[str, Dict[str, Any]] = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and isinstance(entry.get("owner"), str):
                    by_owner.setdefault(entry["owner"], entry)
    for body in posted:
        by_owner.setdefault(body["owner"], body)
    _write_lines(path, by_owner.values())


def _write_lines(path: str, records: Iterable[Any]) -> None:
    """Replace `path` atomically with one JSON line per record."""
    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True)
