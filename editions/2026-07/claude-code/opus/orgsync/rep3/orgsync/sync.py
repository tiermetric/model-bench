"""The `sync` command: reconcile the Directory and the Ledger.

The shape of one run:

1. Read every account, every work item and every summary already posted.
2. Decide who counts. The Ledger decides: the owner of an active, well-formed
   account, and nobody else.
3. Tally each such owner's well-formed items — each distinct item id once.
4. Sort the leftovers. An item whose owner is not one of those owners is an
   orphan; a record of either service that is not well-formed is an error.
   Neither ever enters a count, and neither ever stops the run.
5. Post the summaries the Ledger does not have yet, and only those.
6. Write what happened to `out/`.

Two things about this file are load-bearing. First, validation lives here
rather than in `models`, because well-formedness is a rule of the
reconciliation, not a property of a container. Second, every collection is
keyed — items by id, errors by their exact content — so a record delivered
twice is written once, which is what makes a run reproducible.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import Config
from .http_client import HttpClient
from .models import Account, Item, Summary
from .service import Service

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

ORPHANS_FILENAME = "orphans.jsonl"
ERRORS_FILENAME = "errors.jsonl"
SUMMARY_FILENAME = "summary.jsonl"

ACCOUNTS_PATH = "/v1/accounts"
ITEMS_PATH = "/v1/items"
SUMMARIES_PATH = "/v1/summaries"

DIRECTORY = "directory"
LEDGER = "ledger"

#: The only two statuses a well-formed item may carry.
OPEN, DONE = "open", "done"
ITEM_STATUSES = (OPEN, DONE)

#: Prefix of the `Idempotency-Key` a summary is posted under. The key is
#: derived from the owner alone, so re-posting the same owner's summary — in a
#: later run, or after a crash mid-run — is a replay rather than a new write.
IDEMPOTENCY_KEY_PREFIX = "sync-"

#: Statuses a POST /v1/summaries may answer with and still have been accepted:
#: 201 is a new summary, 200 is a replay of one already accepted.
POST_ACCEPTED_STATUSES = (200, 201)

#: Exit code when the Ledger refused a write. A correct run never provokes one.
EXIT_WRITE_REJECTED = 4


# --------------------------------------------------------------------------
# Validation: what makes a record usable
# --------------------------------------------------------------------------

def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_int(value: Any) -> bool:
    """A JSON integer. `True` is an `int` to Python and is not one here."""
    return isinstance(value, int) and not isinstance(value, bool)


def parse_item(raw: Any) -> Optional[Item]:
    """`raw` as an `Item`, or None if it is not a well-formed item.

    Well-formed is `{"id": str, "owner": str, "status": "open"|"done",
    "points": int >= 0}`, with the JSON types exactly. Extra fields are fine.
    """
    if not isinstance(raw, dict):
        return None
    id_, owner = raw.get("id"), raw.get("owner")
    status, points = raw.get("status"), raw.get("points")
    if not (_is_str(id_) and _is_str(owner)):
        return None
    if not _is_str(status) or status not in ITEM_STATUSES:
        return None
    if not _is_int(points) or points < 0:
        return None
    return Item(id=id_, owner=owner, status=status, points=points)


def parse_account(raw: Any) -> Optional[Account]:
    """`raw` as an `Account`, or None if it is not a well-formed account.

    Well-formed is `{"id": str, "owner": str, "active": bool}`. A record that
    fails this is not an account at all: it cannot make its owner active.
    """
    if not isinstance(raw, dict):
        return None
    id_, owner, active = raw.get("id"), raw.get("owner"), raw.get("active")
    if not (_is_str(id_) and _is_str(owner) and isinstance(active, bool)):
        return None
    return Account(id=id_, owner=owner, active=active)


def summary_owner(raw: Any) -> Optional[str]:
    """The owner a posted summary belongs to, or None if it names no owner.

    Deliberately forgiving: this is only ever used to answer "does the Ledger
    already hold a summary for this owner?", and the safe answer for a summary
    we cannot fully read is to leave that owner alone rather than post over it.
    """
    if isinstance(raw, dict) and _is_str(raw.get("owner")):
        return raw["owner"]
    return None


# --------------------------------------------------------------------------
# The reconciliation
# --------------------------------------------------------------------------

class _Dedup:
    """Keeps records in arrival order, one per key; first occurrence wins."""

    def __init__(self) -> None:
        self._records: Dict[str, Any] = {}

    def add(self, key: str, record: Any) -> None:
        self._records.setdefault(key, record)

    def records(self) -> List[Any]:
        return list(self._records.values())

    def __len__(self) -> int:
        return len(self._records)


def _content_key(record: Any) -> str:
    """A stable key for an arbitrary JSON record, used to write it once."""
    return json.dumps(record, sort_keys=True, default=repr)


def _error(service: str, raw: Any) -> Dict[str, Any]:
    """The error line for a malformed record. Keyed by the whole line, since
    the same junk arriving from both services is two distinct errors."""
    return {"service": service, "record": raw}


class SyncResult:
    """What one reconciliation decided, before any of it is written down."""

    __slots__ = ("required", "posted", "recorded", "orphans", "errors",
                 "rejections")

    def __init__(self, required: List[Summary], posted: List[Summary],
                 recorded: List[Summary], orphans: List[Any],
                 errors: List[Dict[str, Any]],
                 rejections: Optional[List["Rejection"]] = None) -> None:
        #: One per active, well-formed account, owner-sorted.
        self.required = required
        #: The subset of `required` this run actually sent to the Ledger.
        self.posted = posted
        #: The summaries this tool has put in the Ledger — those posted just
        #: now plus those a previous run posted and the Ledger still holds
        #: unchanged. This is what `summary.jsonl` records; see `write_outputs`.
        self.recorded = recorded
        #: Well-formed items with no active owner, as received.
        self.orphans = orphans
        #: `{"service": ..., "record": ...}` for every malformed record.
        self.errors = errors
        #: Writes the Ledger refused. Empty on a correct run.
        self.rejections = rejections or []


def reconcile(directory: Service, ledger: Service) -> SyncResult:
    """Read both services, work out the summaries, post the missing ones."""
    raw_accounts = ledger.fetch_all(ACCOUNTS_PATH, "accounts")
    raw_items = directory.fetch_all(ITEMS_PATH, "items")
    raw_summaries = ledger.fetch_all(SUMMARIES_PATH, "summaries")

    errors = _Dedup()
    active_owners = _active_owners(raw_accounts, errors)
    items = _distinct_items(raw_items, errors)

    required, orphans = _tally(items, active_owners)
    existing = _summaries_by_owner(raw_summaries)
    missing = [summary for summary in required if summary.owner not in existing]
    posted, rejections = _post_summaries(ledger, missing)

    posted_owners = {summary.owner for summary in posted}
    recorded = [summary for summary in required
                if summary.owner in posted_owners
                or _matches(existing.get(summary.owner), summary)]
    return SyncResult(required, posted, recorded, orphans,
                      errors.records(), rejections)


def _summaries_by_owner(raw_summaries: Iterable[Any]) -> Dict[str, Any]:
    """The summaries the Ledger already holds, keyed by owner."""
    existing: Dict[str, Any] = {}
    for raw in raw_summaries:
        owner = summary_owner(raw)
        if owner is not None:
            existing.setdefault(owner, raw)
    return existing


def _matches(raw: Any, summary: Summary) -> bool:
    """Is `raw` the summary this run would have posted for that owner?

    Used to tell a summary this tool posted on an earlier run — the Ledger
    hands it straight back — from one that arrived some other way.
    """
    if not isinstance(raw, dict):
        return False
    for field, expected in summary.to_dict().items():
        value = raw.get(field)
        if isinstance(expected, int) and not _is_int(value):
            return False
        if value != expected:
            return False
    return True


def _active_owners(raw_accounts: Iterable[Any], errors: _Dedup) -> set:
    """Owners of active, well-formed accounts; malformed ones become errors."""
    owners = set()
    for raw in raw_accounts:
        account = parse_account(raw)
        if account is None:
            record = _error(LEDGER, raw)
            errors.add(_content_key(record), record)
        elif account.active:
            owners.add(account.owner)
    return owners


def _distinct_items(raw_items: Iterable[Any],
                    errors: _Dedup) -> List[Tuple[Item, Any]]:
    """Well-formed items, one per id, paired with the record as received.

    The same id delivered twice is the same item twice, so the second delivery
    is dropped rather than counted again.
    """
    by_id: Dict[str, Tuple[Item, Any]] = {}
    for raw in raw_items:
        item = parse_item(raw)
        if item is None:
            record = _error(DIRECTORY, raw)
            errors.add(_content_key(record), record)
        else:
            by_id.setdefault(item.id, (item, raw))
    return list(by_id.values())


def _tally(items: Iterable[Tuple[Item, Any]],
           active_owners: Iterable[str]) -> Tuple[List[Summary], List[Any]]:
    """Summaries for the active owners, plus the items that belong to none.

    Every active owner gets a summary, including one with no items at all —
    the accounts decide which summaries exist, the items only fill them in.
    """
    counts: Dict[str, List[int]] = {owner: [0, 0, 0] for owner in active_owners}
    orphans: List[Any] = []
    for item, raw in items:
        tally = counts.get(item.owner)
        if tally is None:
            orphans.append(raw)
            continue
        tally[0 if item.status == OPEN else 1] += 1
        tally[2] += item.points
    required = [Summary(owner=owner, open_items=tally[0],
                        done_items=tally[1], total_points=tally[2])
                for owner, tally in sorted(counts.items())]
    return required, orphans


class Rejection:
    """A summary the Ledger refused, kept so the run can report which."""

    __slots__ = ("summary", "status", "body")

    def __init__(self, summary: Summary, status: int, body: str) -> None:
        self.summary = summary
        self.status = status
        self.body = body

    def __str__(self) -> str:
        return ("ledger rejected the summary for %r with %d: %s"
                % (self.summary.owner, self.status, self.body.strip()[:200]))


def _post_summaries(
        ledger: Service,
        summaries: List[Summary]) -> Tuple[List[Summary], List[Rejection]]:
    """POST each summary; return those the Ledger accepted and those it did not.

    A rejection (400/409/422) is a bug in this tool, not a condition to work
    around: every summary posted here is for an owner the Ledger itself called
    active, under a key derived from that owner and a body derived from the
    Directory. So one is recorded and reported loudly rather than retried into
    a second refusal — and the remaining summaries are still posted, because
    one bad owner is no reason to leave the rest of the Ledger stale.
    """
    accepted: List[Summary] = []
    rejections: List[Rejection] = []
    for summary in summaries:
        headers = {"Idempotency-Key": IDEMPOTENCY_KEY_PREFIX + summary.owner}
        response = ledger.post_json(SUMMARIES_PATH, summary.to_dict(),
                                    headers=headers)
        if response.status in POST_ACCEPTED_STATUSES:
            accepted.append(summary)
        else:
            rejections.append(Rejection(summary, response.status, response.text))
    return accepted, rejections


# --------------------------------------------------------------------------
# Output files
# --------------------------------------------------------------------------

def _write_lines(path: str, records: Iterable[Any]) -> None:
    """Write `records` as JSON lines, replacing whatever was there.

    Lines go out in a canonical order. Order carries no meaning in these
    files, but a fixed one makes two runs over the same data produce
    byte-identical output, which is the cheapest possible way to be able to
    check that they did.
    """
    with open(path, "w", encoding="utf-8") as handle:
        for record in sorted(records, key=_content_key):
            handle.write(json.dumps(record) + "\n")


def write_outputs(out_dir: str, result: SyncResult) -> None:
    """Write the three output files, creating `out_dir` if it is absent.

    All three are rewritten from this run's reading of the services and from
    nothing else. `orphans.jsonl` and `errors.jsonl` are plainly that: what
    the services currently hold that no summary can account for.

    `summary.jsonl` needs a word, because a second run against unchanged
    services posts nothing and yet must leave the file as it found it. It
    holds the summaries this tool has put in the Ledger: the ones posted just
    now, plus the ones an earlier run posted and the Ledger is still handing
    back unchanged. On the first run those are exactly the posts it made; on
    every run after that the Ledger's own copy reproduces the same lines.
    """
    os.makedirs(out_dir, exist_ok=True)
    _write_lines(os.path.join(out_dir, ORPHANS_FILENAME), result.orphans)
    _write_lines(os.path.join(out_dir, ERRORS_FILENAME), result.errors)
    _write_lines(os.path.join(out_dir, SUMMARY_FILENAME),
                 [summary.to_dict() for summary in result.recorded])


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    directory = Service(DIRECTORY, HttpClient(config.directory_url))
    ledger = Service(LEDGER, HttpClient(config.ledger_url))
    result = reconcile(directory, ledger)
    write_outputs(out_dir, result)
    if result.rejections:
        for rejection in result.rejections:
            print("orgsync: %s" % rejection, file=sys.stderr)
        return EXIT_WRITE_REJECTED
    return 0
