"""The `sync` command: reconcile the Directory and the Ledger.

Everything else in the package is in place — config loading, the HTTP client,
the record types. What lives here is the part that has to make decisions.

Three layers, in dependency order:

* `ServiceClient` wraps one `HttpClient` with the two behaviours the services
  demand: follow `next_page` until it is null, and respect a `429` by sending
  that service nothing at all until its `Retry-After` has elapsed. The pause is
  per service, not per endpoint, because that is what the limit means.
* `plan()` is pure: raw records in, decisions out. It owns every rule about
  what a well-formed record is, who counts as an owner, and what to post. No
  I/O, so the rules can be tested without a socket.
* `run_sync()` is the wiring: read, plan, write, post, write.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .config import Config
from .http_client import HttpClient, Response
from .models import Account, Item, Summary

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

ERRORS_FILENAME = "errors.jsonl"
ORPHANS_FILENAME = "orphans.jsonl"
SUMMARY_FILENAME = "summary.jsonl"

ITEMS_PATH = "/v1/items"
ACCOUNTS_PATH = "/v1/accounts"
SUMMARIES_PATH = "/v1/summaries"

#: Service names, as they appear in the `service` field of an error line.
DIRECTORY = "directory"
LEDGER = "ledger"

VALID_STATUSES = ("open", "done")

#: Statuses that mean "the ledger refused this write". A run that is behaving
#: correctly never provokes one: we only post summaries for owners of active,
#: well-formed accounts that the ledger has no summary for yet.
REJECTED_STATUSES = frozenset({400, 409, 422})

#: A 429 carries `Retry-After`; this is what we wait if it is missing or
#: unparseable, so a malformed header cannot turn into a busy loop.
DEFAULT_RETRY_AFTER_SECONDS = 1.0

#: Added to every rate-limit wait. The service timed the window from when it
#: answered, we time it from when we read the answer, so we are already late —
#: this only guards against clock granularity at the boundary.
RETRY_AFTER_MARGIN_SECONDS = 0.1

#: Attempts per request, counting the first. Only 429 and 5xx are retried.
MAX_ATTEMPTS = 12

#: Backoff between retries of a 5xx, in seconds.
SERVER_ERROR_BACKOFF_SECONDS = 0.5

#: Refuses to follow a pagination cycle forever, whatever the service says.
MAX_PAGES = 10000

#: Exit code when the ledger refused a write we should never have sent.
EXIT_REJECTED_WRITE = 4


class SyncError(Exception):
    """A service answered in a way the reconciliation cannot continue from."""


# --------------------------------------------------------------------------
# Transport: pagination and the rate limit
# --------------------------------------------------------------------------


def parse_retry_after(headers: Any) -> float:
    """Seconds to wait from a `Retry-After` header, defaulted if unusable."""
    raw = None
    if headers is not None:
        try:
            raw = headers.get("Retry-After")
        except AttributeError:
            raw = None
    try:
        delay = float(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS
    if delay != delay or delay in (float("inf"), float("-inf")):  # NaN / infinity
        return DEFAULT_RETRY_AFTER_SECONDS
    return max(0.0, delay)


class ServiceClient:
    """One service, with `next_page` following and `429` handling added.

    The rate-limit pause is held here rather than per request because a `429`
    is the *service* saying stop: the next request to it, on any path, waits
    the window out. `sleep` and `monotonic` are injectable so a test can watch
    the waiting happen without doing any.
    """

    def __init__(self, name: str, client: HttpClient, sleep: Any = time.sleep,
                 monotonic: Any = time.monotonic,
                 max_attempts: int = MAX_ATTEMPTS) -> None:
        self.name = name
        self.client = client
        self._sleep = sleep
        self._monotonic = monotonic
        self._max_attempts = max_attempts
        #: Monotonic timestamp before which this service must not be called.
        self._not_before = 0.0

    # -- rate limiting ----------------------------------------------------

    def _await_window(self) -> None:
        """Block until the service's rate-limit window has passed."""
        remaining = self._not_before - self._monotonic()
        if remaining > 0:
            self._sleep(remaining)

    def _hold_off(self, response: Response) -> None:
        """Record the pause a 429 asks for."""
        delay = parse_retry_after(response.headers)
        self._not_before = self._monotonic() + delay + RETRY_AFTER_MARGIN_SECONDS

    # -- requests ---------------------------------------------------------

    def request(self, method: str, path: str,
                params: Optional[Mapping[str, Any]] = None,
                payload: Optional[Any] = None,
                headers: Optional[Mapping[str, str]] = None) -> Response:
        """Issue one logical request, waiting out rate limits and retrying.

        A 429 is neither data nor failure: we wait as instructed and send the
        same request again. A 5xx gets a small bounded retry. Anything else —
        including a 4xx the caller may want to inspect — comes straight back.
        """
        last: Optional[Response] = None
        for _ in range(self._max_attempts):
            self._await_window()
            response = self.client.request(method, path, params=params,
                                           payload=payload, headers=headers)
            if response.status == 429:
                self._hold_off(response)
                last = response
                continue
            if 500 <= response.status < 600:
                last = response
                self._sleep(SERVER_ERROR_BACKOFF_SECONDS)
                continue
            return response
        raise SyncError("%s: %s %s still failing after %d attempts (last status %s)"
                        % (self.name, method, path, self._max_attempts,
                           last.status if last is not None else "none"))

    def get(self, path: str,
            params: Optional[Mapping[str, Any]] = None) -> Response:
        """GET `path`. See `request`."""
        return self.request("GET", path, params=params)

    def post_json(self, path: str, payload: Any,
                  headers: Optional[Mapping[str, str]] = None) -> Response:
        """POST `payload` to `path` as JSON. See `request`."""
        return self.request("POST", path, payload=payload, headers=headers)

    # -- pagination -------------------------------------------------------

    def fetch_all(self, path: str, key: str) -> List[Any]:
        """Return every record under `key`, following `next_page` to the end.

        Records come back exactly as received — judging them is somebody else's
        job. A `next_page` that is neither an integer nor null ends the walk,
        and so does revisiting a page, which would otherwise loop forever.
        """
        records: List[Any] = []
        seen_pages = set()
        page: Any = 1
        for _ in range(MAX_PAGES):
            if not isinstance(page, int) or isinstance(page, bool):
                break
            if page in seen_pages:
                break
            seen_pages.add(page)
            response = self.get(path, {"page": page})
            if response.status != 200:
                raise SyncError("%s: GET %s?page=%s returned %d"
                                % (self.name, path, page, response.status))
            try:
                body = response.json()
            except ValueError as exc:
                raise SyncError("%s: GET %s?page=%s sent a body that is not JSON: %s"
                                % (self.name, path, page, exc)) from exc
            if not isinstance(body, dict):
                raise SyncError("%s: GET %s?page=%s did not send a JSON object"
                                % (self.name, path, page))
            batch = body.get(key)
            if isinstance(batch, list):
                records.extend(batch)
            page = body.get("next_page")
        return records


