"""The `sync` command: reconcile the Directory and the Ledger.

One pass reads every page of accounts, items and existing summaries, decides
what the Ledger should hold, writes the three output files, and then posts the
summaries the Ledger is missing:

- the Ledger decides who counts: each active, well-formed account gets one
  summary, counting its owner's well-formed items (each item id once);
- a well-formed item whose owner has no active account is an orphan;
- a malformed record is skipped and reported in `errors.jsonl`;
- a summary is posted only for an owner the Ledger has no summary for yet.

`summary.jsonl` accumulates across runs: an entry posted by an earlier run is
kept as long as the Ledger still holds a summary for that owner. That keeps a
second run, which posts nothing, from emptying the file.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from typing import Any, Dict, Iterable, List, Set

from .config import Config
from .http_client import HttpClient
from .models import Summary
from .service import RateLimitedService, ServiceError
from .validate import is_json_int, parse_account, parse_item

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

ORPHANS_FILE = "orphans.jsonl"
ERRORS_FILE = "errors.jsonl"
SUMMARY_FILE = "summary.jsonl"

ACCOUNTS_PATH, ACCOUNTS_KEY = "/v1/accounts", "accounts"
ITEMS_PATH, ITEMS_KEY = "/v1/items", "items"
SUMMARIES_PATH, SUMMARIES_KEY = "/v1/summaries", "summaries"


def read_all_pages(service: RateLimitedService, path: str, key: str) -> List[Any]:
    """Every entry under `key` across all pages of `path`, in page order.

    Pages are numbered from 1 and followed by `next_page` until it is null. A
    page that is not the expected shape, or a `next_page` that repeats a page,
    raises `ServiceError`: the page cannot be trusted to be complete.
    """
    entries: List[Any] = []
    fetched: Set[int] = set()
    page = 1
    while True:
        fetched.add(page)
        response = service.request("GET", path, params={"page": page})
        if response.status != 200:
            raise ServiceError("GET %s page %d: HTTP %d" % (path, page, response.status))
        try:
            body = response.json()
        except ValueError as exc:
            raise ServiceError("GET %s page %d: body is not JSON" % (path, page)) from exc
        if not isinstance(body, dict) or "next_page" not in body \
                or not isinstance(body.get(key), list):
            raise ServiceError("GET %s page %d: unexpected body shape" % (path, page))
        entries.extend(body[key])

        next_page = body["next_page"]
        if next_page is None:
            return entries
        if not is_json_int(next_page) or next_page in fetched:
            raise ServiceError("GET %s page %d: bad next_page %r" % (path, page, next_page))
        page = next_page


class Plan:
    """What the Ledger should hold, and what else the run must report."""

    def __init__(self) -> None:
        #: owner -> the summary that owner must have, for active owners.
        self.required: Dict[str, Summary] = {}
        #: well-formed items with no active owner, as received.
        self.orphans: List[Any] = []
        #: malformed records as `{"service": ..., "record": ...}`, each once.
        self.errors: List[Dict[str, Any]] = []


def build_plan(account_records: Iterable[Any], item_records: Iterable[Any]) -> Plan:
    """Decide the summaries, orphans and errors from the raw service records."""
    plan = Plan()
    seen_errors: Set[str] = set()

    def reject(service: str, record: Any) -> None:
        marker = json.dumps([service, record], sort_keys=True)
        if marker not in seen_errors:
            seen_errors.add(marker)
            plan.errors.append({"service": service, "record": record})

    active_owners: Set[str] = set()
    for record in account_records:
        account = parse_account(record)
        if account is None:
            reject("ledger", record)
        elif account.active:
            active_owners.add(account.owner)

    counts: Dict[str, List[int]] = {owner: [0, 0, 0] for owner in active_owners}
    seen_items: Set[str] = set()
    for record in item_records:
        item = parse_item(record)
        if item is None:
            reject("directory", record)
            continue
        if item.id in seen_items:
            continue  # the same item delivered again: count it once
        seen_items.add(item.id)
        if item.owner not in active_owners:
            plan.orphans.append(record)
            continue
        tally = counts[item.owner]
        if item.status == "open":
            tally[0] += 1
        else:
            tally[1] += 1
        tally[2] += item.points

    for owner in sorted(counts):
        open_items, done_items, total_points = counts[owner]
        plan.required[owner] = Summary(owner=owner, open_items=open_items,
                                       done_items=done_items, total_points=total_points)
    return plan


def idempotency_key(owner: str) -> str:
    """`sync-<owner>`, percent-encoded only if an HTTP header cannot carry it."""
    key = "sync-" + owner
    try:
        key.encode("latin-1")
    except UnicodeEncodeError:
        return "sync-" + urllib.parse.quote(owner, safe="")
    if not key.isprintable():
        return "sync-" + urllib.parse.quote(owner, safe="")
    return key


def write_jsonl(path: str, entries: Iterable[Any]) -> None:
    """Replace `path` with one JSON line per entry, via a temporary file."""
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")
    os.replace(temp, path)


def load_prior_summaries(path: str, ledger_owners: Set[str]) -> List[Dict[str, Any]]:
    """Entries from an earlier run's `summary.jsonl` the Ledger still holds.

    An entry whose owner the Ledger no longer has a summary for is dropped: the
    file then no longer describes anything the Ledger holds.
    """
    if not os.path.exists(path):
        return []
    kept: List[Dict[str, Any]] = []
    kept_owners: Set[str] = set()
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            owner = entry.get("owner") if isinstance(entry, dict) else None
            if isinstance(owner, str) and owner in ledger_owners and owner not in kept_owners:
                kept.append(entry)
                kept_owners.add(owner)
    return kept


def post_summary(ledger: RateLimitedService, body: Dict[str, Any]) -> None:
    """POST one summary under its idempotency key. Any rejection stops the run."""
    response = ledger.request("POST", SUMMARIES_PATH, payload=body,
                              headers={"Idempotency-Key": idempotency_key(body["owner"])})
    if response.status not in (200, 201):
        raise ServiceError("ledger rejected summary for %r: HTTP %d"
                           % (body["owner"], response.status))


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success). Raises `ServiceError` if a
    service cannot be read completely or the Ledger rejects a summary.
    """
    directory = RateLimitedService(HttpClient(config.directory_url))
    ledger = RateLimitedService(HttpClient(config.ledger_url))

    accounts = read_all_pages(ledger, ACCOUNTS_PATH, ACCOUNTS_KEY)
    items = read_all_pages(directory, ITEMS_PATH, ITEMS_KEY)
    summaries = read_all_pages(ledger, SUMMARIES_PATH, SUMMARIES_KEY)

    plan = build_plan(accounts, items)
    ledger_owners = {s["owner"] for s in summaries
                     if isinstance(s, dict) and isinstance(s.get("owner"), str)}

    os.makedirs(out_dir, exist_ok=True)
    summary_path = os.path.join(out_dir, SUMMARY_FILE)
    write_jsonl(os.path.join(out_dir, ORPHANS_FILE), plan.orphans)
    write_jsonl(os.path.join(out_dir, ERRORS_FILE), plan.errors)
    posted = load_prior_summaries(summary_path, ledger_owners)
    write_jsonl(summary_path, posted)

    posted_now = 0
    for owner, summary in plan.required.items():
        if owner in ledger_owners:
            continue
        body = summary.to_dict()
        post_summary(ledger, body)
        posted.append(body)
        posted_now += 1
        write_jsonl(summary_path, posted)

    print("orgsync: %d summaries required, %d posted, %d orphans, %d errors"
          % (len(plan.required), posted_now, len(plan.orphans), len(plan.errors)))
    return 0
