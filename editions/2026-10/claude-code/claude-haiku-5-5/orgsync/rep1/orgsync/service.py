"""Rate limiting and retries for calls to one service.

`RateLimitedService` wraps an `HttpClient` and applies two rules to its own
service only. After a 429 it sends nothing until `Retry-After` seconds have
passed, then retries the same request. A transport failure or a 5xx is retried
a few times with a short backoff, then the run gives up with `ServiceError`.
Callers therefore only ever see a final response, and never a 429.

The clock and sleep are injectable so the waiting can be tested without sleeping.
"""

from __future__ import annotations

import email.utils
import math
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional

from .http_client import HttpClient, HttpClientError, Response

#: Wait used when a 429 carries no usable Retry-After.
DEFAULT_RETRY_AFTER_SECONDS = 1.0
#: Attempts for a request that fails in transport or with a 5xx.
MAX_TRANSIENT_ATTEMPTS = 3
#: Backoff per transient failure; multiplied by the attempt number.
TRANSIENT_BACKOFF_SECONDS = 0.5


class ServiceError(Exception):
    """The run cannot complete: a service is unreachable or answers unusably."""


def retry_after_seconds(headers: Any) -> float:
    """Seconds to wait for a 429 response's `Retry-After` header.

    Accepts delta-seconds (the form the services send) or an HTTP date. A
    missing, unparseable, or non-finite value falls back to the default.
    """
    raw = headers.get("Retry-After") if headers is not None else None
    if raw is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    raw = raw.strip()
    try:
        seconds = float(raw)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return DEFAULT_RETRY_AFTER_SECONDS
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - datetime.now(timezone.utc)).total_seconds()
    if not math.isfinite(seconds):
        return DEFAULT_RETRY_AFTER_SECONDS
    return max(0.0, seconds)


class RateLimitedService:
    """Every request to one service goes through `request`."""

    def __init__(self, client: Any, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.client = client
        self._clock = clock
        self._sleep = sleep
        #: Monotonic instant before which this service may not be sent to.
        self._not_before = 0.0

    def request(self, method: str, path: str,
                params: Optional[Mapping[str, Any]] = None,
                payload: Optional[Any] = None,
                headers: Optional[Mapping[str, str]] = None) -> Response:
        """Send one request, honouring 429s and retrying transient failures."""
        failures = 0
        while True:
            self._wait_for_rate_limit()
            try:
                response = self.client.request(method, path, params=params,
                                               payload=payload, headers=headers)
            except HttpClientError as exc:
                failures = self._note_transient_failure(failures, "%s %s" % (method, path), exc)
                continue

            if response.status == 429:
                self._not_before = self._clock() + retry_after_seconds(response.headers)
                continue
            if response.status >= 500:
                failures = self._note_transient_failure(
                    failures, "%s %s" % (method, path), "HTTP %d" % response.status)
                continue
            return response

    def _wait_for_rate_limit(self) -> None:
        delay = self._not_before - self._clock()
        if delay > 0:
            self._sleep(delay)

    def _note_transient_failure(self, failures: int, what: str, cause: Any) -> int:
        """Count a transient failure; back off, or give up once attempts run out."""
        failures += 1
        if failures >= MAX_TRANSIENT_ATTEMPTS:
            raise ServiceError("%s failed after %d attempts: %s" % (what, failures, cause))
        self._sleep(TRANSIENT_BACKOFF_SECONDS * failures)
        return failures