# --------------------------------------------------------------------------
# Rules: what a well-formed record is
# --------------------------------------------------------------------------


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_int(value: Any) -> bool:
    """A JSON integer. `True` is an `int` to Python but a boolean to JSON."""
    return isinstance(value, int) and not isinstance(value, bool)


def parse_item(raw: Any) -> Optional[Item]:
    """An `Item` if `raw` is a well-formed item, else None.

    Extra fields never make a record malformed, so only the four that matter
    are looked at.
    """
    if not isinstance(raw, dict):
        return None
    identifier, owner = raw.get("id"), raw.get("owner")
    status, points = raw.get("status"), raw.get("points")
    if not _is_str(identifier) or not _is_str(owner):
        return None
    if not _is_str(status) or status not in VALID_STATUSES:
        return None
    if not _is_int(points) or points < 0:
        return None
    return Item(id=identifier, owner=owner, status=status, points=points)


def parse_account(raw: Any) -> Optional[Account]:
    """An `Account` if `raw` is a well-formed account, else None."""
    if not isinstance(raw, dict):
        return None
    identifier, owner, active = raw.get("id"), raw.get("owner"), raw.get("active")
    if not _is_str(identifier) or not _is_str(owner):
        return None
    if not isinstance(active, bool):
        return None
    return Account(id=identifier, owner=owner, active=active)


