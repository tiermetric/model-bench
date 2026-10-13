"""The `sync` command: reconcile the Directory and the Ledger."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .config import Config
from .http_client import HttpClient, HttpClientError

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

MAX_ATTEMPTS = 8


class SyncError(Exception):
    """A request could not be completed."""


class Service:
    """An HttpClient plus the rate-limit clock for one service."""

    def __init__(self, base_url: str) -> None:
        self.client = HttpClient(base_url)
        self.not_before = 0.0

    def call(self, method: str, path: str, params=None, payload=None, headers=None):
        failures = 0
        while True:
            wait = self.not_before - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                resp = self.client.request(method, path, params=params,
                                           payload=payload, headers=headers)
            except HttpClientError as exc:
                failures += 1
                if failures >= MAX_ATTEMPTS:
                    raise SyncError(str(exc)) from exc
                self.not_before = time.monotonic() + 0.5
                continue
            if resp.status == 429:
                try:
                    delay = float(resp.headers.get("Retry-After", "1"))
                except (TypeError, ValueError):
                    delay = 1.0
                self.not_before = time.monotonic() + max(delay, 0.0) + 0.05
                continue
            if resp.status >= 500:
                failures += 1
                if failures >= MAX_ATTEMPTS:
                    raise SyncError("%s %s -> %d" % (method, path, resp.status))
                self.not_before = time.monotonic() + 0.5
                continue
            return resp

    def pages(self, path: str, key: str) -> Iterator[Any]:
        """Yield every record under `key` across all pages."""
        page: Optional[int] = 1
        seen = set()
        while page is not None and page not in seen:
            seen.add(page)
            resp = self.call("GET", path, params={"page": page})
            if resp.status != 200:
                raise SyncError("GET %s page %s -> %d" % (path, page, resp.status))
            try:
                body = resp.json()
            except ValueError as exc:
                raise SyncError("bad JSON from %s: %s" % (path, exc)) from exc
            records = body.get(key) if isinstance(body, dict) else None
            if not isinstance(records, list):
                raise SyncError("unexpected body from %s" % path)
            for record in records:
                yield record
            nxt = body.get("next_page")
            page = nxt if isinstance(nxt, int) and not isinstance(nxt, bool) else None


def _is_str(v: Any) -> bool:
    return isinstance(v, str)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def valid_item(r: Any) -> bool:
    return (isinstance(r, dict) and _is_str(r.get("id")) and _is_str(r.get("owner"))
            and r.get("status") in ("open", "done") and _is_str(r.get("status"))
            and _is_int(r.get("points")) and r["points"] >= 0)


def valid_account(r: Any) -> bool:
    return (isinstance(r, dict) and _is_str(r.get("id")) and _is_str(r.get("owner"))
            and isinstance(r.get("active"), bool))


def _canon(r: Any) -> str:
    return json.dumps(r, sort_keys=True)


def _write_lines(path: str, rows: List[Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    os.replace(tmp, path)


def _read_lines(path: str) -> List[Any]:
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        pass
    return rows


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`."""
    directory = Service(config.directory_url)
    ledger = Service(config.ledger_url)
    errors: Dict[str, Dict[str, Any]] = {}

    def bad(service: str, record: Any) -> None:
        row = {"service": service, "record": record}
        errors.setdefault(_canon(row), row)

    # Read everything before any write.
    active_owners = set()
    for rec in ledger.pages("/v1/accounts", "accounts"):
        if not valid_account(rec):
            bad("ledger", rec)
        elif rec["active"]:
            active_owners.add(rec["owner"])

    items: Dict[str, Dict[str, Any]] = {}
    for rec in directory.pages("/v1/items", "items"):
        if not valid_item(rec):
            bad("directory", rec)
        else:
            items.setdefault(rec["id"], rec)

    posted_owners = set()
    for rec in ledger.pages("/v1/summaries", "summaries"):
        if isinstance(rec, dict) and _is_str(rec.get("owner")):
            posted_owners.add(rec["owner"])

    totals: Dict[str, Dict[str, int]] = {
        o: {"open_items": 0, "done_items": 0, "total_points": 0} for o in active_owners}
    orphans = []
    for item in items.values():
        t = totals.get(item["owner"])
        if t is None:
            orphans.append(item)
            continue
        t["open_items" if item["status"] == "open" else "done_items"] += 1
        t["total_points"] += item["points"]

    os.makedirs(out_dir, exist_ok=True)
    summary_path = os.path.join(out_dir, "summary.jsonl")

    posted = _read_lines(summary_path)
    for owner in sorted(totals):
        if owner in posted_owners:
            continue
        body = {"owner": owner, **totals[owner]}
        resp = ledger.call("POST", "/v1/summaries", payload=body,
                           headers={"Idempotency-Key": "sync-" + owner})
        if resp.status not in (200, 201):
            raise SyncError("POST summary for %r -> %d" % (owner, resp.status))
        if body not in posted:
            posted.append(body)

    _write_lines(summary_path, posted)
    _write_lines(os.path.join(out_dir, "orphans.jsonl"), orphans)
    _write_lines(os.path.join(out_dir, "errors.jsonl"), list(errors.values()))
    return 0
