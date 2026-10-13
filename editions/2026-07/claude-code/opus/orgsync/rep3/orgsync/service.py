"""One service, seen as sync needs to see it: paginated and rate limited.

`http_client` moves bytes and interprets nothing; the reconciliation in `sync`
wants whole collections. This layer is the bit in between, and it owns exactly
two policies:

* **Rate limiting.** A `429` is not data and not a failure — it is the service
  saying "not yet". The answer carries `Retry-After`, and the rule is that this
  service receives no further request until that long has passed. A `Service`
  therefore remembers a per-service deadline and waits for it before every
  request, so the pause applies to whatever request comes next, not merely to
  the retry of the one that was refused.
* **Pagination.** Follow `next_page` until it is null, and hand back the whole
  list.

Everything else — what a record means, whether it is usable — stays out of
here. One `Service` per service: the deadline is per service, because the two
services rate limit independently.
"""

from __future__ import annotations

import datetime
import email.utils
import time
from typing import Any, Callable, List, Mapping, Optional

from .http_client import HttpClient, Response

STATUS_OK = 200
STATUS_TOO_MANY_REQUESTS = 429

#: Used when a 429 arrives without a usable `Retry-After`.
DEFAULT_RETRY_AFTER_SECONDS = 1.0

#: A service asking for longer than this is treated as asking for this long;
#: keeps a malformed or hostile header from parking the run forever.
MAX_RETRY_AFTER_SECONDS = 120.0

#: Added to every wait. "At least Retry-After seconds" is a lower bound, and
#: clocks — ours and the service's — do not agree to the microsecond.
RETRY_AFTER_SLACK_SECONDS = 0.05

#: Guard rails. Neither is expected to be reached; both keep a misbehaving
#: service from turning the run into an infinite loop.
MAX_ATTEMPTS = 100
MAX_PAGES = 1000


class ServiceError(Exception):
    """A service answered in a way the run cannot continue from."""


def retry_after_seconds(headers: Any,
                        now: Optional[datetime.datetime] = None) -> float:
    """Seconds to wait, read from a `Retry-After` header.

    Accepts the delay-seconds form and, defensively, the HTTP-date form.
    Anything missing, unparseable or negative yields a sane default rather
    than an exception: a rate limit must never be the thing that fails a run.
    """
    raw = None
    if headers is not None:
        try:
            raw = headers.get("Retry-After")
        except AttributeError:
            raw = None
    if raw is None:
        return DEFAULT_RETRY_AFTER_SECONDS

    text = str(raw).strip()
    try:
        seconds = float(text)
    except ValueError:
        seconds = _http_date_delay(text, now)
    if seconds != seconds:  # NaN
        return DEFAULT_RETRY_AFTER_SECONDS
    return max(0.0, min(seconds, MAX_RETRY_AFTER_SECONDS))


def _http_date_delay(text: str, now: Optional[datetime.datetime]) -> float:
    """Seconds from `now` until the HTTP-date `text`, or the default."""
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return DEFAULT_RETRY_AFTER_SECONDS
    if when is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    reference = now or datetime.datetime.now(datetime.timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=datetime.timezone.utc)
    return (when - reference).total_seconds()


class Service:
    """A rate-limit-aware, pagination-aware view of one service.

    Wraps an `HttpClient`. `sleep` and `clock` are injectable so the waiting
    behaviour can be tested without spending the time; `clock` must be a
    monotonic-style source of seconds.
    """

    def __init__(self, name: str, client: HttpClient,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 max_attempts: int = MAX_ATTEMPTS) -> None:
        self.name = name
        self.client = client
        self._sleep = sleep
        self._clock = clock
        self._max_attempts = max_attempts
        #: Clock reading before which this service must not be spoken to.
        self._blocked_until: Optional[float] = None

    # -- requests ---------------------------------------------------------

    def request(self, method: str, path: str,
                params: Optional[Mapping[str, Any]] = None,
                payload: Optional[Any] = None,
                headers: Optional[Mapping[str, str]] = None) -> Response:
        """Issue one request, waiting out and retrying rate limits.

        Returns the first response that is not a `429`, whatever its status —
        deciding what a status means is still the caller's job.
        """
        for _ in range(self._max_attempts):
            self._wait_for_slot()
            response = self.client.request(method, path, params=params,
                                           payload=payload, headers=headers)
            if response.status != STATUS_TOO_MANY_REQUESTS:
                return response
            self._note_rate_limit(response)
        raise ServiceError("%s: still rate limited after %d attempts at %s %s"
                           % (self.name, self._max_attempts, method, path))

    def get(self, path: str, params: Optional[Mapping[str, Any]] = None,
            headers: Optional[Mapping[str, str]] = None) -> Response:
        """GET `path`. See `request`."""
        return self.request("GET", path, params=params, headers=headers)

    def post_json(self, path: str, payload: Any,
                  headers: Optional[Mapping[str, str]] = None) -> Response:
        """POST `payload` as JSON to `path`. See `request`."""
        return self.request("POST", path, payload=payload, headers=headers)

    def _wait_for_slot(self) -> None:
        """Block until this service is allowed to receive a request again."""
        if self._blocked_until is None:
            return
        remaining = self._blocked_until - self._clock()
        if remaining > 0:
            self._sleep(remaining)
        self._blocked_until = None

    def _note_rate_limit(self, response: Response) -> None:
        """Record how long this service asked to be left alone for."""
        delay = retry_after_seconds(response.headers) + RETRY_AFTER_SLACK_SECONDS
        self._blocked_until = self._clock() + delay

    # -- pagination -------------------------------------------------------

    def fetch_all(self, path: str, key: str) -> List[Any]:
        """Every record under `key`, following `next_page` until it is null.

        Records are returned exactly as received — not validated, not
        deduplicated, not even required to be objects.
        """
        records: List[Any] = []
        seen_pages = set()
        page: Optional[int] = 1
        while page is not None:
            if page in seen_pages or len(seen_pages) >= MAX_PAGES:
                raise ServiceError("%s: pagination of %s does not terminate"
                                   % (self.name, path))
            seen_pages.add(page)
            body = self._fetch_page(path, page)
            batch = body.get(key)
            if isinstance(batch, list):
                records.extend(batch)
            page = _parse_next_page(body.get("next_page"))
        return records

    def _fetch_page(self, path: str, page: int) -> dict:
        """One page of `path`, as a JSON object."""
        response = self.get(path, params={"page": page})
        if response.status != STATUS_OK:
            raise ServiceError("%s: GET %s?page=%d returned %d"
                               % (self.name, path, page, response.status))
        try:
            body = response.json()
        except ValueError as exc:
            raise ServiceError("%s: GET %s?page=%d returned invalid JSON: %s"
                               % (self.name, path, page, exc)) from exc
        if not isinstance(body, dict):
            raise ServiceError("%s: GET %s?page=%d did not return an object"
                               % (self.name, path, page))
        return body


def _parse_next_page(value: Any) -> Optional[int]:
    """The next page number, or None when the last page has been reached.

    Anything that is not a page number — null, absent, a bool, junk — ends the
    walk, because there is no further page it could name.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None
