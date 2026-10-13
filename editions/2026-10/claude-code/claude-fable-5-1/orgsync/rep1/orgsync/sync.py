"""The `sync` command: reconcile the Directory and the Ledger.

One pass does, in order:

1. Read every page of accounts and existing summaries from the Ledger and
   every page of items from the Directory.
2. Decide, from those records alone, what the world should look like
   (`reconcile`): one summary per active well-formed account, the orphan
   items, and the malformed records. This step is pure so it can be tested
   without a socket.
3. POST the summaries the Ledger does not have yet, each under the
   idempotency key `sync-<owner>`, waiting out any rate limit.
4. Write `orphans.jsonl`, `errors.jsonl` and `summary.jsonl` under the output
   directory.

The run is idempotent: a second pass against unchanged services finds every
summary already present, sends no write, and rewrites the same files.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import Config
from .models import Summary
from .service import Service, ServiceError, make_service
from .validate import validate_account, validate_item

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

ORPHANS_FILE = "orphans.jsonl"
ERRORS_FILE = "errors.jsonl"
SUMMARY_FILE = "summary.jsonl"

DIRECTORY = "directory"
LEDGER = "ledger"

ACCOUNTS_PATH = "/v1/accounts"
ITEMS_PATH = "/v1/items"
SUMMARIES_PATH = "/v1/summaries"

IDEMPOTENCY_KEY_PREFIX = "sync-"

#: Exit code when the ledger rejected a write or a service could not be read.
EXIT_SYNC_FAILED = 1

#: Statuses with which the ledger *accepts* a summary post.
ACCEPTED_POST_STATUSES = (200, 201)
#: Statuses with which the ledger *rejects* a summary post.
REJECTED_POST_STATUSES = (400, 409, 422)


def idempotency_key(owner: str) -> str:
    return IDEMPOTENCY_KEY_PREFIX + owner


def canonical(record: Any) -> str:
    """A stable text form of a JSON value, used to tell records apart."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


@dataclass
class Plan:
    """What one reconciliation decided, before anything is written."""

    #: owner -> required summary, for every active well-formed account.
    summaries: Dict[str, Summary] = field(default_factory=dict)
    #: Well-formed items with no active owner, keyed by item id, as received.
    orphans: Dict[str, Any] = field(default_factory=dict)
    #: Malformed records as `{"service": ..., "record": ...}`, keyed by canonical text.
    errors: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def add_error(self, service: str, record: Any) -> None:
        entry = {"service": service, "record": record}
        self.errors.setdefault(canonical(entry), entry)


def reconcile(accounts: Iterable[Any], items: Iterable[Any]) -> Plan:
    """Decide summaries, orphans and errors from raw account and item records.

    Each raw record is taken exactly as the service delivered it. Items are
    counted once per id however many times they arrive; a malformed record is
    reported once however many times it arrives. A malformed account is not
    an account, so its owner is not active and that owner's items are orphans.
    """
    plan = Plan()

    active_owners: set = set()
    seen_account_ids: set = set()
    for raw in accounts:
        account = validate_account(raw)
        if account is None:
            plan.add_error(LEDGER, raw)
            continue
        if account.id in seen_account_ids:
            continue
        seen_account_ids.add(account.id)
        if account.active:
            active_owners.add(account.owner)

    counts: Dict[str, List[int]] = {owner: [0, 0, 0] for owner in active_owners}
    seen_item_ids: set = set()
    for raw in items:
        item = validate_item(raw)
        if item is None:
            plan.add_error(DIRECTORY, raw)
            continue
        if item.id in seen_item_ids:
            continue
        seen_item_ids.add(item.id)
        if item.owner not in active_owners:
            plan.orphans[item.id] = raw
            continue
        tally = counts[item.owner]
        if item.status == "open":
            tally[0] += 1
        else:
            tally[1] += 1
        tally[2] += item.points

    for owner, (open_items, done_items, total_points) in counts.items():
        plan.summaries[owner] = Summary(owner=owner, open_items=open_items,
                                        done_items=done_items, total_points=total_points)
    return plan


def owners_with_summaries(summaries: Iterable[Any]) -> set:
    """Owners that already have a summary in the ledger, from raw records."""
    owners = set()
    for raw in summaries:
        if isinstance(raw, dict) and isinstance(raw.get("owner"), str):
            owners.add(raw["owner"])
    return owners


@dataclass
class PostOutcome:
    posted: List[Dict[str, Any]] = field(default_factory=list)
    rejected: List[Tuple[Dict[str, Any], int, str]] = field(default_factory=list)


