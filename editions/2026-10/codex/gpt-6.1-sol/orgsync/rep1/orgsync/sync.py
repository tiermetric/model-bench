"""Reconcile Directory items with Ledger accounts and log the results."""

from __future__ import annotations

import json
from pathlib import Path
import time

from .config import Config
from .http_client import HttpClient

DEFAULT_OUT_DIR = "out"


def _request(client, method, path, **kwargs):
    """Retry rate-limited requests only after the service's deadline."""
    while True:
        response = client.request(method, path, **kwargs)
        if response.status != 429:
            return response
        deadline = time.monotonic() + max(0.0, float(response.headers["Retry-After"]))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(remaining)


def _records(client, collection):
    page = 1
    while page is not None:
        response = _request(client, "GET", "/v1/" + collection, params={"page": page})
        if response.status != 200:
            raise RuntimeError("GET %s returned %s" % (response.url, response.status))
        data = response.json()
        yield from data[collection]
        page = data["next_page"]


def _account(record):
    return (isinstance(record, dict)
            and type(record.get("id")) is str
            and type(record.get("owner")) is str
            and type(record.get("active")) is bool)


def _item(record):
    return (isinstance(record, dict)
            and type(record.get("id")) is str
            and type(record.get("owner")) is str
            and type(record.get("status")) is str
            and record["status"] in ("open", "done")
            and type(record.get("points")) is int
            and record["points"] >= 0)


def _encoded(record):
    return json.dumps(record, sort_keys=True, ensure_ascii=True)


def _write_records(path, records):
    # Canonical JSON distinguishes JSON types and deduplicates repeated errors.
    lines = sorted({_encoded(record) for record in records})
    content = "".join(line + "\n" for line in lines)
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Read all service data, post missing summaries, and keep deduplicated logs."""
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)
    errors = []
    active = set()
    for record in _records(ledger, "accounts"):
        if not _account(record):
            errors.append({"service": "ledger", "record": record})
        elif record["active"]:
            active.add(record["owner"])

    summaries = {owner: {"owner": owner, "open_items": 0, "done_items": 0,
                         "total_points": 0} for owner in active}
    seen_items = set()
    orphans = []
    for record in _records(directory, "items"):
        if not _item(record):
            errors.append({"service": "directory", "record": record})
            continue
        if record["id"] in seen_items:
            continue
        seen_items.add(record["id"])
        if record["owner"] not in active:
            orphans.append(record)
            continue
        summary = summaries[record["owner"]]
        summary[record["status"] + "_items"] += 1
        summary["total_points"] += record["points"]

    existing = {record["owner"] for record in _records(ledger, "summaries")
                if isinstance(record, dict) and type(record.get("owner")) is str}

    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    _write_records(output / "orphans.jsonl", orphans)
    _write_records(output / "errors.jsonl", errors)
    history = output / "summary.jsonl"
    # This file records posts made by this tool, including earlier invocations.
    if not history.exists():
        history.touch()
    logged = {line for line in history.read_text(encoding="utf-8").splitlines() if line}
    for owner in sorted(active - existing):
        summary = summaries[owner]
        response = _request(ledger, "POST", "/v1/summaries", payload=summary,
                            headers={"Idempotency-Key": "sync-" + owner})
        if response.status not in (200, 201):
            raise RuntimeError("POST %s returned %s" % (response.url, response.status))
        line = _encoded(summary)
        if line not in logged:
            with history.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            logged.add(line)
    return 0