def summary_owner(raw: Any) -> Optional[str]:
    """The owner an already-posted summary covers, if it names one.

    Deliberately lenient: the only thing a plan needs from an existing summary
    is which owner the ledger already holds one for, and anything naming an
    owner is that evidence.
    """
    if isinstance(raw, dict) and _is_str(raw.get("owner")):
        return raw["owner"]
    return None


# --------------------------------------------------------------------------
# Rules: the reconciliation itself
# --------------------------------------------------------------------------


@dataclass
class Plan:
    """What one reconciliation decided, before anything has been written."""

    #: `{"service": ..., "record": ...}` lines, one per malformed record.
    errors: List[Dict[str, Any]] = field(default_factory=list)
    #: Items, as received, whose owner has no active well-formed account.
    orphans: List[Any] = field(default_factory=list)
    #: The summaries that must exist: exactly one per active account.
    required: List[Summary] = field(default_factory=list)
    #: The subset of `required` the ledger has no summary for yet.
    to_post: List[Summary] = field(default_factory=list)


def _canonical(value: Any) -> str:
    """A stable key for a JSON record, so it can be written exactly once."""
    try:
        return json.dumps(value, sort_keys=True, default=repr)
    except (TypeError, ValueError):  # pragma: no cover - JSON in, JSON out
        return repr(value)


def plan(account_records: Iterable[Any], item_records: Iterable[Any],
         summary_records: Iterable[Any]) -> Plan:
    """Decide everything, from the raw records of all three endpoints.

    Pure. The ledger decides who counts: an owner counts only if a well-formed
    account names them and is active. A malformed account is not an account, so
    its owner is not thereby active and its items are orphans. Every record is
    handled once however many times it was delivered — items by id, malformed
    records by their content.
    """
    result = Plan()
    seen_errors = set()

    def record_error(service: str, raw: Any) -> None:
        key = (service, _canonical(raw))
        if key not in seen_errors:
            seen_errors.add(key)
            result.errors.append({"service": service, "record": raw})

    # Accounts first: they decide who counts.
    active_owners: List[str] = []
    seen_owners = set()
    for raw in account_records:
        account = parse_account(raw)
        if account is None:
            record_error(LEDGER, raw)
            continue
        if account.active and account.owner not in seen_owners:
            seen_owners.add(account.owner)
            active_owners.append(account.owner)

    #: owner -> [open, done, points]. An active owner with no items keeps zeros.
    tallies: Dict[str, List[int]] = {owner: [0, 0, 0] for owner in active_owners}

    # Items, deduplicated by id: the same id twice is one item delivered twice.
    seen_items = set()
    for raw in item_records:
        item = parse_item(raw)
        if item is None:
            record_error(DIRECTORY, raw)
            continue
        if item.id in seen_items:
            continue
        seen_items.add(item.id)
        tally = tallies.get(item.owner)
        if tally is None:
            result.orphans.append(raw)
            continue
        if item.status == "open":
            tally[0] += 1
        else:
            tally[1] += 1
        tally[2] += item.points

    result.required = [
        Summary(owner=owner, open_items=tallies[owner][0],
                done_items=tallies[owner][1], total_points=tallies[owner][2])
        for owner in sorted(active_owners)
    ]

    # Post only what the ledger is missing. A summary it already holds would be
    # a replay at best and a 409 at worst, and neither is ours to send.
    posted_owners = {owner for owner in
                     (summary_owner(raw) for raw in summary_records)
                     if owner is not None}
    result.to_post = [s for s in result.required if s.owner not in posted_owners]
    return result


