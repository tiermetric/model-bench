"""Reconcile Directory items against Ledger accounts and retain a post journal."""

from __future__ import annotations

import json
from pathlib import Path
import time

from .config import Config
from .http_client import HttpClient
from .models import Summary

DEFAULT_OUT_DIR = "out"


def _request(client, method, path, **kwargs):
    """Retry the identical request, blocking all work during its cooldown."""
    while True:
        response = client.request(method, path, **kwargs)
        if response.status != 429:
            if response.status not in (200, 201):
                raise RuntimeError("%s %s returned HTTP %s" %
                                   (method, response.url, response.status))
            return response
        deadline = time.monotonic() + float(response.headers.get("Retry-After"))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(remaining)


def _records(client, resource):
    page = 1
    while page is not None:
        data = _request(client, "GET", "/v1/" + resource,
                        params={"page": page}).json()
        yield from data[resource]
        page = data["next_page"]


def _identity(record):
    return (isinstance(record, dict) and type(record.get("id")) is str
            and type(record.get("owner")) is str)


def _account(record):
    return _identity(record) and type(record.get("active")) is bool


def _item(record):
    return (_identity(record) and type(record.get("status")) is str
            and record["status"] in ("open", "done")
            and type(record.get("points")) is int and record["points"] >= 0)


def _line(record):
    # Canonical JSON also distinguishes booleans, integers and floating values.
    return json.dumps(record, sort_keys=True, ensure_ascii=True)


def _write_records(path, records):
    lines = sorted({_line(record) for record in records})
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Read all pages, reconcile, and journal only successfully posted bodies.

    errors/orphans describe the current inputs; summary.jsonl is a persistent
    journal so a no-write rerun retains the summaries this tool posted earlier.
    """
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)
    errors, orphans = [], []
    summaries = {}
    for record in _records(ledger, "accounts"):
        if not _account(record):
            errors.append({"service": "ledger", "record": record})
        elif record["active"]:
            summaries[record["owner"]] = Summary(record["owner"]).to_dict()

    seen_items = set()
    for record in _records(directory, "items"):
        if not _item(record):
            errors.append({"service": "directory", "record": record})
            continue
        if record["id"] in seen_items:
            continue
        seen_items.add(record["id"])
        summary = summaries.get(record["owner"])
        if summary is None:
            orphans.append(record)
        else:
            summary[record["status"] + "_items"] += 1
            summary["total_points"] += record["points"]

    existing = {record["owner"] for record in _records(ledger, "summaries")}
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    _write_records(output / "errors.jsonl", errors)
    _write_records(output / "orphans.jsonl", orphans)
    # Append after every success, preserving the journal on subsequent runs.
    with (output / "summary.jsonl").open("a", encoding="utf-8") as journal:
        for owner in sorted(summaries):
            if owner in existing:
                continue
            summary = summaries[owner]
            _request(ledger, "POST", "/v1/summaries", payload=summary,
                     headers={"Idempotency-Key": "sync-" + owner})
            journal.write(_line(summary) + "\n")
            journal.flush()
    return 0
