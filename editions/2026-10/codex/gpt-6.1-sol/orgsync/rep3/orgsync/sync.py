"""Reconcile Directory items with authoritative Ledger accounts."""

from __future__ import annotations

import json
from pathlib import Path
import time

from .config import Config
from .http_client import HttpClient
from .models import Summary

DEFAULT_OUT_DIR = "out"


def _request(client, method, path, **kwargs):
    """Retry rate limits synchronously, blocking every request to this service."""
    while True:
        response = client.request(method, path, **kwargs)
        if response.status != 429:
            return response
        deadline = time.monotonic() + max(0.0, float(response.headers["Retry-After"]))
        # Recheck the deadline in case sleep returns early.
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(remaining)


def _pages(client, collection):
    page = 1
    while page is not None:
        response = _request(client, "GET", "/v1/" + collection,
                            params={"page": page})
        if response.status != 200:
            raise RuntimeError("GET %s returned %s" % (response.url, response.status))
        data = response.json()
        yield from data[collection]
        page = data["next_page"]


def _strings(record, *fields):
    return isinstance(record, dict) and all(type(record.get(f)) is str for f in fields)


def _account(record):
    return _strings(record, "id", "owner") and type(record.get("active")) is bool


def _item(record):
    return (_strings(record, "id", "owner", "status")
            and record["status"] in ("open", "done")
            and type(record.get("points")) is int and record["points"] >= 0)


def _summary(record):
    return (_strings(record, "owner")
            and all(type(record.get(f)) is int and record[f] >= 0
                    for f in ("open_items", "done_items", "total_points")))


def _encoded(record):
    return json.dumps(record, sort_keys=True, ensure_ascii=True)


def _write_records(path, records):
    # Stable serialization also makes repeated deliveries of malformed records
    # collapse even when JSON object fields arrive in a different order.
    content = "".join(line + "\n" for line in sorted({_encoded(r) for r in records}))
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Read every page, post missing summaries, and write deduplicated JSONL."""
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)
    errors = []

    def valid_records(client, collection, validator, service):
        for record in _pages(client, collection):
            if validator(record):
                yield record
            else:
                errors.append({"service": service, "record": record})

    accounts = list(valid_records(ledger, "accounts", _account, "ledger"))
    items = list(valid_records(directory, "items", _item, "directory"))
    existing = list(valid_records(ledger, "summaries", _summary, "ledger"))
    summaries = {a["owner"]: Summary(a["owner"]).to_dict()
                 for a in accounts if a["active"]}
    orphans = []
    seen_items = set()
    for item in items:
        if item["id"] in seen_items:
            continue
        seen_items.add(item["id"])
        summary = summaries.get(item["owner"])
        if summary is None:
            orphans.append(item)
        else:
            summary[item["status"] + "_items"] += 1
            summary["total_points"] += item["points"]

    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    _write_records(output / "orphans.jsonl", orphans)
    _write_records(output / "errors.jsonl", errors)
    history = output / "summary.jsonl"
    if not history.exists():
        history.touch()
    recorded = {line.strip() for line in history.read_text(encoding="utf-8").splitlines()
                if line.strip()}
    owners_present = {s["owner"] for s in existing}
    for owner, summary in summaries.items():
        if owner in owners_present:
            continue
        response = _request(ledger, "POST", "/v1/summaries", payload=summary,
                            headers={"Idempotency-Key": "sync-" + owner})
        if response.status not in (200, 201):
            raise RuntimeError("POST %s returned %s" % (response.url, response.status))
        line = _encoded(summary)
        if line not in recorded:
            with history.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            recorded.add(line)
    return 0
