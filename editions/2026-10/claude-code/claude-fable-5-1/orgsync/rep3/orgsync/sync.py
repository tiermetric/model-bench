"""The `sync` command: reconcile the Directory and the Ledger.

One pass does, in order: read every page of accounts, items and existing
summaries; decide which records are well-formed; work out the summary every
active account should have; post the summaries the Ledger lacks; and write
what happened to `out/`. The Ledger decides who counts — only an active,
well-formed account gets a summary — and the Directory decides what is
counted. Everything the run could not use is written down rather than
dropped: items with no active owner go to `orphans.jsonl`, records that are
not well-formed go to `errors.jsonl`.

The pass is idempotent: it only ever posts a summary whose owner has none
yet, so a second run against unchanged services issues no writes and leaves
the output files as they were.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from .config import Config
from .http_client import HttpClient, HttpClientError
from .models import Account, Item, Summary
from .service import Service, ServiceError

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

ORPHANS_FILE = "orphans.jsonl"
ERRORS_FILE = "errors.jsonl"
SUMMARY_FILE = "summary.jsonl"

#: Exit code when a service could not be read or a write was rejected.
EXIT_SYNC_ERROR = 4

ACCOUNTS_PATH = "/v1/accounts"
ITEMS_PATH = "/v1/items"
SUMMARIES_PATH = "/v1/summaries"

ITEM_STATUSES = ("open", "done")

#: Ledger statuses that mean "the write was refused". A 429 is deliberately
#: not here: it is a rate limit, handled underneath by `Service`.
REJECTED_STATUSES = (400, 409, 422)
ACCEPTED_STATUSES = (200, 201)


def idempotency_key(owner: str) -> str:
    return "sync-%s" % owner


# -- validation ---------------------------------------------------------------
#
# JSON types exactly: in Python a bool is an int, so an integer field must
# refuse bools explicitly, and a boolean field must demand the bool type.

def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_account(record: Any) -> Optional[Account]:
    """The `Account` a Ledger record describes, or None if it is malformed."""
    if not isinstance(record, dict):
        return None
    if not (_is_str(record.get("id")) and _is_str(record.get("owner"))
            and isinstance(record.get("active"), bool)):
        return None
    return Account(id=record["id"], owner=record["owner"], active=record["active"])


def parse_item(record: Any) -> Optional[Item]:
    """The `Item` a Directory record describes, or None if it is malformed."""
    if not isinstance(record, dict):
        return None
    if not (_is_str(record.get("id")) and _is_str(record.get("owner"))
            and record.get("status") in ITEM_STATUSES
            and _is_int(record.get("points")) and record["points"] >= 0):
        return None
    return Item(id=record["id"], owner=record["owner"],
                status=record["status"], points=record["points"])


def summary_owner(record: Any) -> Optional[str]:
    """The owner a posted summary is for, or None if it does not name one."""
    if isinstance(record, dict) and _is_str(record.get("owner")):
        return record["owner"]
    return None


# -- reconciliation ------------------------------------------------------------

def canonical(record: Any) -> str:
    """A stable JSON rendering, used to recognise the same record twice."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


@dataclass
class Plan:
    """What one pass worked out before touching the Ledger.

    `required` holds the summary every active account must have, by owner.
    `missing` is the subset the Ledger has no summary for — the owners to
    post, in the order their accounts were delivered. `orphans` and `errors`
    hold raw records, exactly as received, each once.
    """

    required: Dict[str, Summary] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)
    orphans: List[Any] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)


def build_plan(accounts: Iterable[Any], items: Iterable[Any],
               summaries: Iterable[Any]) -> Plan:
    """Classify the raw records and compute every required summary."""
    plan = Plan()
    seen_errors: Set[Tuple[str, str]] = set()

    def record_error(service: str, record: Any) -> None:
        key = (service, canonical(record))
        if key not in seen_errors:
            seen_errors.add(key)
            plan.errors.append({"service": service, "record": record})

    # Accounts: the Ledger decides who counts.
    active_owners: List[str] = []
    for raw in accounts:
        account = parse_account(raw)
        if account is None:
            record_error("ledger", raw)
        elif account.active and account.owner not in plan.required:
            active_owners.append(account.owner)
            plan.required[account.owner] = Summary(owner=account.owner)

    # Items: each counted once, however many times it was delivered.
    seen_item_ids: Set[str] = set()
    for raw in items:
        item = parse_item(raw)
        if item is None:
            record_error("directory", raw)
            continue
        if item.id in seen_item_ids:
            continue
        seen_item_ids.add(item.id)
        current = plan.required.get(item.owner)
        if current is None:
            plan.orphans.append(raw)
            continue
        plan.required[item.owner] = Summary(
            owner=current.owner,
            open_items=current.open_items + (1 if item.status == "open" else 0),
            done_items=current.done_items + (1 if item.status == "done" else 0),
            total_points=current.total_points + item.points)

    # Summaries: post only what the Ledger is missing.
    existing = {owner for owner in (summary_owner(s) for s in summaries)
                if owner is not None}
    plan.missing = [owner for owner in active_owners if owner not in existing]
    return plan


