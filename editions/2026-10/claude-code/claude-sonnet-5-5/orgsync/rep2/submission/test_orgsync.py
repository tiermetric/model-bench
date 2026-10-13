import json
import os
import shutil
import tempfile
import unittest

from orgsync.config import load_config
from orgsync.sync import Service, run_sync, valid_account, valid_item


def read(path):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


class ValidationTest(unittest.TestCase):
    def test_items(self):
        good = {"id": "a", "owner": "o", "status": "open", "points": 0, "x": 1}
        self.assertTrue(valid_item(good))
        for patch in ({"points": "3"}, {"points": -1}, {"points": True},
                      {"status": "weird"}, {"owner": None}, {"id": 5}):
            self.assertFalse(valid_item(dict(good, **patch)), patch)
        self.assertFalse(valid_item({"id": "a"}))
        self.assertFalse(valid_item("x"))

    def test_accounts(self):
        good = {"id": "a", "owner": "o", "active": False}
        self.assertTrue(valid_account(good))
        self.assertFalse(valid_account(dict(good, active="true")))
        self.assertFalse(valid_account(dict(good, active=1)))
        self.assertFalse(valid_account({"id": "a", "owner": "o"}))


class SyncTest(unittest.TestCase):
    """Runs against the live services named in config.json."""

    def test_sync_twice(self):
        config = load_config("config.json")
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out)
        self.assertEqual(run_sync(config, out), 0)
        first = {n: sorted(map(json.dumps, read(os.path.join(out, n))))
                 for n in ("summary.jsonl", "orphans.jsonl", "errors.jsonl")}
        ledger = Service(config.ledger_url)
        accounts = [a for a in ledger.pages("/v1/accounts", "accounts") if valid_account(a)]
        active = {a["owner"] for a in accounts if a["active"]}
        summaries = ledger.pages("/v1/summaries", "summaries")
        self.assertEqual({s["owner"] for s in summaries}, active)
        self.assertEqual(len(summaries), len(active))
        self.assertEqual(run_sync(config, out), 0)
        second = {n: sorted(map(json.dumps, read(os.path.join(out, n))))
                  for n in first}
        self.assertEqual(first, second)
        for line in read(os.path.join(out, "orphans.jsonl")):
            self.assertNotIn(line["owner"], active)


if __name__ == "__main__":
    unittest.main()