def post_missing_summaries(ledger: Service, required: Dict[str, Summary],
                           existing_owners: set) -> PostOutcome:
    """POST every required summary whose owner has none in the ledger yet."""
    outcome = PostOutcome()
    for owner in sorted(required):
        if owner in existing_owners:
            continue
        body = required[owner].to_dict()
        response = ledger.post_json(SUMMARIES_PATH, body,
                                    {"Idempotency-Key": idempotency_key(owner)})
        if response.status in ACCEPTED_POST_STATUSES:
            outcome.posted.append(body)
        else:
            outcome.rejected.append((body, response.status, response.text))
    return outcome


# -- output files ----------------------------------------------------------


def write_jsonl(path: str, records: Iterable[Any]) -> None:
    """Write `records` as JSON lines, replacing `path` atomically."""
    directory = os.path.dirname(path) or "."
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory,
                                         prefix=".tmp-", suffix=".jsonl", delete=False)
    try:
        with handle:
            for record in records:
                handle.write(json.dumps(record))
                handle.write("\n")
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def read_jsonl(path: str) -> List[Any]:
    """Parse the JSON lines in `path`; a missing file is an empty list and a
    line that is not JSON is skipped."""
    if not os.path.exists(path):
        return []
    records = []
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


def merge_summary_records(previous: Iterable[Any], posted: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Summaries to record: everything this tool has posted so far.

    `summary.jsonl` lists the summaries the tool posted, one line each. A
    later run that posts nothing keeps the file as it was, so re-running
    against unchanged services leaves it unchanged; a run that posts for an
    owner already listed replaces that owner's line.
    """
    by_owner: Dict[str, Dict[str, Any]] = {}
    for record in previous:
        if isinstance(record, dict) and isinstance(record.get("owner"), str):
            by_owner[record["owner"]] = record
    for record in posted:
        by_owner[record["owner"]] = record
    return [by_owner[owner] for owner in sorted(by_owner)]


def write_outputs(out_dir: str, plan: Plan, posted: List[Dict[str, Any]]) -> None:
    os.makedirs(out_dir, exist_ok=True)
    orphans_path = os.path.join(out_dir, ORPHANS_FILE)
    errors_path = os.path.join(out_dir, ERRORS_FILE)
    summary_path = os.path.join(out_dir, SUMMARY_FILE)

    write_jsonl(orphans_path, (plan.orphans[item_id] for item_id in sorted(plan.orphans)))
    write_jsonl(errors_path, (plan.errors[key] for key in sorted(plan.errors)))
    write_jsonl(summary_path, merge_summary_records(read_jsonl(summary_path), posted))


# -- the command -----------------------------------------------------------


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR,
             directory: Optional[Service] = None,
             ledger: Optional[Service] = None) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success). `directory` and `ledger`
    may be supplied to bypass `config`, for tests.
    """
    directory = directory or make_service(DIRECTORY, config.directory_url)
    ledger = ledger or make_service(LEDGER, config.ledger_url)

    try:
        accounts = list(ledger.iter_pages(ACCOUNTS_PATH, "accounts"))
        existing = owners_with_summaries(ledger.iter_pages(SUMMARIES_PATH, "summaries"))
        items = list(directory.iter_pages(ITEMS_PATH, "items"))
    except ServiceError as exc:
        print("orgsync: %s" % exc, file=sys.stderr)
        return EXIT_SYNC_FAILED

    plan = reconcile(accounts, items)

    try:
        outcome = post_missing_summaries(ledger, plan.summaries, existing)
    except ServiceError as exc:
        print("orgsync: %s" % exc, file=sys.stderr)
        return EXIT_SYNC_FAILED

    write_outputs(out_dir, plan, outcome.posted)

    print("orgsync: %d account(s) active, %d item(s) read, %d orphan(s), %d malformed, "
          "%d summary(ies) posted, %d already present"
          % (len(plan.summaries), len(items), len(plan.orphans), len(plan.errors),
             len(outcome.posted), len(plan.summaries) - len(outcome.posted)))
    for body, status, text in outcome.rejected:
        print("orgsync: ledger rejected summary for %r with %d: %s"
              % (body["owner"], status, text.strip()), file=sys.stderr)
    return EXIT_SYNC_FAILED if outcome.rejected else 0


__all__ = ["DEFAULT_OUT_DIR", "Plan", "reconcile", "run_sync"]
