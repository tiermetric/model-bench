"""The `sync` command: reconcile the Directory and the Ledger.

A run reads everything first, then decides, then writes. The deciding is
`reconcile`, which touches no network and no disk, so its rules can be read
on their own. Writes are:

- `orphans.jsonl` and `errors.jsonl`: recomputed from this run's reads.
- `summary.jsonl`: every summary this tool has posted. It is merged with what
  an earlier run recorded, so a rerun against an unchanged ledger, which posts
  nothing, leaves the file as it was.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Set

from .client import ServiceClient, SyncError
from .config import Config
from .http_client import HttpClient
from .models import Summary
from .validate import DONE_STATUS, OPEN_STATUS, parse_account, parse_item

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

#: Exit code when a service could not be read or written to completion.
EXIT_SERVICE_ERROR = 4

ORPHANS_FILE = "orphans.jsonl"
ERRORS_FILE = "errors.jsonl"
SUMMARY_FILE = "summary.jsonl"

ACCOUNTS_PATH = "/v1/accounts"
ITEMS_PATH = "/v1/items"
SUMMARIES_PATH = "/v1/summaries"


@dataclass
class Reconciliation:
    """What one pass over the services decided."""

    #: One required summary per active, well-formed account, sorted by owner.
    summaries: List[Summary] = field(default_factory=list)
    #: Owners that already have a summary in the ledger.
    existing_owners: Set[str] = field(default_factory=set)
    #: Well-formed items with no active owner, as received, each once.
    orphans: List[Any] = field(default_factory=list)
    #: Malformed records as `{"service", "record"}` entries, each once.
    errors: List[Dict[str, Any]] = field(default_factory=list)

    def missing(self) -> List[Summary]:
        """Required summaries the ledger does not have yet."""
        return [s for s in self.summaries if s.owner not in self.existing_owners]


def reconcile(account_records: Iterable[Any], item_records: Iterable[Any],
              summary_records: Iterable[Any]) -> Reconciliation:
    """Apply the sync rules to the records as the services delivered them."""
    result = Reconciliation()
    error_keys: Set[str] = set()

    def add_error(service: str, record: Any) -> None:
        entry = {"service": service, "record": record}
        key = json.dumps(entry, sort_keys=True)
        if key not in error_keys:
            error_keys.add(key)
            result.errors.append(entry)

    # The ledger decides who counts. Owners are unique among well-formed
    # accounts, so keying by owner loses nothing.
    accounts = {}
    for raw in account_records:
        account = parse_account(raw)
        if account is None:
            add_error("ledger", raw)
            continue
        accounts.setdefault(account.owner, account)
    active = {owner for owner, account in accounts.items() if account.active}

    # The same item id delivered twice is one item, so keep the first.
    items = {}
    for raw in item_records:
        item = parse_item(raw)
        if item is None:
            add_error("directory", raw)
            continue
        items.setdefault(item.id, (raw, item))

    # owner -> [open_items, done_items, total_points]
    tallies = {owner: [0, 0, 0] for owner in active}
    for raw, item in items.values():
        if item.owner not in active:
            result.orphans.append(raw)
            continue
        tally = tallies[item.owner]
        tally[0 if item.status == OPEN_STATUS else 1] += 1
        tally[2] += item.points

    result.summaries = [
        Summary(owner=owner, open_items=tallies[owner][0],
                done_items=tallies[owner][1], total_points=tallies[owner][2])
        for owner in sorted(active)
    ]
    result.existing_owners = {
        record["owner"] for record in summary_records
        if isinstance(record, dict) and isinstance(record.get("owner"), str)
    }
    return result


def idempotency_key(owner: str) -> str:
    """`sync-<owner>`, or a percent-encoded form when the owner cannot travel
    in an HTTP header unchanged (non-ASCII, control characters, edge spaces)."""
    key = "sync-" + owner
    if key.isascii() and key.isprintable() and key == key.strip():
        return key
    return "sync-" + urllib.parse.quote(owner, safe="")


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    # The services are local. Ignore any proxy in the environment so that a
    # proxy setting cannot divert the requests.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    directory = ServiceClient("directory", HttpClient(config.directory_url, opener=opener))
    ledger = ServiceClient("ledger", HttpClient(config.ledger_url, opener=opener))
    try:
        return _sync(directory, ledger, out_dir)
    except SyncError as exc:
        print("orgsync: sync failed: %s" % exc, file=sys.stderr)
        return EXIT_SERVICE_ERROR


def _sync(directory: ServiceClient, ledger: ServiceClient, out_dir: str) -> int:
    accounts = ledger.fetch_all(ACCOUNTS_PATH, "accounts")
    items = directory.fetch_all(ITEMS_PATH, "items")
    summaries = ledger.fetch_all(SUMMARIES_PATH, "summaries")
    plan = reconcile(accounts, items, summaries)

    os.makedirs(out_dir, exist_ok=True)
    _write_lines(os.path.join(out_dir, ORPHANS_FILE),
                 (json.dumps(raw) for raw in plan.orphans))
    _write_lines(os.path.join(out_dir, ERRORS_FILE),
                 (json.dumps(entry) for entry in plan.errors))

    summary_path = os.path.join(out_dir, SUMMARY_FILE)
    posted: Dict[str, Dict[str, Any]] = {}
    try:
        for summary in plan.missing():
            body = summary.to_dict()
            response = ledger.send(
                "POST", SUMMARIES_PATH, payload=body,
                headers={"Idempotency-Key": idempotency_key(summary.owner)})
            if response.status not in (200, 201):
                raise SyncError("POST summary for %r: status %d"
                                % (summary.owner, response.status))
            posted[summary.owner] = body
    finally:
        _record_posted(summary_path, posted)
    return 0


def _record_posted(path: str, posted: Dict[str, Dict[str, Any]]) -> None:
    """Merge this run's posts into the summary file, one line per owner."""
    merged = _read_summary_file(path)
    merged.update(posted)
    _write_lines(path, (json.dumps(merged[owner]) for owner in sorted(merged)))


def _read_summary_file(path: str) -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    if not os.path.exists(path):
        return records
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and isinstance(record.get("owner"), str):
                records[record["owner"]] = record
    return records


def _write_lines(path: str, lines: Iterable[str]) -> None:
    """Replace `path` with `lines`, one per line, in one rename."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")
    os.replace(tmp, path)
