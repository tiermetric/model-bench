"""A service as `sync` sees it: rate-limited, retrying, paginated JSON.

`HttpClient` moves bytes and hands back whatever status it got. This layer
adds the policy the two services demand: a `429` with `Retry-After` is not an
answer but an instruction to wait and ask again, and a list endpoint is only
fully read once `next_page` comes back `null`. One `Service` per base URL,
because the rate limit is per service — a `429` from the Directory says
nothing about when the Ledger may next be called.

Time is injectable (`clock`, `sleep`) so tests can run without waiting.
"""

from __future__ import annotations

import datetime
import email.utils
import sys
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

from .http_client import HttpClient, Response

#: Seconds to wait after a 429 whose Retry-After is missing or unparseable.
DEFAULT_RETRY_AFTER_SECONDS = 1.0

#: Small cushion added to every Retry-After wait so that clock granularity on
#: either side can never make the retry land a hair too early.
RETRY_AFTER_MARGIN_SECONDS = 0.05

#: Server-side failures (5xx) are retried this many times, with a short pause,
#: before the run gives up. They are rare and have no Retry-After to honour.
MAX_SERVER_ERROR_RETRIES = 5
SERVER_ERROR_PAUSE_SECONDS = 0.5

#: Pagination guard: a service that keeps handing out pages past this is
#: looping, and the run should fail rather than spin forever.
MAX_PAGES = 100000


class ServiceError(Exception):
    """A service answered in a way the run cannot proceed from."""


def parse_retry_after(value: Optional[str], now: Optional[float] = None) -> float:
    """Seconds to wait for a `Retry-After` header value (delta or HTTP-date).

    Returns DEFAULT_RETRY_AFTER_SECONDS when the header is absent or cannot be
    read, and never less than zero.
    """
    if value is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    text = value.strip()
    if not text:
        return DEFAULT_RETRY_AFTER_SECONDS
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return DEFAULT_RETRY_AFTER_SECONDS
    if when is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    current = time.time() if now is None else now
    return max(0.0, when.timestamp() - current)


class Service:
    """One HTTP service, with rate limiting and pagination applied.

    Every request goes through `request`, which waits out any outstanding
    `Retry-After` for this service before sending, and on a `429` records the
    new wait and sends the same request again — as many times as it takes. A
    `429` is therefore never visible to callers.
    """

    def __init__(self, name: str, client: HttpClient,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 log: Optional[Callable[[str], None]] = None) -> None:
        self.name = name
        self.client = client
        self._clock = clock
        self._sleep = sleep
        self._log = log if log is not None else _stderr
        #: Earliest `clock()` value at which this service may be called again.
        self.not_before = clock()
        self.rate_limit_hits = 0

    # -- rate limiting -------------------------------------------------------

    def _wait_until_allowed(self) -> None:
        while True:
            remaining = self.not_before - self._clock()
            if remaining <= 0:
                return
            self._sleep(remaining)

    def _note_rate_limit(self, response: Response) -> None:
        wait = parse_retry_after(response.headers.get("Retry-After"))
        self.not_before = self._clock() + wait + RETRY_AFTER_MARGIN_SECONDS
        self.rate_limit_hits += 1
        self._log("%s: rate limited (429), retrying after %.2fs" % (self.name, wait))

    # -- requests ------------------------------------------------------------

    def request(self, method: str, path: str,
                params: Optional[Mapping[str, Any]] = None,
                payload: Optional[Any] = None,
                headers: Optional[Mapping[str, str]] = None) -> Response:
        """Send one request, honouring rate limits; return the final response.

        A `429` is retried after its `Retry-After`; a `5xx` is retried a
        bounded number of times. Anything else — success or a 4xx that
        actually means something — is returned to the caller to interpret.
        """
        server_errors = 0
        while True:
            self._wait_until_allowed()
            response = self.client.request(method, path, params=params,
                                           payload=payload, headers=headers)
            if response.status == 429:
                self._note_rate_limit(response)
                continue
            if 500 <= response.status <= 599:
                server_errors += 1
                if server_errors > MAX_SERVER_ERROR_RETRIES:
                    raise ServiceError("%s: %s %s kept failing with HTTP %d" % (
                        self.name, method, response.url, response.status))
                self._log("%s: HTTP %d from %s, retrying" % (
                    self.name, response.status, response.url))
                self._sleep(SERVER_ERROR_PAUSE_SECONDS)
                continue
            return response

    def get(self, path: str, params: Optional[Mapping[str, Any]] = None) -> Response:
        return self.request("GET", path, params=params)

    def post_json(self, path: str, payload: Any,
                  headers: Optional[Mapping[str, str]] = None) -> Response:
        return self.request("POST", path, payload=payload, headers=headers)

    # -- pagination ----------------------------------------------------------

    def fetch_all(self, path: str, key: str) -> List[Any]:
        """Every record under `key` across all pages of `path`, in order.

        Follows `next_page` from page 1 until it is `null`. Records come back
        exactly as the service sent them — nothing here validates them.
        """
        records: List[Any] = []
        page: Any = 1
        pages_seen = 0
        while page is not None:
            response = self.get(path, {"page": page})
            if response.status != 200:
                raise ServiceError("%s: GET %s answered HTTP %d" % (
                    self.name, response.url, response.status))
            try:
                document = response.json()
            except ValueError as exc:
                raise ServiceError("%s: GET %s returned invalid JSON: %s" % (
                    self.name, response.url, exc)) from exc
            if not isinstance(document, dict):
                raise ServiceError("%s: GET %s did not return a JSON object" % (
                    self.name, response.url))
            batch = document.get(key)
            if batch is None:
                batch = []
            if not isinstance(batch, list):
                raise ServiceError("%s: GET %s: %r is not a list" % (
                    self.name, response.url, key))
            records.extend(batch)

            next_page = document.get("next_page")
            if next_page is not None and (
                    isinstance(next_page, bool) or not isinstance(next_page, int)):
                raise ServiceError("%s: GET %s: next_page is %r" % (
                    self.name, response.url, next_page))
            pages_seen += 1
            if pages_seen >= MAX_PAGES:
                raise ServiceError("%s: GET %s: more than %d pages" % (
                    self.name, path, MAX_PAGES))
            page = next_page
        return records


def _stderr(message: str) -> None:
    print("orgsync: %s" % message, file=sys.stderr)
