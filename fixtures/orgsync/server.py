#!/usr/bin/env python3
"""orgsync benchmark fixtures — the Directory and Ledger services.

Built to `design/orgsync/FIXTURES.md`. SEALED: this file carries the seeded
dataset and therefore must never be copied into a run repo or shown to a model
under test.

Role in the harness
-------------------
One process serves BOTH fixture services on two loopback ports, so a single
lifetime (and therefore a single deterministic request-ordinal counter per
service) spans the judge's double invocation of `python3 -m orgsync sync`.
Posted summaries live in memory for the lifetime of the process; killing the
process is the only reset.

Contract summary (authority is FIXTURES.md, not this docstring)
---------------------------------------------------------------
* Directory: ``GET /v1/items?page=N``            — page size 4, 12 records.
* Ledger:    ``GET /v1/accounts?page=N``         — page size 3, 7 records.
* Ledger:    ``GET /v1/summaries?page=N``        — page size 10, initially empty.
* Ledger:    ``POST /v1/summaries``              — validation ladder, see
  ``_post_summary``: 422 owner-not-active → 200 idempotent replay → 409 key
  conflict → 201 accepted.
* Both: request ordinals 2 and 8 (1-based, per service, counting *every*
  request including 429s) are answered ``429`` + ``Retry-After: 1``, no body.
* Both: every request appends one JSON line to
  ``<log-dir>/{directory,ledger}.jsonl``. That log is the judge's evidence.

Determinism
-----------
No randomness, no wall-clock dependence in any response body or status. The
only clock use is the ``ts`` field of the request log (judge evidence for
Retry-After compliance). Two runs of the same client against a fresh process
are byte-identical in behaviour.

Usage
-----
    python3 server.py [--directory-port N] [--ledger-port N] [--log-dir DIR]

Ports default to 0 (OS-assigned) so back-to-back runs cannot collide. The
chosen ports are printed as a single JSON object on stdout and flushed, e.g.

    {"directory_url": "http://127.0.0.1:51931", "directory_port": 51931,
     "ledger_url": "http://127.0.0.1:51932", "ledger_port": 51932, "pid": 4242}

SIGTERM / SIGINT shut both listeners down cleanly and close the log files, so a
port is never leaked between runs.

Thread safety: each service holds a mutex for the whole of one request, so the
request-ordinal sequence equals the arrival order even if a client under test
issues concurrent requests. The two services are independent.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs

# ── Constants (FIXTURES.md §3) ───────────────────────────────────────────────

HOST = "127.0.0.1"

PAGE_SIZE_ACCOUNTS = 3
PAGE_SIZE_ITEMS = 4
PAGE_SIZE_SUMMARIES = 10

#: 1-based per-service request ordinals answered 429. Counting includes the
#: 429 responses themselves, and runs for the lifetime of the process.
RATE_LIMIT_ORDINALS = frozenset({2, 8})
RETRY_AFTER_SECONDS = 1

#: Socket read timeout for one request, seconds. Guards against a client that
#: opens a connection and never completes a request line.
REQUEST_TIMEOUT_SECONDS = 30.0

#: Hard cap on a POST body, bytes. Fixture bodies are tiny; this only stops a
#: buggy client under test from making the fixture allocate without bound.
MAX_BODY_BYTES = 1 << 20

# ── Seeded dataset (FIXTURES.md §4) ──────────────────────────────────────────
# Field order is the order given in FIXTURES.md. The judge compares parsed
# objects, so key order is cosmetic — it is preserved for readable logs.

ACCOUNTS: List[Dict[str, Any]] = [
    {"id": "acct-01", "owner": "amara", "active": True},
    {"id": "acct-02", "owner": "bo", "active": True},
    {"id": "acct-03", "owner": "chen", "active": False},
    {"id": "acct-04", "owner": "dee", "active": True},
    {"id": "acct-05", "owner": "eli", "active": True},
    # MALFORMED on purpose: `active` is a JSON string, not a JSON boolean.
    {"id": "acct-06", "owner": "fen", "active": "yes"},
    {"id": "acct-07", "owner": "gus", "active": False},
]

ITEMS: List[Dict[str, Any]] = [
    {"id": "item-101", "owner": "amara", "status": "open", "points": 3},
    {"id": "item-102", "owner": "amara", "status": "done", "points": 2},
    {"id": "item-103", "owner": "bo", "status": "open", "points": 5},
    {"id": "item-104", "owner": "chen", "status": "open", "points": 8},
    {"id": "item-105", "owner": "eli", "status": "done", "points": 1},
    {"id": "item-106", "owner": "zara", "status": "open", "points": 4},
    {"id": "item-107", "owner": "fen", "status": "open", "points": 2},
    {"id": "item-108", "owner": "bo", "status": "done", "points": 1},
    # Duplicate delivery of item-103, byte-identical to record #3.
    {"id": "item-103", "owner": "bo", "status": "open", "points": 5},
    # MALFORMED on purpose: no `points` field.
    {"id": "item-109", "owner": "amara", "status": "open"},
    {"id": "item-110", "owner": "amara", "status": "open", "points": 7},
    {"id": "item-111", "owner": "eli", "status": "open", "points": 2},
]


def _is_well_formed_account(record: Any) -> bool:
    """True when `record` is a well-formed account per the task contract.

    Well-formed = mapping with `id`: str, `owner`: str, `active`: JSON boolean.
    Extra fields are ignored. `bool` is checked with `type(...) is bool` because
    Python's `isinstance(True, int)` would otherwise let a JSON integer through
    the `active` gate.

    Pure; O(1). Used only by the ledger's POST validation (step 1) — the fixture
    never needs to validate items.
    """
    if not isinstance(record, dict):
        return False
    return (
        isinstance(record.get("id"), str)
        and isinstance(record.get("owner"), str)
        and type(record.get("active")) is bool
    )


#: Owners the ledger will accept a summary for (POST validation step 1).
#: Derived from the seeded dataset rather than hardcoded so the dataset stays
#: the single source of truth. Value for the L2 dataset: amara, bo, dee, eli.
ACTIVE_OWNERS = frozenset(
    a["owner"] for a in ACCOUNTS if _is_well_formed_account(a) and a["active"]
)


# ── Pagination ───────────────────────────────────────────────────────────────


def paginate(records: List[Any], page: int, page_size: int) -> Tuple[List[Any], Optional[int]]:
    """Return `(slice_for_page, next_page)` for a 1-based `page`.

    `next_page` is `page + 1` when more records follow, else `None`. A page
    past the end yields an empty slice and `None` (FIXTURES.md does not seed
    this case; answering empty rather than 404 keeps a probing client honest
    about `next_page` being the only pagination signal).

    Pure; O(page_size) slicing. `page >= 1` is guaranteed by the caller.
    """
    start = (page - 1) * page_size
    window = records[start:start + page_size]
    has_more = len(records) > start + page_size
    return window, (page + 1 if has_more else None)


# ── Request log (FIXTURES.md §1) ─────────────────────────────────────────────


class RequestLog:
    """Append-only JSONL request log for one service; one line per request.

    Fields, exactly as FIXTURES.md §1 names them::

        {"n": int, "ts": float, "method": str, "path": str, "query": str,
         "idempotency_key": str|null, "body": any|null, "status": int}

    `ts` is taken when the request reaches the head of the service mutex, i.e.
    arrival order, and has full float resolution — the judge differences
    consecutive `ts` values to check Retry-After compliance. Using arrival
    (rather than response) time can only over-state a client's wait by the
    response latency, so a compliant client never fails on this choice.

    Not thread-safe on its own; every call is made under the owning service's
    mutex. Each line is flushed so the log is readable while the fixture runs.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # "w": a fixture instance always starts with an empty log. The judge
        # runs its own instance in its own directory, so nothing is lost.
        self._fh = open(path, "w", encoding="utf-8")

    def append(self, entry: Dict[str, Any]) -> None:
        self._fh.write(json.dumps(entry, sort_keys=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except OSError:
            pass


# ── Service state ────────────────────────────────────────────────────────────


class ServiceState:
    """Mutable per-service state: request counter, log, and posted summaries.

    Lives for the lifetime of the process, which is what makes the 429 schedule
    and the idempotency ladder deterministic across the judge's two `sync`
    invocations. All mutation happens under `lock`.
    """

    def __init__(self, name: str, log_path: str) -> None:
        self.name = name
        self.lock = threading.Lock()
        self.log = RequestLog(log_path)
        self.count = 0
        #: Accepted summaries, in acceptance order — what GET /v1/summaries serves.
        self.summaries: List[Dict[str, Any]] = []
        #: Idempotency-Key -> accepted body, for the 200/409 ladder steps.
        self.keys: Dict[Any, Dict[str, Any]] = {}

    def next_ordinal(self) -> int:
        self.count += 1
        return self.count


# ── HTTP handling ────────────────────────────────────────────────────────────


class _FixtureHandler(BaseHTTPRequestHandler):
    """Shared request plumbing. Subclasses supply `state` and `route`.

    Subclasses are per-service classes with a `state` class attribute; the
    routing table is a subclass method so the two services genuinely differ
    (the directory 404s on `/v1/accounts`, and vice versa).
    """

    protocol_version = "HTTP/1.1"       # accurate Content-Length on every reply
    timeout = REQUEST_TIMEOUT_SECONDS
    server_version = "orgsync-fixture/1.0"
    sys_version = ""

    state: ServiceState                 # set on the subclass
    verbose = False

    # -- BaseHTTPRequestHandler hooks ----------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401
        """Silence the default stderr access log; the JSONL log is the record."""
        if self.verbose:
            sys.stderr.write("[%s] %s\n" % (self.state.name, fmt % args))

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_HEAD(self) -> None:
        self._handle("HEAD")

    # -- core ----------------------------------------------------------------

    def _handle(self, method: str) -> None:
        """Count, rate-limit, route, log and answer exactly one request.

        The whole request is served under the service mutex so that the
        request-ordinal sequence is the arrival sequence even under a
        concurrent client. Bodies are small and the peer is loopback, so
        holding the mutex across the response write is not a stall risk.
        """
        parsed = urlparse(self.path)
        path = parsed.path
        query = parsed.query or ""

        with self.state.lock:
            ts = time.time()
            n = self.state.next_ordinal()
            raw_body = self._read_body()
            idem = self.headers.get("Idempotency-Key")

            body_for_log: Any = None
            parsed_body: Any = None
            body_ok = True
            if raw_body is not None:
                try:
                    parsed_body = json.loads(raw_body.decode("utf-8"))
                    body_for_log = parsed_body
                except (UnicodeDecodeError, ValueError):
                    body_ok = False
                    # Unparseable body still has to be evidence; keep the text.
                    body_for_log = raw_body.decode("utf-8", "replace")

            if n in RATE_LIMIT_ORDINALS:
                # FIXTURES.md §3: fixed ordinal schedule, no body, before routing.
                status, payload, extra_headers = (
                    429,
                    None,
                    {"Retry-After": str(RETRY_AFTER_SECONDS)},
                )
            elif raw_body is not None and not body_ok:
                status, payload, extra_headers = 400, {"error": "malformed_json"}, {}
            else:
                status, payload, extra_headers = self.route(
                    method, path, query, parsed_body, idem
                )

            self.state.log.append(
                {
                    "n": n,
                    "ts": ts,
                    "method": method,
                    "path": path,
                    "query": query,
                    "idempotency_key": idem,
                    "body": body_for_log,
                    "status": status,
                }
            )
            self._respond(status, payload, extra_headers, head_only=(method == "HEAD"))

    def _read_body(self) -> Optional[bytes]:
        """Read the request body, or None when the request carried none.

        Always drains the declared Content-Length — even for a request that
        will be answered 429 — so the connection stays usable for keep-alive.
        """
        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            return None
        try:
            length = int(raw_len)
        except ValueError:
            return b""
        if length <= 0:
            return b""
        return self.rfile.read(min(length, MAX_BODY_BYTES))

    def _respond(
        self,
        status: int,
        payload: Any,
        extra_headers: Dict[str, str],
        head_only: bool = False,
    ) -> None:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in extra_headers.items():
            self.send_header(key, value)
        self.end_headers()
        if body and not head_only:
            self.wfile.write(body)

    # -- helpers for subclasses ----------------------------------------------

    @staticmethod
    def _page_arg(query: str) -> Tuple[Optional[int], Any]:
        """Parse `?page=N`; return `(page, None)` or `(None, error_payload)`.

        A missing `page` defaults to 1 (the prompt says N starts at 1 and never
        promises a bare collection URL; defaulting is friendlier than 400 and
        cannot change the answer key). A present-but-unusable `page` is a 400.
        """
        values = parse_qs(query).get("page")
        if not values:
            return 1, None
        raw = values[0]
        try:
            page = int(raw)
        except ValueError:
            return None, {"error": "bad_page"}
        if page < 1:
            return None, {"error": "bad_page"}
        return page, None

    def _page_response(
        self, key: str, records: List[Any], page_size: int, query: str
    ) -> Tuple[int, Any, Dict[str, str]]:
        page, err = self._page_arg(query)
        if err is not None:
            return 400, err, {}
        window, next_page = paginate(records, page, page_size)
        return 200, {key: window, "next_page": next_page}, {}

    # -- routing (overridden) -------------------------------------------------

    def route(
        self,
        method: str,
        path: str,
        query: str,
        body: Any,
        idem: Optional[str],
    ) -> Tuple[int, Any, Dict[str, str]]:
        raise NotImplementedError


class DirectoryHandler(_FixtureHandler):
    """Directory service: authoritative for work items."""

    def route(self, method, path, query, body, idem):
        if path == "/v1/items":
            if method not in ("GET", "HEAD"):
                return 405, {"error": "method_not_allowed"}, {}
            return self._page_response("items", ITEMS, PAGE_SIZE_ITEMS, query)
        return 404, {"error": "not_found"}, {}


class LedgerHandler(_FixtureHandler):
    """Ledger service: authoritative for accounts, and the summary sink."""

    def route(self, method, path, query, body, idem):
        if path == "/v1/accounts":
            if method not in ("GET", "HEAD"):
                return 405, {"error": "method_not_allowed"}, {}
            return self._page_response("accounts", ACCOUNTS, PAGE_SIZE_ACCOUNTS, query)
        if path == "/v1/summaries":
            if method in ("GET", "HEAD"):
                return self._page_response(
                    "summaries", self.state.summaries, PAGE_SIZE_SUMMARIES, query
                )
            if method == "POST":
                return self._post_summary(body, idem)
            return 405, {"error": "method_not_allowed"}, {}
        return 404, {"error": "not_found"}, {}

    def _post_summary(
        self, body: Any, idem: Optional[str]
    ) -> Tuple[int, Any, Dict[str, str]]:
        """POST /v1/summaries validation ladder, in the order FIXTURES.md §3 fixes.

        1. owner is not the owner of an active, well-formed account → 422
        2. known Idempotency-Key, identical body                    → 200 (replay)
        3. known Idempotency-Key, different body                    → 409
        4. otherwise                                                → 201, stored

        Note the order: an invalid owner is rejected 422 even on a key replay.
        Bodies are compared as parsed JSON (`==`), not as bytes, so whitespace
        and key order do not decide a replay. Called under the service mutex.
        """
        if not isinstance(body, dict):
            return 400, {"error": "malformed_json"}, {}

        owner = body.get("owner")
        if not isinstance(owner, str) or owner not in ACTIVE_OWNERS:
            return 422, {"error": "owner_not_active"}, {}

        if idem in self.state.keys:
            if self.state.keys[idem] == body:
                return 200, self.state.keys[idem], {}
            return 409, {"error": "idempotency_key_conflict"}, {}

        stored = dict(body)
        self.state.keys[idem] = stored
        self.state.summaries.append(stored)
        return 201, stored, {}


# ── Process wiring ───────────────────────────────────────────────────────────


def _make_server(handler_cls, state: ServiceState, port: int, verbose: bool):
    """Bind one service on 127.0.0.1:`port` and return the server.

    A per-service handler subclass is minted so the class-level `state` never
    leaks between services. Binding happens here (in the constructor), so by
    the time this returns the OS-assigned port is known and the caller may
    print it — no readiness race for the harness.
    """
    bound = type(
        handler_cls.__name__ + "Bound",
        (handler_cls,),
        {"state": state, "verbose": verbose},
    )
    server = ThreadingHTTPServer((HOST, port), bound)
    server.daemon_threads = True
    return server


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="orgsync benchmark fixture services")
    parser.add_argument("--directory-port", type=int, default=0,
                        help="port for the Directory service (0 = OS-assigned)")
    parser.add_argument("--ledger-port", type=int, default=0,
                        help="port for the Ledger service (0 = OS-assigned)")
    parser.add_argument("--log-dir", default="fixture-logs",
                        help="directory for <service>.jsonl request logs")
    parser.add_argument("--verbose", action="store_true",
                        help="echo an access log to stderr")
    args = parser.parse_args(argv)

    log_dir = os.path.abspath(args.log_dir)
    directory_state = ServiceState("directory", os.path.join(log_dir, "directory.jsonl"))
    ledger_state = ServiceState("ledger", os.path.join(log_dir, "ledger.jsonl"))

    servers = []
    try:
        servers.append(_make_server(DirectoryHandler, directory_state,
                                    args.directory_port, args.verbose))
        servers.append(_make_server(LedgerHandler, ledger_state,
                                    args.ledger_port, args.verbose))
    except OSError as exc:
        for srv in servers:
            srv.server_close()
        directory_state.log.close()
        ledger_state.log.close()
        print(json.dumps({"error": "bind_failed", "detail": str(exc)}), flush=True)
        return 1

    directory_port = servers[0].server_address[1]
    ledger_port = servers[1].server_address[1]
    print(
        json.dumps(
            {
                "directory_url": "http://%s:%d" % (HOST, directory_port),
                "ledger_url": "http://%s:%d" % (HOST, ledger_port),
                "directory_port": directory_port,
                "ledger_port": ledger_port,
                "log_dir": log_dir,
                "pid": os.getpid(),
            }
        ),
        flush=True,
    )

    stop = threading.Event()

    def _shutdown(signum, frame):  # noqa: ARG001 - signal handler signature
        stop.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    threads = []
    for srv in servers:
        thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1},
                                  daemon=True)
        thread.start()
        threads.append(thread)

    try:
        while not stop.wait(0.25):
            pass
    finally:
        # shutdown() stops the accept loop; server_close() releases the listening
        # socket immediately so the port is never held into the next run.
        for srv in servers:
            srv.shutdown()
            srv.server_close()
        for thread in threads:
            thread.join(timeout=5)
        directory_state.log.close()
        ledger_state.log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
