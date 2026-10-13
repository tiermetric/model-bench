"""The `sync` command: reconcile the Directory and the Ledger.

Everything else in the package is in place — config loading, the HTTP client,
the record types. What lives here is the part that has to make decisions.

The shape of a run, in order:

1. Read every page of accounts, items and existing summaries, following
   `next_page` until it is null. A 429 from a service pauses *that* service
   for `Retry-After` seconds and the same request is then retried; it is never
   data and never abandons the work.
2. Split every record into well-formed and malformed. A malformed record is
   skipped, never counted, and recorded once in `errors.jsonl`.
3. The ledger decides who counts: the owner of every active, well-formed
   account gets exactly one summary. Items belonging to anyone else are
   orphans.
4. POST only the summaries the ledger is missing, so no write is ever one the
   ledger would reject with 400, 409 or 422.
"""

from __future__ import annotations

import json
import os
import time
from typing import (Any, Callable, Dict, Iterator, List, Mapping, Optional,
                    Sequence, Set, Tuple)

from .config import Config
from .http_client import HttpClient, Response
from .models import Account, Item, Summary

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

ORPHANS_FILENAME = "orphans.jsonl"
ERRORS_FILENAME = "errors.jsonl"
SUMMARY_FILENAME = "summary.jsonl"

#: The two service names, as they appear in `errors.jsonl`.
DIRECTORY = "directory"
LEDGER = "ledger"

#: The only statuses a well-formed item may carry.
ITEM_STATUSES = ("open", "done")

#: Used when a 429 arrives without a usable `Retry-After`.
DEFAULT_RETRY_AFTER_SECONDS = 1.0
#: Clamp, so a mistaken header cannot stall the run indefinitely.
MAX_RETRY_AFTER_SECONDS = 120.0
#: Slack added to every rate-limit wait, to absorb clock and rounding skew
#: between us and the service — waiting slightly too long is always safe.
RATE_LIMIT_MARGIN_SECONDS = 0.05

#: How many times one request may be re-sent after a 429 or a 5xx.
MAX_ATTEMPTS = 25
#: Backstop against a service that paginates in a cycle.
MAX_PAGES = 10000

#: Prefix of the idempotency key for a posted summary: `sync-<owner>`.
IDEMPOTENCY_KEY_PREFIX = "sync-"

#: POST statuses that mean the ledger took the summary: 201 accepted, 200
#: replay. Anything else is a rejection, which a correct run never provokes.
POST_ACCEPTED_STATUSES = (200, 201)


class SyncError(Exception):
    """A service answered in a way the run cannot continue from."""


# --------------------------------------------------------------------------
# Talking to one service
# --------------------------------------------------------------------------


