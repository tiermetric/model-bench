"""Tests for orgsync.http_client.

No sockets: the client takes an injected opener, so these exercise real request
construction and real response handling against a fake transport.
"""

import email.message
import io
import unittest
import urllib.error

from orgsync.http_client import HttpClient, HttpClientError, Response


def make_headers(pairs=None):
    """An email.message.Message, which is what urllib hands back for headers."""
    message = email.message.Message()
    for key, value in (pairs or {}).items():
        message[key] = value
    return message


class FakeHTTPResponse:
    """Stands in for http.client.HTTPResponse: context manager + read()."""

    def __init__(self, status, body=b"", headers=None):
        self.status = status
        self.headers = make_headers(headers)
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeOpener:
    """Returns queued responses (or raises queued exceptions), recording calls."""

    def __init__(self, *outcomes):
        self.outcomes, self.requests, self.timeouts = list(outcomes), [], []

    def open(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class BuildUrlTest(unittest.TestCase):
    def setUp(self):
        self.client = HttpClient("http://example.test/", opener=FakeOpener())

    def test_strips_trailing_slash_and_joins_either_form_of_path(self):
        self.assertEqual(self.client.base_url, "http://example.test")
        self.assertEqual(self.client.build_url("/v1/things"), "http://example.test/v1/things")
        self.assertEqual(self.client.build_url("v1/things"), "http://example.test/v1/things")

    def test_encodes_params_and_drops_none_values(self):
        self.assertEqual(self.client.build_url("/v1/things", {"page": 2}),
                         "http://example.test/v1/things?page=2")
        self.assertEqual(self.client.build_url("/v1/things", {"page": None}),
                         "http://example.test/v1/things")

    def test_rejects_non_positive_timeout(self):
        with self.assertRaises(ValueError):
            HttpClient("http://example.test", timeout=0)


class GetTest(unittest.TestCase):
    def test_returns_status_headers_and_parsed_body(self):
        opener = FakeOpener(FakeHTTPResponse(200, b'{"ok": true}', {"X-Trace": "abc"}))
        client = HttpClient("http://example.test", timeout=7.0, opener=opener)

        response = client.get("/v1/things", {"page": 1})

        self.assertIsInstance(response, Response)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(response.headers.get("x-trace"), "abc")
        self.assertEqual(opener.requests[0].get_method(), "GET")
        self.assertEqual(opener.requests[0].full_url, "http://example.test/v1/things?page=1")
        self.assertIsNone(opener.requests[0].data)
        self.assertEqual(opener.timeouts[0], 7.0)

    def test_non_2xx_is_returned_not_raised_with_headers_and_body_intact(self):
        error = urllib.error.HTTPError(
            "http://example.test/v1/things", 503, "Service Unavailable",
            make_headers({"X-Trace": "def"}), io.BytesIO(b'{"error": "busy"}'))
        client = HttpClient("http://example.test", opener=FakeOpener(error))

        response = client.get("/v1/things")

        self.assertEqual(response.status, 503)
        self.assertEqual(response.headers.get("X-Trace"), "def")
        self.assertEqual(response.json(), {"error": "busy"})

    def test_transport_failure_raises_http_client_error(self):
        client = HttpClient("http://example.test",
                            opener=FakeOpener(urllib.error.URLError("connection refused")))
        with self.assertRaises(HttpClientError):
            client.get("/v1/things")


class PostJsonTest(unittest.TestCase):
    def test_sends_json_body_content_type_and_extra_headers(self):
        opener = FakeOpener(FakeHTTPResponse(201, b'{"stored": 1}'))
        client = HttpClient("http://example.test", opener=opener)

        response = client.post_json("/v1/things", {"a": 1}, {"X-Key": "k1"})

        request = opener.requests[0]
        self.assertEqual(response.status, 201)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.data, b'{"a": 1}')
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(request.get_header("X-key"), "k1")


class ResponseTest(unittest.TestCase):
    def test_text_replaces_undecodable_bytes(self):
        self.assertEqual(Response(200, make_headers(), b"\xff", "u").text, "�")

    def test_json_rejects_empty_and_invalid_bodies(self):
        with self.assertRaises(ValueError):
            Response(204, make_headers(), b"", "u").json()
        with self.assertRaises(ValueError):
            Response(200, make_headers(), b"not json", "u").json()


if __name__ == "__main__":
    unittest.main()