# -- output files -------------------------------------------------------------

def _write_jsonl(path: str, lines: Iterable[str]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line)
            handle.write("\n")
    os.replace(tmp, path)


def _read_jsonl(path: str) -> List[str]:
    """Non-empty lines of `path` that parse as JSON; nothing if it is absent."""
    if not os.path.exists(path):
        return []
    kept: List[str] = []
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                json.loads(line)
            except ValueError:
                continue
            kept.append(line)
    return kept


def _merge_summary_lines(previous: List[str], posted: List[str]) -> List[str]:
    """Previous summary lines plus those posted now, one line per owner.

    `summary.jsonl` records what this tool has posted. A later run posts only
    what the Ledger still lacks, so the file carries forward what earlier
    runs posted and a fresh post for an owner replaces any earlier line.
    """
    by_owner: Dict[str, str] = {}
    unowned: List[str] = []
    for line in previous + posted:
        owner = summary_owner(json.loads(line))
        if owner is None:
            unowned.append(line)
        else:
            by_owner[owner] = line
    return list(by_owner.values()) + unowned


# -- the command --------------------------------------------------------------

def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR,
             clock: Optional[Callable[[], float]] = None,
             sleep: Optional[Callable[[float], None]] = None,
             log: Optional[Callable[[str], None]] = None) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code: 0 on success, EXIT_SYNC_ERROR if a service
    could not be read or the Ledger rejected a write. `clock` and `sleep`
    exist so tests can run the rate-limit handling without real waiting.
    """
    emit = log if log is not None else _stderr
    extra: Dict[str, Any] = {}
    if clock is not None:
        extra["clock"] = clock
    if sleep is not None:
        extra["sleep"] = sleep
    directory = Service("directory", HttpClient(config.directory_url), log=emit, **extra)
    ledger = Service("ledger", HttpClient(config.ledger_url), log=emit, **extra)

    try:
        accounts = ledger.fetch_all(ACCOUNTS_PATH, "accounts")
        items = directory.fetch_all(ITEMS_PATH, "items")
        summaries = ledger.fetch_all(SUMMARIES_PATH, "summaries")
    except (HttpClientError, ServiceError) as exc:
        emit("sync aborted: %s" % exc)
        return EXIT_SYNC_ERROR

    plan = build_plan(accounts, items, summaries)

    posted_lines: List[str] = []
    rejected = 0
    for owner in plan.missing:
        body = plan.required[owner].to_dict()
        try:
            response = ledger.post_json(SUMMARIES_PATH, body,
                                        {"Idempotency-Key": idempotency_key(owner)})
        except (HttpClientError, ServiceError) as exc:
            emit("sync aborted: %s" % exc)
            rejected += 1
            break
        if response.status in ACCEPTED_STATUSES:
            posted_lines.append(json.dumps(body))
        else:
            rejected += 1
            emit("ledger rejected summary for %r with HTTP %d: %s" % (
                owner, response.status, response.text.strip()[:200]))

    os.makedirs(out_dir, exist_ok=True)
    summary_path = os.path.join(out_dir, SUMMARY_FILE)
    _write_jsonl(os.path.join(out_dir, ORPHANS_FILE),
                 (json.dumps(record) for record in plan.orphans))
    _write_jsonl(os.path.join(out_dir, ERRORS_FILE),
                 (json.dumps(record) for record in plan.errors))
    _write_jsonl(summary_path,
                 _merge_summary_lines(_read_jsonl(summary_path), posted_lines))

    emit("accounts: %d active; items: %d counted, %d orphaned; errors: %d; "
         "summaries posted: %d; rate limits honoured: %d" % (
             len(plan.required), sum(s.open_items + s.done_items
                                     for s in plan.required.values()),
             len(plan.orphans), len(plan.errors), len(posted_lines),
             directory.rate_limit_hits + ledger.rate_limit_hits))
    return EXIT_SYNC_ERROR if rejected else 0


def _stderr(message: str) -> None:
    print("orgsync: %s" % message, file=sys.stderr)