def parse_retry_after(headers: Any) -> float:
    """Seconds to wait, taken from a `Retry-After` header.

    Falls back to `DEFAULT_RETRY_AFTER_SECONDS` when the header is absent or
    unparseable, and clamps into [0, MAX_RETRY_AFTER_SECONDS]: a rate limit we
    cannot read is still a rate limit, so we always wait *something*.
    """
    raw = headers.get("Retry-After") if hasattr(headers, "get") else None
    try:
        seconds = float(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS
    if seconds != seconds:  # NaN
        return DEFAULT_RETRY_AFTER_SECONDS
    return max(0.0, min(seconds, MAX_RETRY_AFTER_SECONDS))


class ServiceClient:
    """One service, with its rate limit respected.

    Holds the deadline set by the most recent 429 and refuses to send anything
    to that service before it passes, so rule 6 is enforced in one place
    rather than at every call site. One instance per service — the Directory's
    rate limit says nothing about the Ledger's.
    """

    def __init__(self, name: str, base_url: Optional[str] = None,
                 client: Optional[HttpClient] = None,
                 sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        """Either `base_url` (a client is built for it) or `client` is required.
        `sleep` and `monotonic` are injectable so tests need not pass time."""
        if client is None:
            if base_url is None:
                raise ValueError("ServiceClient needs a base_url or a client")
            client = HttpClient(base_url)
        self.name = name
        self.client = client
        self._sleep = sleep
        self._monotonic = monotonic
        self._not_before: Optional[float] = None
        #: How many 429s this service has answered with; useful to tests.
        self.rate_limit_hits = 0

    # -- rate limiting -----------------------------------------------------

    def _await_rate_limit(self) -> None:
        """Block until this service may receive a request again."""
        if self._not_before is None:
            return
        remaining = self._not_before - self._monotonic()
        if remaining > 0:
            self._sleep(remaining)
        self._not_before = None

    def _record_rate_limit(self, response: Response) -> float:
        delay = parse_retry_after(response.headers) + RATE_LIMIT_MARGIN_SECONDS
        self._not_before = self._monotonic() + delay
        self.rate_limit_hits += 1
        return delay

    def _send(self, describe: str, send: Callable[[], Response],
              accepted: Optional[Sequence[int]] = None) -> Response:
        """Send one request, retrying it through 429s and transient 5xxs.

        `accepted` lists statuses to hand straight back; when it is given, any
        other status that is not retryable raises, because the caller asked
        for data it did not get. The retry re-sends the *same* request, so a
        write retried after a 429 stays the same write under the same key.
        """
        for attempt in range(MAX_ATTEMPTS):
            self._await_rate_limit()
            response = send()
            if response.status == 429:
                self._record_rate_limit(response)
                continue
            if accepted is None or response.status in accepted:
                return response
            if 500 <= response.status < 600:
                # Transient by convention: back off briefly, then try again.
                self._sleep(min(2.0, 0.2 * (attempt + 1)))
                continue
            raise SyncError("%s: %s answered %d" % (self.name, describe, response.status))
        raise SyncError("%s: %s did not succeed in %d attempts"
                        % (self.name, describe, MAX_ATTEMPTS))

    # -- requests ----------------------------------------------------------

    def get_json(self, path: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        """GET `path` and return the decoded JSON body."""
        describe = "GET %s %r" % (path, dict(params or {}))
        response = self._send(describe, lambda: self.client.get(path, params),
                              accepted=(200,))
        try:
            return response.json()
        except ValueError as exc:
            raise SyncError("%s: %s returned an unreadable body: %s"
                            % (self.name, describe, exc)) from exc

    def post_json(self, path: str, payload: Any,
                  headers: Optional[Mapping[str, str]] = None) -> Response:
        """POST `payload` to `path`, retrying through 429s.

        Every status other than 429 comes back to the caller, including a
        rejection — the caller decides what to make of one.
        """
        return self._send("POST %s" % path,
                          lambda: self.client.post_json(path, payload, headers))


def iter_pages(service: ServiceClient, path: str, key: str) -> Iterator[Any]:
    """Yield every record from every page of a paginated collection.

    Follows `next_page` until it is null. A page whose envelope or record list
    is not the shape the API documents contributes nothing rather than
    stopping the run; a `next_page` that is not a fresh positive integer ends
    the walk, which also breaks any pagination cycle.
    """
    page: Optional[int] = 1
    seen: Set[int] = set()
    while page is not None and page not in seen and len(seen) < MAX_PAGES:
        seen.add(page)
        payload = service.get_json(path, {"page": page})
        if not isinstance(payload, dict):
            return
        records = payload.get(key)
        if isinstance(records, list):
            for record in records:
                yield record
        next_page = payload.get("next_page")
        page = next_page if is_json_int(next_page) and next_page > 0 else None


# --------------------------------------------------------------------------
# What counts as well-formed
# --------------------------------------------------------------------------


def is_json_int(value: Any) -> bool:
    """True for a JSON integer. `bool` is an `int` in Python but not in JSON."""
    return isinstance(value, int) and not isinstance(value, bool)


def parse_item(record: Any) -> Optional[Item]:
    """An `Item` if `record` is a well-formed item, else None.

    Well-formed is `{"id": str, "owner": str, "status": "open"|"done",
    "points": int >= 0}` with those JSON types exactly. Extra fields are
    ignored — they never make a record malformed.
    """
    if not isinstance(record, dict):
        return None
    identifier, owner = record.get("id"), record.get("owner")
    status, points = record.get("status"), record.get("points")
    if not (isinstance(identifier, str) and isinstance(owner, str)):
        return None
    if not isinstance(status, str) or status not in ITEM_STATUSES:
        return None
    if not is_json_int(points) or points < 0:
        return None
    return Item(id=identifier, owner=owner, status=status, points=points)


def parse_account(record: Any) -> Optional[Account]:
    """An `Account` if `record` is a well-formed account, else None.

    Well-formed is `{"id": str, "owner": str, "active": bool}` — `active` must
    be a JSON boolean, so `"yes"` or `1` is malformed. A malformed account is
    not an account: its owner is not thereby active.
    """
    if not isinstance(record, dict):
        return None
    identifier, owner, active = record.get("id"), record.get("owner"), record.get("active")
    if not (isinstance(identifier, str) and isinstance(owner, str)
            and isinstance(active, bool)):
        return None
    return Account(id=identifier, owner=owner, active=active)


def summary_owner(record: Any) -> Optional[str]:
    """The owner a summary is for, if it names one.

    Deliberately lenient: a summary already in the ledger only has to tell us
    that its owner is spoken for. Demanding more would risk posting a second
    summary over the first, which is exactly the write the ledger rejects.
    """
    if isinstance(record, dict) and isinstance(record.get("owner"), str):
        return record["owner"]
    return None


# --------------------------------------------------------------------------
# Reading each service
# --------------------------------------------------------------------------


def canonical(value: Any) -> str:
    """A stable string for `value`, for use as a deduplication key."""
    try:
        return json.dumps(value, sort_keys=True, default=repr)
    except (TypeError, ValueError):  # pragma: no cover - the input came from JSON
        return repr(value)


class ErrorLog:
    """Collects malformed records, each recorded exactly once.

    Deduplication is by service plus the record's canonical JSON, because a
    malformed record may lack the id we would otherwise key it on and may
    still be delivered more than once.
    """

    def __init__(self) -> None:
        self.entries: List[Dict[str, Any]] = []
        self._seen: Set[Tuple[str, str]] = set()

    def add(self, service: str, record: Any) -> None:
        key = (service, canonical(record))
        if key in self._seen:
            return
        self._seen.add(key)
        self.entries.append({"service": service, "record": record})

    def sorted_entries(self) -> List[Dict[str, Any]]:
        """The entries in a fixed order. Order carries no meaning, but a run
        that repeats should write byte-identical files."""
        return sorted(self.entries, key=lambda entry: (entry["service"],
                                                       canonical(entry["record"])))


def read_items(service: ServiceClient,
               errors: ErrorLog) -> Tuple[Dict[str, Item], Dict[str, Any]]:
    """Every well-formed item by id, plus the raw record each arrived as.

    Ids are unique, so the same id twice is the same item twice: the first
    delivery wins and the repeat is not counted again. The raw records are
    kept because an orphan is written out exactly as it was received.
    """
    items: Dict[str, Item] = {}
    raw: Dict[str, Any] = {}
    for record in iter_pages(service, "/v1/items", "items"):
        item = parse_item(record)
        if item is None:
            errors.add(DIRECTORY, record)
            continue
        if item.id not in items:
            items[item.id] = item
            raw[item.id] = record
    return items, raw


def read_accounts(service: ServiceClient, errors: ErrorLog) -> Dict[str, Account]:
    """Every well-formed account, by owner. Owner is unique among them."""
    accounts: Dict[str, Account] = {}
    for record in iter_pages(service, "/v1/accounts", "accounts"):
        account = parse_account(record)
        if account is None:
            errors.add(LEDGER, record)
            continue
        accounts.setdefault(account.owner, account)
    return accounts


def read_summaries(service: ServiceClient) -> Dict[str, Any]:
    """The summaries the ledger already holds, by owner.

    The owners are what rule 5 turns on — an owner in here is one we must not
    post for. The records come back too, because a summary already in the
    ledger is one an earlier sync posted, and `merge_posted_summaries` needs
    to recognise it as such.
    """
    summaries: Dict[str, Any] = {}
    for record in iter_pages(service, "/v1/summaries", "summaries"):
        owner = summary_owner(record)
        if owner is not None:
            summaries.setdefault(owner, record)
    return summaries


# --------------------------------------------------------------------------
# The arithmetic
# --------------------------------------------------------------------------


def active_owners(accounts: Mapping[str, Account]) -> Set[str]:
    """The owners of the active accounts among `accounts`."""
    return {owner for owner, account in accounts.items() if account.active}


def build_summaries(accounts: Mapping[str, Account],
                    items: Mapping[str, Item]) -> Dict[str, Summary]:
    """One summary per active account owner, counting that owner's items.

    An active account with no items still gets a summary with every counter
    zero — the account is what calls for a summary, not the items.
    """
    active = active_owners(accounts)
    counts: Dict[str, List[int]] = {owner: [0, 0, 0] for owner in active}

    for item in items.values():
        counters = counts.get(item.owner)
        if counters is None:
            continue  # an orphan: no active account owns it
        counters[0 if item.status == "open" else 1] += 1
        counters[2] += item.points

    return {owner: Summary(owner=owner, open_items=values[0],
                           done_items=values[1], total_points=values[2])
            for owner, values in counts.items()}


def find_orphans(accounts: Mapping[str, Account], items: Mapping[str, Item],
                 raw_items: Mapping[str, Any]) -> List[Any]:
    """The record of every item whose owner has no active, well-formed account.

    Ordered by item id: order carries no meaning here, but a repeat run should
    write a byte-identical file, not merely an equivalent one.
    """
    active = active_owners(accounts)
    return [raw_items[item_id] for item_id, item in sorted(items.items())
            if item.owner not in active]


# --------------------------------------------------------------------------
# Output files
# --------------------------------------------------------------------------


def write_jsonl(path: str, entries: Sequence[Any]) -> None:
    """Write `entries`, one JSON value per line, replacing the file."""
    with open(path, "w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry))
            handle.write("\n")


def read_jsonl(path: str) -> List[Any]:
    """Every JSON value in `path`; [] if it is absent, empty or unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return []
    values: List[Any] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            values.append(json.loads(line))
        except ValueError:
            continue
    return values


def merge_posted_summaries(path: str, posted: Sequence[Dict[str, Any]],
                           required: Mapping[str, Summary],
                           existing: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """What `summary.jsonl` should hold once this run is done.

    A summary posted by this run belongs in the file. So does one this run had
    no reason to post because the ledger already held it, byte for byte, from
    an earlier sync — without that, a second sync, which correctly writes
    nothing, would leave the file empty and break rule 7. Lines already in the
    file count for the same reason, and cover a ledger that stores a summary
    in some shape other than the one it was posted in.

    An entry only survives if it matches a summary this run requires, so the
    file states the current truth rather than accumulating stale records.
    """
    owners: Set[str] = set()
    for entry in list(posted) + list(existing.values()) + read_jsonl(path):
        owner = summary_owner(entry)
        wanted = required.get(owner) if owner is not None else None
        if wanted is not None and entry == wanted.to_dict():
            owners.add(wanted.owner)
    return [required[owner].to_dict() for owner in sorted(owners)]


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------


def post_missing_summaries(ledger: ServiceClient, required: Mapping[str, Summary],
                           existing: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """POST every required summary the ledger does not have yet.

    Only owners of active, well-formed accounts are in `required`, so no post
    can be rejected as invalid; an owner the ledger already has a summary for
    is skipped, so no post can collide with an earlier one. Together that is
    what lets a repeat run issue no writes at all.
    """
    posted: List[Dict[str, Any]] = []
    for owner in sorted(required):
        if owner in existing:
            continue
        body = required[owner].to_dict()
        response = ledger.post_json("/v1/summaries", body,
                                    {"Idempotency-Key": IDEMPOTENCY_KEY_PREFIX + owner})
        if response.status not in POST_ACCEPTED_STATUSES:
            # A correct run never reaches here; say so rather than record a
            # summary the ledger did not take.
            raise SyncError("ledger rejected the summary for %r with %d: %s"
                            % (owner, response.status, response.text))
        posted.append(body)
    return posted


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR,
             directory: Optional[ServiceClient] = None,
             ledger: Optional[ServiceClient] = None) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success). The two service clients may be
    supplied to run the pass against something other than the live services.
    """
    if directory is None:
        directory = ServiceClient(DIRECTORY, config.directory_url)
    if ledger is None:
        ledger = ServiceClient(LEDGER, config.ledger_url)

    errors = ErrorLog()
    accounts = read_accounts(ledger, errors)
    items, raw_items = read_items(directory, errors)
    existing = read_summaries(ledger)

    required = build_summaries(accounts, items)
    orphans = find_orphans(accounts, items, raw_items)
    posted = post_missing_summaries(ledger, required, existing)

    os.makedirs(out_dir, exist_ok=True)
    summary_path = os.path.join(out_dir, SUMMARY_FILENAME)
    write_jsonl(os.path.join(out_dir, ORPHANS_FILENAME), orphans)
    write_jsonl(os.path.join(out_dir, ERRORS_FILENAME), errors.sorted_entries())
    write_jsonl(summary_path,
                merge_posted_summaries(summary_path, posted, required, existing))
    return 0