# --------------------------------------------------------------------------
# Output files
# --------------------------------------------------------------------------


def _encode(record: Any) -> str:
    return json.dumps(record, sort_keys=True)


def read_jsonl(path: str) -> List[Any]:
    """Parse a JSONL file, skipping blank and unparseable lines. [] if absent."""
    records: List[Any] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return records


def write_jsonl(path: str, records: Sequence[Any]) -> None:
    """Write `records`, one JSON object per line, replacing the file."""
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(_encode(record) + "\n")


def merge_jsonl(path: str, records: Sequence[Any]) -> List[Any]:
    """Add `records` to the file at `path`, keeping every line unique.

    Used for `summary.jsonl` alone, and for one reason: a summary is written
    when it is posted, and a later run posts nothing because an earlier one
    already did. Rewriting that file from what this run posted would empty it;
    merging keeps the record of what was posted intact across runs while still
    never repeating a line.
    """
    merged: List[Any] = []
    seen = set()
    for record in list(read_jsonl(path)) + list(records):
        key = _canonical(record)
        if key in seen:
            continue
        seen.add(key)
        merged.append(record)
    write_jsonl(path, merged)
    return merged


# --------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------


def idempotency_key(owner: str) -> str:
    """The key a summary for `owner` is posted under, stable across runs."""
    return "sync-%s" % owner


def post_summaries(ledger: ServiceClient,
                   summaries: Sequence[Summary]) -> Tuple[List[Dict[str, Any]],
                                                          List[str]]:
    """POST each summary, returning the bodies accepted and any complaints.

    A 201 accepts, and a 200 is a replay of an identical earlier post; either
    way the ledger now holds that summary, so both are recorded. A 400/409/422
    is a write that should never have been sent — it is reported and the run
    carries on, so one bad owner cannot cost the others their summaries.
    """
    written: List[Dict[str, Any]] = []
    rejected: List[str] = []
    for summary in summaries:
        body = summary.to_dict()
        response = ledger.post_json(
            SUMMARIES_PATH, body, {"Idempotency-Key": idempotency_key(summary.owner)})
        if response.status in (200, 201):
            written.append(body)
        elif response.status in REJECTED_STATUSES:
            rejected.append("ledger rejected the summary for %r with %d: %s"
                            % (summary.owner, response.status, response.text.strip()))
        else:
            rejected.append("ledger answered %d for the summary for %r: %s"
                            % (response.status, summary.owner, response.text.strip()))
    return written, rejected


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR,
             directory: Optional[ServiceClient] = None,
             ledger: Optional[ServiceClient] = None) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success). `directory` and `ledger` are
    injectable so a test can drive a whole pass over a fake transport.
    """
    if directory is None:
        directory = ServiceClient(DIRECTORY, HttpClient(config.directory_url))
    if ledger is None:
        ledger = ServiceClient(LEDGER, HttpClient(config.ledger_url))

    accounts = ledger.fetch_all(ACCOUNTS_PATH, "accounts")
    items = directory.fetch_all(ITEMS_PATH, "items")
    summaries = ledger.fetch_all(SUMMARIES_PATH, "summaries")

    decided = plan(accounts, items, summaries)

    os.makedirs(out_dir, exist_ok=True)
    write_jsonl(os.path.join(out_dir, ERRORS_FILENAME), decided.errors)
    write_jsonl(os.path.join(out_dir, ORPHANS_FILENAME), decided.orphans)

    written, rejected = post_summaries(ledger, decided.to_post)
    merge_jsonl(os.path.join(out_dir, SUMMARY_FILENAME), written)

    for message in rejected:
        print("orgsync: %s" % message, file=sys.stderr)
    if rejected:
        return EXIT_REJECTED_WRITE

    print("orgsync: %d summaries required, %d posted, %d orphans, "
          "%d malformed records" % (len(decided.required), len(written),
                                    len(decided.orphans), len(decided.errors)))
    return 0
