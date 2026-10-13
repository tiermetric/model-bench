"""One service's API, with the two behaviours every endpoint shares.

`HttpClient` moves bytes and knows nothing about status codes. This layer adds
what both the Directory and the Ledger have in common:

* **Rate limiting.** Any request may be answered `429` with `Retry-After`. The
  service then gets no further request until that many seconds have passed,
  after which the same request is sent again. A 429 is never data and never
  gives up on the work.
* **Pagination.** List endpoints answer `{"<key>": [...], "next_page": N}`
  with `next_page` null on the last page; `fetch_all` walks that to the end.

Time is injectable (`sleep` and `clock`) so tests do not have to wait.
"""

from __future__ import annotations

import email.utils
import sys
import time
from typing import Any, Callable, List, Mapping, Optional

from .http_client import HttpClient, Response

#: Used when a 429 carries no usable Retry-After header.
DEFAULT_RETRY_AFTER_SECONDS = 1.0

#: Added to every Retry-After wait so that clock granularity on either side
#: can never make the retry arrive a hair early.
RETRY_AFTER_MARGIN_SECONDS = 0.05

#: Transient server-side failures are retried a bounded number of times.
TRANSIENT_STATUSES = (500, 502, 503, 504)
MAX_TRANSIENT_RETRIES = 5
TRANSIENT_BACKOFF_SECONDS = 0.5


class ServiceError(Exception):
    """A request could not be turned into a usable answer."""


def parse_retry_after(value: Optional[str], now: float) -> float:
    """Seconds to wait given a Retry-After header value (delta or HTTP-date).

    Unparseable or missing values fall back to DEFAULT_RETRY_AFTER_SECONDS;
    the result is never negative.
    """
    if value is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    text = value.strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return DEFAULT_RETRY_AFTER_SECONDS
    if parsed is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    return max(0.0, parsed.timestamp() - now)


class Service:
    """A rate-limit-honouring view of one service."""

    def __init__(self, name: str, client: HttpClient,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 log: Callable[[str], None] = lambda msg: print(msg, file=sys.stderr)) -> None:
        self.name = name
        self.client = client
        self._sleep = sleep
        self._clock = clock
        self._log = log
        #: Monotonic time before which no request may be sent to this service.
        self._not_before = 0.0
        self.rate_limited_count = 0

    # -- transport ---------------------------------------------------------

    def _wait_for_window(self) -> None:
        remaining = self._not_before - self._clock()
        if remaining > 0:
            self._sleep(remaining)

    def request(self, method: str, path: str,
                params: Optional[Mapping[str, Any]] = None,
                payload: Optional[Any] = None,
                headers: Optional[Mapping[str, str]] = None) -> Response:
        """Send one request, retrying the same request after any 429.

        Returns the first response that is not a 429 (or a transient 5xx
        within the retry budget). Every other status is the caller's to judge.
        """
        transient_failures = 0
        while True:
            self._wait_for_window()
            response = self.client.request(method, path, params=params,
                                           payload=payload, headers=headers)
            if response.status == 429:
                self.rate_limited_count += 1
                delay = parse_retry_after(response.headers.get("Retry-After"),
                                          time.time())
                self._not_before = self._clock() + delay + RETRY_AFTER_MARGIN_SECONDS
                self._log("%s: rate limited on %s %s; retrying after %.3gs"
                          % (self.name, method, path, delay))
                continue
            if response.status in TRANSIENT_STATUSES:
                transient_failures += 1
                if transient_failures > MAX_TRANSIENT_RETRIES:
                    raise ServiceError("%s: %s %s kept failing with %d"
                                       % (self.name, method, path, response.status))
                self._not_before = self._clock() + TRANSIENT_BACKOFF_SECONDS * transient_failures
                self._log("%s: %d on %s %s; retrying" % (self.name, response.status, method, path))
                continue
            return response

    # -- endpoints ---------------------------------------------------------

    def fetch_all(self, path: str, key: str) -> List[Any]:
        """Follow `next_page` from page 1 until null, concatenating `key`."""
        records: List[Any] = []
        page: Any = 1
        seen_pages = set()
        while page is not None:
            if page in seen_pages:
                raise ServiceError("%s: %s pagination loops back to page %r"
                                   % (self.name, path, page))
            seen_pages.add(page)
            response = self.request("GET", path, params={"page": page})
            if response.status != 200:
                raise ServiceError("%s: GET %s?page=%s answered %d"
                                   % (self.name, path, page, response.status))
            try:
                body = response.json()
            except ValueError as exc:
                raise ServiceError("%s: GET %s?page=%s is not JSON: %s"
                                   % (self.name, path, page, exc)) from exc
            if not isinstance(body, dict):
                raise ServiceError("%s: GET %s?page=%s did not return an object"
                                   % (self.name, path, page))
            chunk = body.get(key)
            if isinstance(chunk, list):
                records.extend(chunk)
            elif chunk is not None:
                raise ServiceError("%s: GET %s?page=%s: %r is not a list"
                                   % (self.name, path, page, key))
            page = body.get("next_page")
        return records

    def post_json(self, path: str, payload: Any,
                  headers: Optional[Mapping[str, str]] = None) -> Response:
        """POST `payload` as JSON, honouring rate limits. See `request`."""
        return self.request("POST", path, payload=payload, headers=headers)
