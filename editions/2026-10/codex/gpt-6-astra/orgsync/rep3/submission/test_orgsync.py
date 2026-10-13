"""Offline reconciliation tests; no requests leave the in-memory transport."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orgsync.__main__ import main
from orgsync.config import Config
from orgsync.http_client import Response
from orgsync.sync import run_sync


def account(owner, active=True):
    return {"id": "a-" + owner, "owner": owner, "active": active}


def item(identifier, owner="kim", status="open", points=3, **extra):
    return dict(id=identifier, owner=owner, status=status, points=points, **extra)


def summary(owner, opened=0, done=0, points=0):
    return dict(owner=owner, open_items=opened, done_items=done, total_points=points)


class Services:
    def __init__(self, accounts, items, existing=()):
        self.data = {"accounts": accounts, "items": items,
                     "summaries": list(existing)}
        self.calls = []
        self.posts = []
        self.limits = {}
        self.now = 0.0
        self.blocked = {}
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        # Simulate an early wakeup to check the deadline loop as well.
        self.now += min(seconds, 0.75)

    def client(self, base):
        services = self

        class Client:
            def request(self, method, path, **kwargs):
                resource = path.rsplit("/", 1)[1]
                page = kwargs.get("params", {}).get("page")
                key = (base, method, resource, page)
                if services.now < services.blocked.get(base, 0):
                    raise AssertionError("request before Retry-After elapsed")
                services.calls.append((key, kwargs))
                if services.limits.get(key, 0):
                    services.limits[key] -= 1
                    services.blocked[base] = services.now + 2.5
                    return Response(429, {"Retry-After": "2.5"}, b"not data", base)
                if method == "GET":
                    # Nonconsecutive page numbers ensure next_page is followed.
                    index = (page - 1) // 3
                    records = services.data[resource]
                    body = {resource: records[index:index + 1],
                            "next_page": page + 3 if index + 1 < len(records) else None}
                    return Response(200, {}, json.dumps(body).encode(), base)
                body = kwargs["payload"]
                owners = {a["owner"] for a in services.data["accounts"]
                          if isinstance(a, dict) and type(a.get("id")) is str
                          and type(a.get("owner")) is str and a.get("active") is True}
                if body["owner"] not in owners:
                    raise AssertionError("rejected owner")
                if any(s["owner"] == body["owner"] for s in services.data["summaries"]):
                    raise AssertionError("unnecessary/rejected write")
                if kwargs["headers"] != {"Idempotency-Key": "sync-" + body["owner"]}:
                    raise AssertionError("wrong idempotency key")
                services.posts.append(body.copy())
                services.data["summaries"].append(body.copy())
                return Response(201, {}, b"{}", base)
        return Client()


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name) / "out"
        self.config = Config("directory", "ledger")

    def run_sync(self, services):
        with patch("orgsync.sync.HttpClient", side_effect=services.client), \
             patch("orgsync.sync.time.monotonic", side_effect=lambda: services.now), \
             patch("orgsync.sync.time.sleep", side_effect=services.sleep):
            self.assertEqual(run_sync(self.config, str(self.output)), 0)

    def lines(self, name):
        return [json.loads(line) for line in
                (self.output / (name + ".jsonl")).read_text().splitlines()]

    def test_reconcile_all_pages_deduplicate_and_rerun(self):
        orphan = item("orphan", "disabled", extra={"keep": True})
        unknown = item("unknown", "missing")
        services = Services(
            [account("kim"), account("disabled", False), account("empty"), account("prior")],
            [item("one"), orphan, item("two", points=4), item("one"),
             item("three", status="done", points=2), orphan, unknown],
            [summary("unrelated"), summary("prior", 99, 99, 99)])
        self.run_sync(services)
        self.assertCountEqual(services.posts, [summary("kim", 2, 1, 9), summary("empty")])
        self.assertCountEqual(self.lines("orphans"), [orphan, unknown])
        self.assertEqual(self.lines("errors"), [])
        self.assertCountEqual(self.lines("summary"), services.posts)
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        services.calls.clear()
        self.run_sync(services)
        self.assertTrue(all(key[1] == "GET" for key, _ in services.calls))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.output.iterdir()})

    def test_malformed_records_are_logged_once_and_never_counted(self):
        bad_accounts = [None, [], "account", {}, account("bad", 1),
                        dict(account("bad2"), id=False), dict(account("bad3"), owner=7)]
        bad_items = [None, [], "item", {}, item("b", points=True), item("f", points=1.0),
                     item("n", points=-1), item("s", points="3"),
                     item("state", status=[]), item("state2", status="closed"),
                     item(7), item("owner", owner=False)]
        for field in ("id", "owner", "status", "points"):
            record = item("missing-" + field)
            del record[field]
            bad_items.append(record)
        services = Services([dict(account("kim"), ignored=[1])] + bad_accounts * 2,
                            [item("valid", ignored=True), item("orphan", "bad")] + bad_items * 2)
        self.run_sync(services)
        self.assertEqual(services.posts, [summary("kim", 1, 0, 3)])
        self.assertCountEqual(self.lines("errors"),
                              [{"service": "ledger", "record": r} for r in bad_accounts] +
                              [{"service": "directory", "record": r} for r in bad_items])
        self.assertEqual(self.lines("orphans"), [item("orphan", "bad")])

    def test_repeated_rate_limits_on_reads_and_posts(self):
        services = Services([account("kim"), account("empty")], [item("one")])
        services.limits = {("ledger", "GET", "accounts", 4): 2,
                           ("directory", "GET", "items", 1): 2,
                           ("ledger", "GET", "summaries", 1): 2,
                           ("ledger", "POST", "summaries", None): 2}
        self.run_sync(services)
        self.assertEqual(services.now, 20)
        self.assertTrue(services.sleeps)
        self.assertCountEqual(self.lines("summary"), [summary("kim", 1, 0, 3), summary("empty")])
        attempts = [kwargs for key, kwargs in services.calls if key[1] == "POST"]
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(attempts[1], attempts[2])

    def test_empty_services_create_three_empty_files_via_cli(self):
        services = Services([], [])
        with patch("orgsync.__main__.load_config", return_value=self.config), \
             patch("orgsync.sync.HttpClient", side_effect=services.client):
            self.assertEqual(main(["sync", "--out-dir", str(self.output)]), 0)
        self.assertEqual({p.name: p.read_bytes() for p in self.output.iterdir()},
                         {name + ".jsonl": b"" for name in ("errors", "orphans", "summary")})


if __name__ == "__main__":
    unittest.main()
