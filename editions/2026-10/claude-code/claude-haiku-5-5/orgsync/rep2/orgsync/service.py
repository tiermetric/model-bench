"""Rate-limited, paginated access to one service.

`ServiceClient` is the only way the sync logic talks to a service. A 429 stops
every further request to that service for `Retry-After` seconds, after which the
same request is retried; a 429 is never treated as an answer. Transport failures
and 5xx responses are retried a bounded number of times. Any other status is
returned to the caller, which decides what it means. `fetch_all` follows
`next_page` from page 1 until it is null.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable, List, Mapping, Optional, Set

from .http_client import HttpClient, HttpClientError, Response

#: Wait for a 429 whose Retry-After is missing or unusable.
DEFAULT_RETRY_AFTER_SECONDS = 1.0
#: Added to every wait so that clock granularity cannot make a retry land early.
WAIT_MARGIN_SECONDS = 0.05
#: Attempts for a request that fails in transport or returns 5xx before giving up.
MAX_ATTEMPTS = 5
#: Base wait between those attempts; it grows with the attempt number.
BACKOFF_SECONDS = 0.5


class ServiceError(Exception):
    """A service could not give a usable answer."""


def parse_retry_after(value: Optional[str]) -> float:
    """Seconds to wait for a Retry-After header value, falling back to the default."""
    if value is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    try:
        seconds = float(value.strip())
    except ValueError:
        return DEFAULT_RETRY_AFTER_SECONDS
    if not math.isfinite(seconds) or seconds < 0:
        return DEFAULT_RETRY_AFTER_SECONDS
    return seconds


class ServiceClient:
    """Sends requests to one named service, honouring its rate limit.

    `clock` and `sleep` are injectable so tests can run the waiting without
    real delays.
    """

    def __init__(self, name: str, http: HttpClient,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.name = name
        self._http = http
        self._clock = clock
        self._sleep = sleep
        #: Monotonic instant before which no request may be sent; None until
        #: the first wait is needed.
        self._not_before: Optional[float] = None

    def request(self, method: str, path: str,
                params: Optional[Mapping[str, Any]] = None,
                payload: Optional[Any] = None,
                headers: Optional[Mapping[str, str]] = None) -> Response:
        """Send one request and return its response, waiting out rate limits.

        Raises `ServiceError` only when transport failures or 5xx responses
        persist past `MAX_ATTEMPTS`.
        """
        failures = 0
        while True:
            self._wait_for_turn()
            try:
                response = self._http.request(method, path, params=params,
                                              payload=payload, headers=headers)
            except HttpClientError as exc:
                failures += 1
                if failures >= MAX_ATTEMPTS:
                    raise ServiceError("%s: %s %s failed %d times: %s"
                                       % (self.name, method, path, failures, exc)) from exc
                self._hold(BACKOFF_SECONDS * failures)
                continue

            if response.status == 429:
                self._hold(parse_retry_after(response.headers.get("Retry-After")))
                continue

            if response.status >= 500:
                failures += 1
                if failures >= MAX_ATTEMPTS:
                    raise ServiceError("%s: %s %s returned HTTP %d on %d attempts"
                                       % (self.name, method, path, response.status, failures))
                self._hold(BACKOFF_SECONDS * failures)
                continue

            return response

    def get(self, path: str, params: Optional[Mapping[str, Any]] = None) -> Response:
        return self.request("GET", path, params=params)

    def _hold(self, seconds: float) -> None:
        """Forbid requests to this service for `seconds` from now."""
        self._not_before = self._clock() + seconds + WAIT_MARGIN_SECONDS

    def _wait_for_turn(self) -> None:
        if self._not_before is None:
            return
        remaining = self._not_before - self._clock()
        if remaining > 0:
            self._sleep(remaining)


def fetch_all(client: ServiceClient, path: str, key: str) -> List[Any]:
    """Return the `key` list from every page of `path`, following `next_page`.

    Page 1 is requested first and the walk ends at the first `next_page` that
    is null. A page that is not a 200 with a JSON object holding a list under
    `key`, or a `next_page` that is neither an integer nor null, is a
    `ServiceError`: the page is unusable as a whole, unlike a single record.
    """
    records: List[Any] = []
    seen: Set[int] = set()
    page: Optional[int] = 1
    while page is not None:
        seen.add(page)
        response = client.get(path, {"page": page})
        if response.status != 200:
            raise ServiceError("%s: GET %s page %d returned HTTP %d"
                               % (client.name, path, page, response.status))
        body = _decode_page(client, path, page, key, response)
        records.extend(body[key])
        page = _next_page(client, path, body, seen)
    return records


def _decode_page(client: ServiceClient, path: str, page: int, key: str,
                 response: Response) -> Any:
    try:
        body = response.json()
    except ValueError as exc:
        raise ServiceError("%s: GET %s page %d is not JSON: %s"
                           % (client.name, path, page, exc)) from exc
    if not isinstance(body, dict) or not isinstance(body.get(key), list):
        raise ServiceError("%s: GET %s page %d has no list under %r"
                           % (client.name, path, page, key))
    return body


def _next_page(client: ServiceClient, path: str, body: Mapping[str, Any],
               seen: Set[int]) -> Optional[int]:
    value = body.get("next_page")
    if value is None:
        return None
    if type(value) is not int or value in seen:
        raise ServiceError("%s: GET %s gave an unusable next_page: %r"
                           % (client.name, path, value))
    return value
