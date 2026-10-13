"""The `sync` command: reconcile the Directory and the Ledger.

One pass does, in order:

1. read every page of accounts, items and existing summaries;
2. decide which accounts count (active and well-formed) and fold each owner's
   well-formed items — each item once, however often it was delivered — into
   the summary the Ledger must hold for them;
3. route everything that does not count: items without an active owner to
   `orphans.jsonl`, records that are not well-formed to `errors.jsonl`;
4. POST only the summaries the Ledger does not have yet, with
   `Idempotency-Key: sync-<owner>`, and record each posted body in
   `summary.jsonl`.

Running it again against unchanged services issues no writes and leaves the
three files as they were: orphans and errors are recomputed from the same
data, and `summary.jsonl` keeps the summaries posted by earlier runs, merged
by owner with anything posted now.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from .config import Config
from .http_client import HttpClient
from .models import Account, Item, Summary
from .service import Service
from .validation import parse_account, parse_item, summary_owner

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

ORPHANS_FILE = "orphans.jsonl"
ERRORS_FILE = "errors.jsonl"
SUMMARY_FILE = "summary.jsonl"

ITEMS_PATH = "/v1/items"
ACCOUNTS_PATH = "/v1/accounts"
SUMMARIES_PATH = "/v1/summaries"

IDEMPOTENCY_KEY_PREFIX = "sync-"

#: Ledger answers that mean the POST was *rejected*; a passing run sends none.
REJECTED_STATUSES = (400, 409, 422)
#: Ledger answers that mean the summary is now held: accepted, or a replay of
#: an identical earlier acceptance.
WRITTEN_STATUSES = (201, 200)

EXIT_OK = 0
EXIT_WRITE_REJECTED = 1


def idempotency_key(owner: str) -> str:
    return IDEMPOTENCY_KEY_PREFIX + owner


def _canonical(record: Any) -> str:
    """A key under which two deliveries of the same record collide."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


@dataclass
class Reconciliation:
    """What one pass decided, before anything is written anywhere."""

    #: owner -> the summary the Ledger must hold, for every active account.
    required: Dict[str, Summary] = field(default_factory=dict)
    #: Raw items (as received) whose owner has no active account; by item id.
    orphans: List[Any] = field(default_factory=list)
    #: `{"service": ..., "record": ...}` lines for records that are not
    #: well-formed, each distinct record once.
    errors: List[Dict[str, Any]] = field(default_factory=list)
    #: Owners that already have a summary in the Ledger.
    existing_owners: Set[str] = field(default_factory=set)

    @property
    def to_post(self) -> List[Summary]:
        """Required summaries the Ledger is missing, in owner order."""
        return [self.required[o] for o in sorted(self.required)
                if o not in self.existing_owners]


def reconcile(raw_accounts: List[Any], raw_items: List[Any],
              raw_summaries: List[Any]) -> Reconciliation:
    """Pure decision step: from the three record lists to a Reconciliation."""
    result = Reconciliation()
    seen_errors: Set[str] = set()

    def record_error(service: str, record: Any) -> None:
        key = service + ":" + _canonical(record)
        if key not in seen_errors:
            seen_errors.add(key)
            result.errors.append({"service": service, "record": record})

    # Accounts: the ledger decides who counts.
    active_owners: Set[str] = set()
    for raw in raw_accounts:
        account: Optional[Account] = parse_account(raw)
        if account is None:
            record_error("ledger", raw)
        elif account.active:
            active_owners.add(account.owner)
    for owner in active_owners:
        result.required[owner] = Summary(owner=owner)

    # Items: each id once; count toward an active owner or become an orphan.
    seen_item_ids: Set[str] = set()
    for raw in raw_items:
        item: Optional[Item] = parse_item(raw)
        if item is None:
            record_error("directory", raw)
            continue
        if item.id in seen_item_ids:
            continue
        seen_item_ids.add(item.id)
        if item.owner not in active_owners:
            result.orphans.append(raw)
            continue
        current = result.required[item.owner]
        result.required[item.owner] = Summary(
            owner=item.owner,
            open_items=current.open_items + (1 if item.status == "open" else 0),
            done_items=current.done_items + (1 if item.status == "done" else 0),
            total_points=current.total_points + item.points)

    # Summaries already held by the ledger.
    for raw in raw_summaries:
        owner = summary_owner(raw)
        if owner is not None:
            result.existing_owners.add(owner)

    return result


# -- output files ------------------------------------------------------------

def _write_jsonl(path: str, records: List[Any]) -> None:
    """Replace `path` with one JSON line per record, atomically."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    os.replace(tmp, path)


def _read_summary_lines(path: str) -> Dict[str, Dict[str, Any]]:
    """owner -> body for each parsable line of an existing summary.jsonl."""
    kept: Dict[str, Dict[str, Any]] = {}
    if not os.path.exists(path):
        return kept
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                body = json.loads(line)
            except ValueError:
                continue
            owner = summary_owner(body)
            if owner is not None:
                kept[owner] = body
    return kept


# -- the command -------------------------------------------------------------

def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR,
             sleep: Callable[[float], None] = time.sleep,
             log: Callable[[str], None] = lambda msg: print(msg, file=sys.stderr)) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success). `sleep` and `log` are
    injectable for tests.
    """
    directory = Service("directory", HttpClient(config.directory_url), sleep=sleep, log=log)
    ledger = Service("ledger", HttpClient(config.ledger_url), sleep=sleep, log=log)

    # 1. Read everything.
    raw_accounts = ledger.fetch_all(ACCOUNTS_PATH, "accounts")
    raw_items = directory.fetch_all(ITEMS_PATH, "items")
    raw_summaries = ledger.fetch_all(SUMMARIES_PATH, "summaries")

    # 2-4. Decide.
    plan = reconcile(raw_accounts, raw_items, raw_summaries)

    # 5. Post what the ledger is missing, and only that.
    os.makedirs(out_dir, exist_ok=True)
    summary_path = os.path.join(out_dir, SUMMARY_FILE)
    posted = _read_summary_lines(summary_path)
    rejected = 0
    for summary in plan.to_post:
        body = summary.to_dict()
        response = ledger.post_json(SUMMARIES_PATH, body,
                                    headers={"Idempotency-Key": idempotency_key(summary.owner)})
        if response.status in WRITTEN_STATUSES:
            posted[summary.owner] = body
        elif response.status in REJECTED_STATUSES:
            rejected += 1
            log("ledger rejected summary for %r with %d: %s"
                % (summary.owner, response.status, response.text.strip()))
        else:
            rejected += 1
            log("ledger answered %d to summary for %r: %s"
                % (response.status, summary.owner, response.text.strip()))

    # Output files. Orphans and errors are a function of the services' data;
    # summary.jsonl accumulates what this tool has posted, one line per owner.
    _write_jsonl(os.path.join(out_dir, ORPHANS_FILE), plan.orphans)
    _write_jsonl(os.path.join(out_dir, ERRORS_FILE), plan.errors)
    _write_jsonl(summary_path, [posted[o] for o in sorted(posted)])

    log("sync: %d active account(s), %d summary(ies) posted, %d orphan(s), %d error(s)"
        % (len(plan.required), len(plan.to_post) - rejected, len(plan.orphans), len(plan.errors)))
    return EXIT_WRITE_REJECTED if rejected else EXIT_OK
