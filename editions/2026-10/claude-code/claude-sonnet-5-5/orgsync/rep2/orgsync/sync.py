"""The `sync` command: reconcile the Directory and the Ledger.

Everything else in the package is in place — config loading, the HTTP client,
the record types. What lives here is the part that has to make decisions, and
it is not written yet.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

from .config import Config
from .http_client import HttpClient, HttpClientError

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

MAX_ATTEMPTS = 8


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def valid_item(r: Any) -> bool:
    return (isinstance(r, dict) and isinstance(r.get("id"), str)
            and isinstance(r.get("owner"), str)
            and r.get("status") in ("open", "done") and isinstance(r.get("status"), str)
            and _is_int(r.get("points")) and r["points"] >= 0)


def valid_account(r: Any) -> bool:
    return (isinstance(r, dict) and isinstance(r.get("id"), str)
            and isinstance(r.get("owner"), str) and isinstance(r.get("active"), bool))


def _retry_after(resp) -> float:
    try:
        return min(max(float(resp.headers.get("Retry-After", 1)), 0.0), 600.0)
    except (TypeError, ValueError):
        return 1.0


class Service:
    """An HttpClient that honors 429 Retry-After per service."""

    def __init__(self, base_url: str) -> None:
        self.client = HttpClient(base_url)
        self.not_before = 0.0

    def call(self, method: str, path: str, **kw):
        failures = 0
        while True:
            wait = self.not_before - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                resp = self.client.request(method, path, **kw)
            except HttpClientError:
                failures += 1
                if failures >= MAX_ATTEMPTS:
                    raise
                time.sleep(0.5)
                continue
            if resp.status == 429:
                self.not_before = time.monotonic() + _retry_after(resp) + 0.05
                continue
            if resp.status >= 500 and method == "GET" and failures < MAX_ATTEMPTS:
                failures += 1
                time.sleep(0.2)
                continue
            return resp

    def pages(self, path: str, key: str) -> List[Any]:
        records: List[Any] = []
        page: Any = 1
        seen = set()
        while page is not None and page not in seen:
            seen.add(page)
            resp = self.call("GET", path, params={"page": page})
            if resp.status != 200:
                raise RuntimeError("GET %s page %s -> %d" % (path, page, resp.status))
            body = resp.json()
            records.extend(body.get(key) or [])
            page = body.get("next_page")
        return records


def _dump(rec: Any) -> str:
    return json.dumps(rec, sort_keys=True)


def _write_lines(path: str, lines: List[str]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")
    os.replace(tmp, path)


def _read_lines(path: str) -> List[Dict[str, Any]]:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    v = json.loads(line)
                except ValueError:
                    continue
                if isinstance(v, dict):
                    out.append(v)
    except OSError:
        pass
    return out


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    directory = Service(config.directory_url)
    ledger = Service(config.ledger_url)

    raw_accounts = ledger.pages("/v1/accounts", "accounts")
    raw_items = directory.pages("/v1/items", "items")
    raw_summaries = ledger.pages("/v1/summaries", "summaries")

    errors: Dict[str, Dict[str, Any]] = {}

    def bad(service: str, rec: Any) -> None:
        entry = {"service": service, "record": rec}
        errors.setdefault(_dump(entry), entry)

    active_owners = set()
    for r in raw_accounts:
        if not valid_account(r):
            bad("ledger", r)
        elif r["active"]:
            active_owners.add(r["owner"])

    items: Dict[str, Dict[str, Any]] = {}
    for r in raw_items:
        if not valid_item(r):
            bad("directory", r)
        else:
            items.setdefault(r["id"], r)

    totals = {o: {"owner": o, "open_items": 0, "done_items": 0, "total_points": 0}
              for o in active_owners}
    orphans = []
    for r in items.values():
        t = totals.get(r["owner"])
        if t is None:
            orphans.append(r)
            continue
        t["open_items" if r["status"] == "open" else "done_items"] += 1
        t["total_points"] += r["points"]

    have = {s.get("owner") for s in raw_summaries if isinstance(s, dict)}

    os.makedirs(out_dir, exist_ok=True)
    summary_path = os.path.join(out_dir, "summary.jsonl")
    posted: Dict[str, Dict[str, Any]] = {}
    for e in _read_lines(summary_path):
        o = e.get("owner")
        if o in totals and o in have:
            posted[o] = e

    for owner in sorted(totals):
        if owner in have:
            continue
        body = totals[owner]
        resp = ledger.call("POST", "/v1/summaries", payload=body,
                           headers={"Idempotency-Key": "sync-" + owner})
        if resp.status in (200, 201):
            posted[owner] = body

    _write_lines(summary_path, [_dump(v) for v in posted.values()])
    _write_lines(os.path.join(out_dir, "orphans.jsonl"), [_dump(r) for r in orphans])
    _write_lines(os.path.join(out_dir, "errors.jsonl"), list(errors))
    return 0
