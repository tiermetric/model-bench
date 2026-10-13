"""One service as `sync` sees it: JSON endpoints, pagination, rate limits.

`HttpClient` moves bytes and hands back whatever status it got. This layer
adds the policy the services require and nothing else:

* **429 means wait, then retry the same request.** The service's
  `Retry-After` is honored per service: after a 429 no request at all goes
  to that service until the interval has elapsed. A 429 is never data and
  never gives up.
* **Pagination** follows `next_page` from 1 until it is `null`, yielding the
  records of each page as received.
* **Transient failures** (5xx, unparsable body, transport error) are retried
  a bounded number of times, then raised as `ServiceError`.

The clock and sleep function are injectable so the waiting is testable
without actually waiting.
"""

from __future__ import annotations

import email.utils
import sys
import time
from typing import Any, Callable, Iterator, Mapping, Optional

from .http_client import HttpClient, HttpClientError, Response

#: Wait when a 429 carries no usable Retry-After.
DEFAULT_RETRY_AFTER_SECONDS = 1.0
#: Added to every rate-limit wait so a request never lands a hair early.
RATE_LIMIT_MARGIN_SECONDS = 0.05
#: Attempts for a transient failure (5xx, bad body, transport) before giving up.
TRANSIENT_ATTEMPTS = 5
TRANSIENT_BACKOFF_SECONDS = 0.5


class ServiceError(Exception):
    """A request could not be completed even after retrying."""


def parse_retry_after(value: Optional[str], now: Optional[float] = None) -> float:
    """Seconds to wait for a `Retry-After` header value.

    Accepts the delta-seconds form (including fractional) and the HTTP-date
    form; anything missing or unreadable falls back to the default so the
    client always waits rather than hammering.
    """
    if value is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    text = value.strip()
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return DEFAULT_RETRY_AFTER_SECONDS
        if when is None:
            return DEFAULT_RETRY_AFTER_SECONDS
        wall_now = time.time() if now is None else now
        seconds = when.timestamp() - wall_now
    return max(0.0, seconds)


class Service:
    """A rate-limit-aware JSON client for one service.

    `name` labels the service in errors and logs ("directory" / "ledger").
    """

    def __init__(self, name: str, client: HttpClient,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 log: Optional[Callable[[str], None]] = None) -> None:
        self.name = name
        self.client = client
        self._sleep = sleep
        self._clock = clock
        self._log = log if log is not None else _stderr_log
        #: Monotonic instant before which no request may be sent.
        self._not_before = 0.0
        self.rate_limit_hits = 0

    # -- rate limiting -----------------------------------------------------

    def _wait_for_window(self) -> None:
        delay = self._not_before - self._clock()
        if delay > 0:
            self._sleep(delay)

    def _note_rate_limit(self, response: Response) -> None:
        self.rate_limit_hits += 1
        retry_after = parse_retry_after(response.headers.get("Retry-After"))
        self._not_before = self._clock() + retry_after + RATE_LIMIT_MARGIN_SECONDS
        self._log("%s: 429 rate limited, retrying after %.2fs" % (self.name, retry_after))

    # -- requests ----------------------------------------------------------

    def request(self, method: str, path: str,
                params: Optional[Mapping[str, Any]] = None,
                payload: Optional[Any] = None,
                headers: Optional[Mapping[str, str]] = None) -> Response:
        """Send one request, waiting out 429s and retrying transient failures.

        Returns the first response that is neither a 429 nor transient; a
        4xx other than 429 is returned for the caller to interpret.
        """
        transient_failures = 0
        while True:
            self._wait_for_window()
            try:
                response = self.client.request(method, path, params=params,
                                               payload=payload, headers=headers)
            except HttpClientError as exc:
                transient_failures = self._transient(transient_failures, str(exc))
                continue
            if response.status == 429:
                self._note_rate_limit(response)
                continue
            if response.status >= 500:
                transient_failures = self._transient(
                    transient_failures, "%s %s answered %d" % (method, path, response.status))
                continue
            return response

    def _transient(self, failures: int, reason: str) -> int:
        failures += 1
        if failures >= TRANSIENT_ATTEMPTS:
            raise ServiceError("%s: %s (gave up after %d attempts)"
                               % (self.name, reason, failures))
        self._log("%s: %s, retrying" % (self.name, reason))
        self._sleep(TRANSIENT_BACKOFF_SECONDS * failures)
        return failures

    def get_json(self, path: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        """GET `path` and parse the JSON body of a 2xx response.

        Any other status, or a 2xx with an unparsable body, is a
        `ServiceError`: a read endpoint that does not answer with data cannot
        be reconciled against.
        """
        attempts = 0
        while True:
            response = self.request("GET", path, params=params)
            if not 200 <= response.status < 300:
                raise ServiceError("%s: GET %s answered %d" % (self.name, path, response.status))
            try:
                return response.json()
            except ValueError as exc:
                attempts += 1
                if attempts >= TRANSIENT_ATTEMPTS:
                    raise ServiceError("%s: GET %s returned an unreadable body: %s"
                                       % (self.name, path, exc)) from exc
                self._log("%s: unreadable body from %s, retrying" % (self.name, path))
                self._sleep(TRANSIENT_BACKOFF_SECONDS * attempts)

    def post_json(self, path: str, payload: Any,
                  headers: Optional[Mapping[str, str]] = None) -> Response:
        """POST `payload` as JSON; see `request` for the retry policy."""
        return self.request("POST", path, payload=payload, headers=headers)

    # -- pagination --------------------------------------------------------

    def iter_pages(self, path: str, key: str) -> Iterator[Any]:
        """Yield every record under `key` across all pages of `path`.

        Starts at page 1 and follows `next_page` until it is null. A page
        whose body is not an object, or whose `key` is not a list, yields
        nothing from that page; a `next_page` that is not a positive integer
        or that was already visited ends the walk, so a confused service can
        never loop the tool forever.
        """
        page: Optional[int] = 1
        seen: set = set()
        while page is not None and page not in seen:
            seen.add(page)
            body = self.get_json(path, {"page": page})
            records = body.get(key) if isinstance(body, dict) else None
            if isinstance(records, list):
                for record in records:
                    yield record
            else:
                self._log("%s: page %d of %s carried no %r list" % (self.name, page, path, key))
            page = _next_page(body)


def _next_page(body: Any) -> Optional[int]:
    if not isinstance(body, dict):
        return None
    value = body.get("next_page")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _stderr_log(message: str) -> None:
    print("orgsync: %s" % message, file=sys.stderr)


def make_service(name: str, base_url: str, **kwargs: Any) -> Service:
    """Build a `Service` over a fresh `HttpClient` for `base_url`."""
    return Service(name, HttpClient(base_url), **kwargs)


__all__ = ["Service", "ServiceError", "make_service", "parse_retry_after"]
