"""Rate-limit-aware access to one service: pagination, retries, and the 429 gate.

`HttpClient` moves bytes and reports every status. This layer decides what a
status means for a sync run:

- A `429` sets a per-service gate: no further request to that service until
  `Retry-After` seconds have passed. The same request is then retried, without
  limit, because a rate limit never abandons the work.
- A `5xx` is retried a few times with a short backoff, then the run gives up.
- Any other unexpected outcome raises `SyncError`.

The clock and the sleep are injectable so the gate can be tested without
waiting.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable, List, Mapping, Optional

from .http_client import HttpClient, HttpClientError, Response

#: Wait used when a `429` carries no usable `Retry-After`.
DEFAULT_RETRY_AFTER_SECONDS = 1.0
#: Attempts for one request that keeps answering `5xx`.
MAX_SERVER_ERROR_ATTEMPTS = 3
#: Backoff after the first `5xx`; it grows by one step per further failure.
SERVER_ERROR_BACKOFF_SECONDS = 0.5


class SyncError(Exception):
    """A service answered in a way the run cannot continue past."""


def retry_after_seconds(headers: Any) -> float:
    """Seconds named by a `Retry-After` header, or the default if unusable."""
    raw = headers.get("Retry-After") if headers is not None else None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS
    if not math.isfinite(value) or value < 0:
        return DEFAULT_RETRY_AFTER_SECONDS
    return value


class ServiceClient:
    """Every request to one service goes through here, sharing one gate."""

    def __init__(self, name: str, http: HttpClient,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.name = name
        self._http = http
        self._clock = clock
        self._sleep = sleep
        self._not_before = 0.0

    def _wait_for_gate(self) -> None:
        # Loop rather than sleep once: a sleep may return a hair early.
        while True:
            remaining = self._not_before - self._clock()
            if remaining <= 0:
                return
            self._sleep(remaining)

    def send(self, method: str, path: str,
             params: Optional[Mapping[str, Any]] = None,
             payload: Optional[Any] = None,
             headers: Optional[Mapping[str, str]] = None) -> Response:
        """Issue one request, honoring the gate, `429`, and `5xx` retries."""
        server_errors = 0
        while True:
            self._wait_for_gate()
            try:
                response = self._http.request(method, path, params=params,
                                              payload=payload, headers=headers)
            except HttpClientError as exc:
                raise SyncError("%s: %s" % (self.name, exc)) from exc

            if response.status == 429:
                self._not_before = self._clock() + retry_after_seconds(response.headers)
                continue
            if response.status >= 500:
                server_errors += 1
                if server_errors >= MAX_SERVER_ERROR_ATTEMPTS:
                    raise SyncError("%s %s: status %d after %d attempts"
                                    % (method, path, response.status, server_errors))
                self._not_before = self._clock() + SERVER_ERROR_BACKOFF_SECONDS * server_errors
                continue
            return response

    def fetch_all(self, path: str, key: str) -> List[Any]:
        """Follow `next_page` from page 1 to null and return every record under `key`."""
        records: List[Any] = []
        visited = set()
        page = 1
        while True:
            visited.add(page)
            response = self.send("GET", path, params={"page": page})
            if response.status != 200:
                raise SyncError("GET %s page %d: unexpected status %d"
                                % (path, page, response.status))
            body = _parse_json(response, "GET %s page %d" % (path, page))
            batch = body.get(key)
            if not isinstance(batch, list):
                raise SyncError("GET %s page %d: %r is not a list" % (path, page, key))
            records.extend(batch)

            next_page = body.get("next_page")
            if next_page is None:
                return records
            if type(next_page) is not int or next_page in visited:
                raise SyncError("GET %s page %d: bad next_page %r" % (path, page, next_page))
            page = next_page


def _parse_json(response: Response, what: str) -> dict:
    try:
        body = response.json()
    except ValueError as exc:
        raise SyncError("%s: body is not JSON: %s" % (what, exc)) from exc
    if not isinstance(body, dict):
        raise SyncError("%s: body is not a JSON object" % what)
    return body
