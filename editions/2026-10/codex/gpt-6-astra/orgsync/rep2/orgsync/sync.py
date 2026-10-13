"""Reconcile Directory items with Ledger accounts and journal successful posts."""

from __future__ import annotations

import json
from pathlib import Path
import time

from .config import Config
from .http_client import HttpClient
from .models import Summary

DEFAULT_OUT_DIR = "out"


def _request(client, method, path, **kwargs):
    """Retry rate limits before allowing any further requests to this service.

    The sync is sequential, so waiting here also gates all other endpoints on
    the same service. Use a monotonic deadline in case sleep returns early.
    """
    while True:
        response = client.request(method, path, **kwargs)
        if response.status != 429:
            return response
        deadline = time.monotonic() + float(response.headers.get("Retry-After", "0"))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(remaining)


def _records(client, collection):
    page = 1
    while page is not None:
        response = _request(client, "GET", "/v1/" + collection,
                            params={"page": page})
        if response.status != 200:
            raise RuntimeError("GET %s failed: HTTP %s" % (collection, response.status))
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


def _line(record):
    # Canonical JSON distinguishes booleans, integers and floats, and removes
    # object key order as a source of duplicate error records.
    return json.dumps(record, sort_keys=True, ensure_ascii=True)


def _write_records(path, records):
    lines = sorted({_line(record) for record in records})
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Read all pages, report unusable records, and post only missing owners."""
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)
    errors = []
    summaries = {}
    for record in _records(ledger, "accounts"):
        if not _account(record):
            errors.append({"service": "ledger", "record": record})
        elif record["active"]:
            summaries[record["owner"]] = Summary(record["owner"]).to_dict()

    seen_items = set()
    orphans = []
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

    # Existing summaries are authoritative evidence that no POST is needed,
    # even if their counters differ from the current Directory contents.
    existing = {record["owner"] for record in _records(ledger, "summaries")
                if isinstance(record, dict) and type(record.get("owner")) is str}

    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    _write_records(output / "orphans.jsonl", orphans)
    _write_records(output / "errors.jsonl", errors)
    # Retain the successful-post journal across runs. Flush each accepted post
    # so that a later request failure does not discard earlier successes.
    with (output / "summary.jsonl").open("a", encoding="utf-8") as journal:
        for owner in sorted(summaries):
            if owner in existing:
                continue
            body = summaries[owner]
            response = _request(ledger, "POST", "/v1/summaries", payload=body,
                                headers={"Idempotency-Key": "sync-" + owner})
            if response.status not in (200, 201):
                raise RuntimeError("POST summary for %r failed: HTTP %s" %
                                   (owner, response.status))
            journal.write(_line(body) + "\n")
            journal.flush()
    return 0
