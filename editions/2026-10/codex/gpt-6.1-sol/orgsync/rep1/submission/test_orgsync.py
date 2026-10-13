"""Offline reconciliation tests using a stateful, rate-limited service double."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orgsync.__main__ import main
from orgsync.config import Config
from orgsync.http_client import Response
from orgsync.sync import run_sync


def item(identifier, owner="kim", status="open", points=3, **extra):
    return dict(id=identifier, owner=owner, status=status, points=points, **extra)


class Services:
    def __init__(self, accounts, items, existing=(), rate_limit=False):
        self.pages = {"accounts": accounts, "items": items}
        self.summaries = list(existing)
        self.calls = []
        self.keys = {}
        self.now = 0.0
        self.blocked = {}
        self.limited = set()
        self.rate_limit = rate_limit

    def sleep(self, seconds):
        self.now += seconds

    def request(self, client, method, path, **kwargs):
        service = client.base_url
        if self.now < self.blocked.get(service, 0):
            raise AssertionError("request during service cooldown")
        self.calls.append((service, method, path, kwargs))
        token = (service, method, path, kwargs.get("params", {}).get("page"))
        if self.rate_limit and token not in self.limited:
            self.limited.add(token)
            self.blocked[service] = self.now + 0.25
            return Response(429, {"Retry-After": "0.25"}, b'not data', service)
        collection = path.rsplit("/", 1)[-1]
        if method == "GET":
            if collection == "summaries":
                # Existing summaries are also paginated, with nonconsecutive pages.
                pages = {1: (self.summaries[:1], 7), 7: (self.summaries[1:], None)}
            else:
                pages = self.pages[collection]
            records, next_page = pages[kwargs["params"]["page"]]
            data = {collection: records, "next_page": next_page}
            return Response(200, {}, json.dumps(data).encode(), service)
        payload = kwargs["payload"]
        key = kwargs["headers"]["Idempotency-Key"]
        assert key == "sync-" + payload["owner"]
        active = {record["owner"] for records, _ in self.pages["accounts"].values()
                  for record in records if isinstance(record, dict)
                  and type(record.get("id")) is str
                  and type(record.get("owner")) is str
                  and record.get("active") is True}
        if payload["owner"] not in active:
            raise AssertionError("rejected POST: owner is not active")
        if key in self.keys:
            if self.keys[key] != payload:
                raise AssertionError("rejected POST: conflicting idempotency key")
            status = 200
        else:
            self.keys[key] = dict(payload)
            self.summaries.append(dict(payload))
            status = 201
        return Response(status, {}, b'{}', service)


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / "out"
        self.config = Config("http://directory", "http://ledger")

    def run_service(self, service, cli=False):
        with patch("orgsync.sync.HttpClient.request", autospec=True,
                   side_effect=service.request), \
             patch("orgsync.sync.time.monotonic", side_effect=lambda: service.now), \
             patch("orgsync.sync.time.sleep", side_effect=service.sleep):
            if cli:
                config_path = Path(self.temp.name) / "config.json"
                config_path.write_text(json.dumps(self.config.__dict__), encoding="utf-8")
                return main(["sync", "--config", str(config_path), "--out-dir", str(self.out)])
            return run_sync(self.config, str(self.out))

    def records(self, name):
        return [json.loads(line) for line in (self.out / name).read_text().splitlines()]

    def test_full_sync_and_second_run_are_idempotent(self):
        bad_account = {"id": "bad", "owner": "ghost", "active": 1}
        bad_item = item("bad", points=True)
        orphan = item("orphan", owner="ghost", note={"keep": [1, 2]})
        inactive = item("inactive", owner="off")
        existing = {"owner": "present", "open_items": 99, "done_items": 0,
                    "total_points": 99}
        service = Services(
            {1: ([{"id": "a", "owner": "kim", "active": True, "extra": 1},
                  bad_account, {"id": "off", "owner": "off", "active": False}], 4),
             4: ([{"id": "z", "owner": "zero", "active": True},
                  {"id": "p", "owner": "present", "active": True}, bad_account], None)},
            {1: ([item("1"), item("2", points=4), bad_item, orphan], 9),
             9: ([item("1"), item("3", status="done", points=2), orphan,
                  inactive, bad_item, item("present", owner="present")], None)},
            [existing], rate_limit=True)
        self.assertEqual(self.run_service(service, cli=True), 0)
        self.assertCountEqual(self.records("summary.jsonl"), [
            {"owner": "kim", "open_items": 2, "done_items": 1, "total_points": 9},
            {"owner": "zero", "open_items": 0, "done_items": 0, "total_points": 0}])
        self.assertCountEqual(self.records("orphans.jsonl"), [orphan, inactive])
        self.assertCountEqual(self.records("errors.jsonl"), [
            {"service": "ledger", "record": bad_account},
            {"service": "directory", "record": bad_item}])
        snapshots = {p.name: p.read_bytes() for p in self.out.iterdir()}
        before = len(service.calls)
        self.assertEqual(self.run_service(service), 0)
        self.assertTrue(all(call[1] == "GET" for call in service.calls[before:]))
        self.assertEqual(snapshots, {p.name: p.read_bytes() for p in self.out.iterdir()})
        # Every page was requested, using the returned next_page rather than +1.
        gets = {(call[2], call[3]["params"]["page"]) for call in service.calls
                if call[1] == "GET"}
        self.assertEqual(gets, {("/v1/accounts", 1), ("/v1/accounts", 4),
                               ("/v1/items", 1), ("/v1/items", 9),
                               ("/v1/summaries", 1), ("/v1/summaries", 7)})
        first_post = next(i for i, call in enumerate(service.calls) if call[1] == "POST")
        self.assertTrue(any(call[2] == "/v1/summaries" and
                            call[3]["params"]["page"] == 7
                            for call in service.calls[:first_post]))
        self.assertGreater(service.now, 0)

    def test_malformed_json_types_missing_fields_and_ranges(self):
        invalid_items = [None, [], "record", 4, True, {},
                         item("a", owner=2), item(2), item("b", status="closed"),
                         item("c", points=-1), item("d", points=1.0),
                         item("e", points="1"), item("f", points=False),
                         {"id": "g", "owner": "kim", "points": 1}]
        invalid_accounts = [None, [], "account", {},
                            {"id": 1, "owner": "kim", "active": True},
                            {"id": "a", "owner": 1, "active": True},
                            {"id": "b", "owner": "kim", "active": "true"},
                            {"id": "c", "owner": "kim"}]
        valid = item("valid", points=0, extra=True)
        service = Services({1: (invalid_accounts, None)},
                           {1: (invalid_items + [valid], None)})
        self.assertEqual(self.run_service(service), 0)
        self.assertEqual(self.records("summary.jsonl"), [])
        self.assertEqual(self.records("orphans.jsonl"), [valid])
        self.assertCountEqual(self.records("errors.jsonl"),
                             [{"service": "directory", "record": r} for r in invalid_items]
                             + [{"service": "ledger", "record": r} for r in invalid_accounts])
        self.assertFalse(any(call[1] == "POST" for call in service.calls))

    def test_empty_services_create_three_empty_files(self):
        service = Services({1: ([], None)}, {1: ([], None)})
        self.assertEqual(self.run_service(service), 0)
        self.assertEqual({p.name: p.read_bytes() for p in self.out.iterdir()},
                         {"summary.jsonl": b"", "errors.jsonl": b"", "orphans.jsonl": b""})


if __name__ == "__main__":
    unittest.main()
