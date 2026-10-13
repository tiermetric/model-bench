import json
import os
import subprocess
import sys
import tempfile
import unittest

from orgsync.config import load_config
from orgsync.sync import Service, valid_account, valid_item

ROOT = os.path.dirname(os.path.abspath(__file__))


def read(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


class ValidationTests(unittest.TestCase):
    def test_item(self):
        good = {"id": "a", "owner": "o", "status": "open", "points": 0, "x": 1}
        self.assertTrue(valid_item(good))
        for bad in ({**good, "points": "3"}, {**good, "points": True}, {**good, "points": -1},
                    {**good, "status": "x"}, {"id": "a"}, [], {**good, "owner": 5}):
            self.assertFalse(valid_item(bad))

    def test_account(self):
        good = {"id": "a", "owner": "o", "active": False}
        self.assertTrue(valid_account(good))
        self.assertFalse(valid_account({**good, "active": "yes"}))
        self.assertFalse(valid_account({"id": "a", "owner": "o"}))


class SyncTests(unittest.TestCase):
    def run_sync(self, out):
        return subprocess.run([sys.executable, "-m", "orgsync", "sync", "--out-dir", out],
                              cwd=ROOT, capture_output=True, text=True)

    def test_sync_correct_and_idempotent(self):
        cfg = load_config(os.path.join(ROOT, "config.json"))
        with tempfile.TemporaryDirectory() as out:
            self.assertEqual(self.run_sync(out).returncode, 0)
            snap = {n: open(os.path.join(out, n)).read()
                    for n in ("summary.jsonl", "orphans.jsonl", "errors.jsonl")}
            self.assertEqual(self.run_sync(out).returncode, 0)
            for n, content in snap.items():
                self.assertEqual(content, open(os.path.join(out, n)).read())

            # Every required summary is present in the ledger and matches.
            ledger = Service(cfg.ledger_url)
            posted = {s["owner"]: s for s in ledger.pages("/v1/summaries", "summaries")}
            owners = set()
            for a in ledger.pages("/v1/accounts", "accounts"):
                if valid_account(a) and a["active"]:
                    owners.add(a["owner"])
            self.assertEqual(owners, set(posted))
            items = {}
            for i in Service(cfg.directory_url).pages("/v1/items", "items"):
                if valid_item(i):
                    items[i["id"]] = i
            for o in owners:
                mine = [i for i in items.values() if i["owner"] == o]
                self.assertEqual(posted[o]["open_items"], sum(i["status"] == "open" for i in mine))
                self.assertEqual(posted[o]["done_items"], sum(i["status"] == "done" for i in mine))
                self.assertEqual(posted[o]["total_points"], sum(i["points"] for i in mine))
            orphans = read(os.path.join(out, "orphans.jsonl"))
            self.assertEqual({i["id"] for i in orphans},
                             {i["id"] for i in items.values() if i["owner"] not in owners})
            errs = read(os.path.join(out, "errors.jsonl"))
            for e in errs:
                self.assertIn(e["service"], ("directory", "ledger"))


if __name__ == "__main__":
    unittest.main()
