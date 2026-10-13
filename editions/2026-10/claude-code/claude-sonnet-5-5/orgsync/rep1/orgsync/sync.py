"""The `sync` command: reconcile the Directory and the Ledger.

Everything else in the package is in place — config loading, the HTTP client,
the record types. What lives here is the part that has to make decisions, and
it is not written yet.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterator, List, Optional

from .config import Config
from .http_client import HttpClient, HttpClientError

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"

MAX_ATTEMPTS = 6


class Service:
    """One service's client plus its rate-limit state."""

    def __init__(self, base_url: str) -> None:
        self.client = HttpClient(base_url)
        self.not_before = 0.0

    def _wait(self) -> None:
        delay = self.not_before - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def call(self, method: str, path: str, params=None, payload=None, headers=None):
        """Issue a request, honoring 429/Retry-After; retry transient failures."""
        failures = 0
        while True:
            self._wait()
            try:
                resp = self.client.request(method, path, params=params,
                                           payload=payload, headers=headers)
            except HttpClientError:
                failures += 1
                if failures >= MAX_ATTEMPTS:
                    raise
                self.not_before = time.monotonic() + 0.5 * failures
                continue
            if resp.status == 429:
                try:
                    wait = float(resp.headers.get("Retry-After", 1))
                except (TypeError, ValueError):
                    wait = 1.0
                self.not_before = time.monotonic() + max(wait, 0.0) + 0.05
                continue
            if resp.status >= 500 and method == "GET":
                failures += 1
                if failures >= MAX_ATTEMPTS:
                    return resp
                self.not_before = time.monotonic() + 0.5 * failures
                continue
            return resp

    def pages(self, path: str, key: str) -> Iterator[Any]:
        page: Any = 1
        seen = set()
        while page is not None and page not in seen:
            seen.add(page)
            resp = self.call("GET", path, params={"page": page})
            if resp.status != 200:
                raise RuntimeError("GET %s page %s: HTTP %d" % (path, page, resp.status))
            body = resp.json()
            records = body.get(key, [])
            if isinstance(records, list):
                for rec in records:
                    yield rec
            page = body.get("next_page")
            if isinstance(page, bool) or not isinstance(page, int):
                page = None


def _is_str(v: Any) -> bool:
    return isinstance(v, str)


def _int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def valid_item(r: Any) -> bool:
    return (isinstance(r, dict) and _is_str(r.get("id")) and _is_str(r.get("owner"))
            and r.get("status") in ("open", "done") and _is_str(r.get("status"))
            and _int(r.get("points")) and r["points"] >= 0)


def valid_account(r: Any) -> bool:
    return (isinstance(r, dict) and _is_str(r.get("id")) and _is_str(r.get("owner"))
            and isinstance(r.get("active"), bool))


def _line(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _write_lines(path: str, lines: List[str]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for ln in lines:
            fh.write(ln + "\n")
    os.replace(tmp, path)


def _read_lines(path: str) -> List[str]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [ln.rstrip("\n") for ln in fh if ln.strip()]


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    directory = Service(config.directory_url)
    ledger = Service(config.ledger_url)
    os.makedirs(out_dir, exist_ok=True)

    errors: Dict[str, str] = {}

    def add_error(service: str, rec: Any) -> None:
        ln = _line({"service": service, "record": rec})
        errors.setdefault(ln, ln)

    active_owners = set()
    for rec in ledger.pages("/v1/accounts", "accounts"):
        if not valid_account(rec):
            add_error("ledger", rec)
        elif rec["active"]:
            active_owners.add(rec["owner"])

    existing = set()
    for rec in ledger.pages("/v1/summaries", "summaries"):
        if isinstance(rec, dict) and _is_str(rec.get("owner")):
            existing.add(rec["owner"])

    items: Dict[str, Dict[str, Any]] = {}
    for rec in directory.pages("/v1/items", "items"):
        if not valid_item(rec):
            add_error("directory", rec)
        else:
            items.setdefault(rec["id"], rec)

    totals = {o: {"owner": o, "open_items": 0, "done_items": 0, "total_points": 0}
              for o in active_owners}
    orphans: Dict[str, str] = {}
    for item in items.values():
        t = totals.get(item["owner"])
        if t is None:
            orphans[item["id"]] = _line(item)
            continue
        t["open_items" if item["status"] == "open" else "done_items"] += 1
        t["total_points"] += item["points"]

    summary_path = os.path.join(out_dir, "summary.jsonl")
    posted = _read_lines(summary_path)
    for owner in sorted(totals):
        if owner in existing:
            continue
        body = totals[owner]
        resp = ledger.call("POST", "/v1/summaries", payload=body,
                           headers={"Idempotency-Key": "sync-" + owner})
        if resp.status in (200, 201):
            ln = _line(body)
            if ln not in posted:
                posted.append(ln)
        else:
            raise RuntimeError("POST summary for %r: HTTP %d" % (owner, resp.status))

    _write_lines(summary_path, posted)
    _write_lines(os.path.join(out_dir, "orphans.jsonl"), list(orphans.values()))
    _write_lines(os.path.join(out_dir, "errors.jsonl"), list(errors.values()))
    return 0
