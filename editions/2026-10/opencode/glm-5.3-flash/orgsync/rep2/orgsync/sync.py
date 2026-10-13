"""The `sync` command: reconcile the Directory and the Ledger.

One pass does five things, in order:

1. Read every page of accounts (Ledger), items (Directory) and existing
   summaries (Ledger), following `next_page` until it is null.
2. Decide who counts: the Ledger's active, well-formed accounts own summaries;
   everything else in a record stream is either a malformed record (reported,
   never counted) or an item without an active owner (an orphan, reported,
   never counted).
3. Compute the one summary each active owner is owed.
4. POST only the summaries the Ledger does not have yet, under the
   `Idempotency-Key: sync-<owner>` scheme.
5. Write the run's report under the output directory: orphans, malformed
   records, and the summaries posted.

Two rules shape the plumbing. A 429 from a service silences that service only
— no request goes to it until `Retry-After` seconds have passed, then the same
request is retried; a 429 is never data and never gives up. And sync is
idempotent: with the services unchanged, a second run issues no writes and
leaves the output files byte-for-byte as the first run left them, which is why
`summary.jsonl` is append-only (nothing new is posted, so nothing is added)
while the orphan and error reports are rewritten from what the services say
today.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple

from .config import Config
from .http_client import HttpClient, Response

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

ITEMS_PATH = "/v1/items"
ACCOUNTS_PATH = "/v1/accounts"
SUMMARIES_PATH = "/v1/summaries"

#: Envelope keys of the three list endpoints, and the output file names.
PAGE_KEYS = {ITEMS_PATH: "items", ACCOUNTS_PATH: "accounts", SUMMARIES_PATH: "summaries"}
ORPHANS_FILE = "orphans.jsonl"
ERRORS_FILE = "errors.jsonl"
SUMMARY_FILE = "summary.jsonl"

STATUS_OPEN = "open"
STATUS_DONE = "done"

#: Used when a 429 arrives without a usable `Retry-After` value.
DEFAULT_RETRY_AFTER_SECONDS = 1.0
#: Slack added to every wait so "at least Retry-After" survives clock fuzz.
RETRY_GRACE_SECONDS = 0.01


class SyncError(Exception):
    """The run could not be completed against the services as they answer."""


# --------------------------------------------------------------------------
# Record well-formedness. JSON types exactly: a JSON `true` is not the integer
# 1, and `1.0` is not an integer, so bools are excluded from int checks and
# only `bool` passes the boolean check.
# --------------------------------------------------------------------------


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_well_formed_item(record: Any) -> bool:
    """{"id": str, "owner": str, "status": "open"|"done", "points": int >= 0}.

    Extra fields never make a record malformed, so only the named fields are
    looked at.
    """
    return (isinstance(record, dict)
            and _is_str(record.get("id"))
            and _is_str(record.get("owner"))
            and record.get("status") in (STATUS_OPEN, STATUS_DONE)
            and _is_int(record.get("points"))
            and record["points"] >= 0)


def is_well_formed_account(record: Any) -> bool:
    """{"id": str, "owner": str, "active": true|false}."""
    return (isinstance(record, dict)
            and _is_str(record.get("id"))
            and _is_str(record.get("owner"))
            and isinstance(record.get("active"), bool))


# --------------------------------------------------------------------------
# Rate limiting. One limiter per service: a 429 silences its own service only.
# --------------------------------------------------------------------------


class RateLimiter:
    """Remembers when a service may next be asked a question.

    `defer` pushes that moment out; `wait` sleeps up to it. The clock and the
    sleeper are injectable so tests can run time at full speed.
    """

    def __init__(self, clock: Any = time.monotonic, sleep: Any = time.sleep) -> None:
        self._clock = clock
        self._sleep = sleep
        self._not_before = 0.0

    def wait(self) -> None:
        """Sleep until the service may be asked again (no-op if already past)."""
        delay = self._not_before - self._clock()
        if delay > 0:
            self._sleep(delay)

    def defer(self, seconds: float) -> None:
        """Silence the service for at least `seconds` more."""
        wait = max(float(seconds), 0.0) + RETRY_GRACE_SECONDS
        self._not_before = max(self._not_before, self._clock() + wait)


def retry_after_seconds(headers: Any) -> float:
    """Read `Retry-After` (seconds form) from response headers.

    Missing, unparseable, or negative values fall back to the default rather
    than spinning or erroring; HTTP-date forms are not produced by either
    service.
    """
    raw = None
    if headers is not None:
        try:
            raw = headers.get("Retry-After")
        except Exception:
            raw = None
    if raw is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    try:
        return max(float(str(raw).strip()), 0.0)
    except ValueError:
        return DEFAULT_RETRY_AFTER_SECONDS


class RateLimitedService:
    """One service's client plus its rate limiter, with 429 retried in-line.

    Every request first waits out any silence period, then goes out; a 429
    answer extends the silence by `Retry-After` and the identical request is
    sent again. Anything else comes back to the caller.
    """

    def __init__(self, client: HttpClient, limiter: Optional[RateLimiter] = None) -> None:
        self.client = client
        self.limiter = limiter if limiter is not None else RateLimiter()

    def _send(self, send: Any) -> Response:
        while True:
            self.limiter.wait()
            response = send()
            if response.status != 429:
                return response
            self.limiter.defer(retry_after_seconds(response.headers))

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Response:
        return self._send(lambda: self.client.get(path, params))

    def post_json(self, path: str, payload: Any,
                  headers: Optional[Dict[str, str]] = None) -> Response:
        return self._send(lambda: self.client.post_json(path, payload, headers))


# --------------------------------------------------------------------------
# Reading: follow `next_page` until it is null.
# --------------------------------------------------------------------------


def _read_all_pages(service: RateLimitedService, path: str) -> Iterator[Any]:
    """Yield every record of a list endpoint, page by page.

    Page 1 is requested; after that the envelope's `next_page` is followed
    until it is absent or null. A non-200 answer other than 429 (which the
    service layer retries) means the endpoint is not doing what it must, and
    the run stops rather than reconcile against half the truth.
    """
    key = PAGE_KEYS[path]
    page = 1
    seen_pages: Set[int] = set()
    while True:
        if page in seen_pages:  # a loop would read forever
            raise SyncError("%s: page %d repeated, pagination is broken" % (path, page))
        seen_pages.add(page)
        response = service.get(path, {"page": page})
        if response.status != 200:
            raise SyncError("%s returned status %d (wanted 200)" % (path, response.status))
        envelope = response.json()  # ValueError on a non-JSON body is fatal too
        records = envelope.get(key) if isinstance(envelope, dict) else None
        if isinstance(records, list):
            for record in records:
                yield record
        next_page = envelope.get("next_page") if isinstance(envelope, dict) else None
        if not _is_int(next_page):
            return
        page = next_page


# --------------------------------------------------------------------------
# Reconciliation.
# --------------------------------------------------------------------------


def _canonical(record: Any) -> str:
    """A stable identity for a received record, for exactly-once reporting."""
    return json.dumps(record, sort_keys=True)


def _build_services(config: Config) -> Tuple[RateLimitedService, RateLimitedService]:
    """One client per service. The Ledger's limiter is shared by its reads and
    writes, so a 429 from either silences the service as a whole."""
    directory = RateLimitedService(HttpClient(config.directory_url))
    ledger = RateLimitedService(HttpClient(config.ledger_url))
    return directory, ledger


def _collect(directory: RateLimitedService,
             ledger: RateLimitedService) -> Tuple[List[Any], List[Any], List[Any]]:
    """Read every page of accounts, items, and existing summaries."""
    accounts = list(_read_all_pages(ledger, ACCOUNTS_PATH))
    items = list(_read_all_pages(directory, ITEMS_PATH))
    summaries = list(_read_all_pages(ledger, SUMMARIES_PATH))
    return accounts, items, summaries


def _reconcile(accounts: List[Any], items: List[Any], summaries: List[Any]
               ) -> Tuple[List[Dict[str, int]], List[Any], List[Tuple[str, Any]]]:
    """Turn the three record streams into what the run must do and report.

    Returns the summaries the Ledger is missing (the POST queue), the orphan
    items, and the malformed records paired with the service that delivered
    them. Items are deduplicated by id before any counting, so a
    doubly-delivered item counts once; malformed records are deduplicated by
    content so each is reported exactly once.
    """
    errors: List[Tuple[str, Any]] = []
    reported: Set[Tuple[str, str]] = set()

    def report(service: str, record: Any) -> None:
        marker = (service, _canonical(record))
        if marker not in reported:
            reported.add(marker)
            errors.append((service, record))

    active_owners: Set[str] = set()
    for record in accounts:
        if not is_well_formed_account(record):
            report("ledger", record)
            continue
        if record["active"]:
            active_owners.add(record["owner"])

    orphans: List[Any] = []
    seen_item_ids: Set[str] = set()
    tallies: Dict[str, Dict[str, int]] = {}
    for record in items:
        if not is_well_formed_item(record):
            report("directory", record)
            continue
        item_id = record["id"]
        if item_id in seen_item_ids:  # same item delivered twice
            continue
        seen_item_ids.add(item_id)
        owner = record["owner"]
        if owner not in active_owners:
            orphans.append(record)
            continue
        tally = tallies.setdefault(
            owner, {"open_items": 0, "done_items": 0, "total_points": 0})
        tally["open_items" if record["status"] == STATUS_OPEN else "done_items"] += 1
        tally["total_points"] += record["points"]

    owners_with_summary: Set[str] = set()
    for record in summaries:
        if isinstance(record, dict) and _is_str(record.get("owner")):
            owners_with_summary.add(record["owner"])

    required = {
        owner: {"owner": owner,
                "open_items": tallies.get(owner, {}).get("open_items", 0),
                "done_items": tallies.get(owner, {}).get("done_items", 0),
                "total_points": tallies.get(owner, {}).get("total_points", 0)}
        for owner in sorted(active_owners)
    }
    missing = [required[owner] for owner in sorted(required)
               if owner not in owners_with_summary]
    return missing, orphans, errors


def _post_summaries(ledger: RateLimitedService, missing: List[Dict[str, int]],
                    summary_path: str) -> None:
    """POST each missing summary and record the body sent, one line each.

    The line written is exactly the request body. 200 (replay) and 201
    (accepted) both mean the summary is in the Ledger; anything else is a
    rejected write, which a correct run never produces, so it stops the run.
    """
    if not missing:
        _ensure_file(summary_path)
        return
    with open(summary_path, "a", encoding="utf-8") as handle:
        for summary in missing:
            body = json.dumps(summary)
            response = ledger.post_json(
                SUMMARIES_PATH, summary, {"Idempotency-Key": "sync-%s" % summary["owner"]})
            if response.status not in (200, 201):
                raise SyncError("ledger rejected summary for %r: status %d"
                                % (summary["owner"], response.status))
            handle.write(body + "\n")


def _write_report(path: str, lines: Iterable[str]) -> None:
    """Rewrite one report file with the given lines."""
    with open(path, "w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")


def _ensure_file(path: str) -> None:
    """Make sure `path` exists, without disturbing content it already has."""
    with open(path, "a", encoding="utf-8"):
        pass


# --------------------------------------------------------------------------
# Entry point.
# --------------------------------------------------------------------------


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    directory, ledger = _build_services(config)
    accounts, items, summaries = _collect(directory, ledger)
    missing, orphans, errors = _reconcile(accounts, items, summaries)

    os.makedirs(out_dir, exist_ok=True)

    # Orphans and malformed records are what the services say today: rewrite
    # the reports so each current entry appears exactly once and a no-change
    # rerun leaves the bytes identical.
    _write_report(os.path.join(out_dir, ORPHANS_FILE),
                  (json.dumps(record) for record in orphans))
    _write_report(os.path.join(out_dir, ERRORS_FILE),
                  (json.dumps({"service": service, "record": record})
                   for service, record in errors))

    # Post only what the Ledger lacks, then append the bodies sent. Nothing is
    # posted on a no-change rerun, so the file keeps its previous content.
    _post_summaries(ledger, missing, os.path.join(out_dir, SUMMARY_FILE))
    return 0
