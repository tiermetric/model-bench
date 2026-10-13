"""A minimal JSON-over-HTTP client for one service, built on urllib.

Scope, deliberately: this layer moves bytes. It does **not** retry, does not
follow pagination, and does not treat any status code as an error — a 4xx or
5xx comes back as an ordinary `Response` for the caller to interpret, because
only the caller knows which status codes mean something for the endpoint it is
talking to. Transport failures (connection refused, timeout) are the one thing
it raises for, since there is no status code to hand back.

The opener is injectable so the client is testable without a socket.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Mapping, Optional

DEFAULT_TIMEOUT_SECONDS = 30.0
JSON_CONTENT_TYPE = "application/json"


class HttpClientError(Exception):
    """A request did not complete: connection failure, timeout, bad URL."""


class Response:
    """One HTTP response: `status` (any code), `headers`, `body` bytes, `url`."""

    __slots__ = ("status", "headers", "body", "url")

    def __init__(self, status: int, headers: Any, body: bytes, url: str) -> None:
        self.status = status
        self.headers = headers
        self.body = body
        self.url = url

    @property
    def text(self) -> str:
        """Body decoded as UTF-8, replacing anything undecodable."""
        return self.body.decode("utf-8", "replace")

    def json(self) -> Any:
        """Parse the body as JSON. Raises ValueError if empty or not JSON."""
        if not self.body:
            raise ValueError("empty response body from %s" % self.url)
        return json.loads(self.body.decode("utf-8"))

    def __repr__(self) -> str:
        return "<Response %d %s (%d bytes)>" % (self.status, self.url, len(self.body))


class HttpClient:
    """Talks to one service, identified by its base URL.

    One instance per service: the base URL is fixed at construction and every
    path is appended to it. Not thread-safe — the urllib opener is shared, so
    use one client per thread if requests are ever issued concurrently.
    """

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS,
                 opener: Optional[Any] = None) -> None:
        """`opener` is anything with `.open(request, timeout=...)`; the default
        is a plain urllib opener. `timeout` is per request, in seconds."""
        if timeout <= 0:
            raise ValueError("timeout must be positive, got %r" % (timeout,))
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = opener if opener is not None else urllib.request.build_opener()

    def build_url(self, path: str, params: Optional[Mapping[str, Any]] = None) -> str:
        """Join `path` onto the base URL, appending `params` as a query string.

        Params whose value is None are dropped, so an optional argument can be
        passed straight through.
        """
        if not path.startswith("/"):
            path = "/" + path
        url = self.base_url + path
        if params:
            pairs = [(k, v) for k, v in params.items() if v is not None]
            if pairs:
                url += "?" + urllib.parse.urlencode(pairs)
        return url

    def request(self, method: str, path: str,
                params: Optional[Mapping[str, Any]] = None,
                payload: Optional[Any] = None,
                headers: Optional[Mapping[str, str]] = None) -> Response:
        """Issue one request and return the response, whatever its status.

        `payload`, when not None, is sent as a JSON body with a JSON
        Content-Type (which `headers` may override). A 4xx/5xx is a normal
        return value; only a request that did not complete raises
        `HttpClientError`.
        """
        url = self.build_url(path, params)
        send_headers: Dict[str, str] = dict(headers or {})
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            send_headers.setdefault("Content-Type", JSON_CONTENT_TYPE)

        request = urllib.request.Request(url, data=data, method=method.upper(),
                                         headers=send_headers)
        try:
            with self._opener.open(request, timeout=self.timeout) as resp:
                return Response(resp.status, resp.headers, resp.read(), url)
        except urllib.error.HTTPError as exc:
            # Not an error at this layer: the status is the answer.
            return Response(exc.code, exc.headers, exc.read(), url)
        except urllib.error.URLError as exc:
            raise HttpClientError("%s %s failed: %s" % (method, url, exc.reason)) from exc
        except OSError as exc:  # socket timeout and friends
            raise HttpClientError("%s %s failed: %s" % (method, url, exc)) from exc

    def get(self, path: str, params: Optional[Mapping[str, Any]] = None,
            headers: Optional[Mapping[str, str]] = None) -> Response:
        """GET `path`. See `request`."""
        return self.request("GET", path, params=params, headers=headers)

    def post_json(self, path: str, payload: Any,
                  headers: Optional[Mapping[str, str]] = None) -> Response:
        """POST `payload` to `path` as JSON. See `request`."""
        return self.request("POST", path, payload=payload, headers=headers)
