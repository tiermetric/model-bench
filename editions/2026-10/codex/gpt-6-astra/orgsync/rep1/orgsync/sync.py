"""Reconcile Directory items with Ledger accounts and journal accepted writes."""

from __future__ import annotations

import json
from pathlib import Path
import time

from .config import Config
from .http_client import HttpClient, HttpClientError
from .models import Summary

DEFAULT_OUT_DIR = "out"


def _request(client, method, path, **kwargs):
    """Serialize requests and retry rate limits before allowing any more work."""
    while True:
        response = client.request(method, path, **kwargs)
        if response.status != 429:
            return response
        deadline = time.monotonic() + float(response.headers["Retry-After"])
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
            raise HttpClientError("GET %s returned %s" % (response.url, response.status))
        data = response.json()
        yield from data[collection]
        page = data["next_page"]


def _identity_fields(record):
    return (type(record) is dict and type(record.get("id")) is str
            and type(record.get("owner")) is str)


def _valid_account(record):
    return _identity_fields(record) and type(record.get("active")) is bool


def _valid_item(record):
    return (_identity_fields(record) and type(record.get("status")) is str
            and record["status"] in ("open", "done")
            and type(record.get("points")) is int and record["points"] >= 0)


def _line(record):
    # Canonical JSON also distinguishes booleans from integers for deduplication.
    return json.dumps(record, sort_keys=True, ensure_ascii=True) + "\n"


def _write_records(path, records):
    path.write_text("".join(sorted({_line(record) for record in records})),
                    encoding="utf-8")


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Read all three collections, reconcile, and post only missing owners.

    Errors and orphans describe this pass. summary.jsonl is a persistent journal
    of this tool's successful posts, so a no-op pass preserves its content.
    """
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)
    accounts = list(_records(ledger, "accounts"))
    items = list(_records(directory, "items"))
    existing = list(_records(ledger, "summaries"))

    errors = []
    summaries = {}
    for account in accounts:
        if not _valid_account(account):
            errors.append({"service": "ledger", "record": account})
        elif account["active"]:
            owner = account["owner"]
            summaries[owner] = Summary(owner).to_dict()

    orphans = []
    seen_items = set()
    for item in items:
        if not _valid_item(item):
            errors.append({"service": "directory", "record": item})
            continue
        if item["id"] in seen_items:
            continue
        seen_items.add(item["id"])
        summary = summaries.get(item["owner"])
        if summary is None:
            orphans.append(item)
        else:
            summary[item["status"] + "_items"] += 1
            summary["total_points"] += item["points"]

    # The summaries endpoint has no additional record schema in the contract.
    existing_owners = {record["owner"] for record in existing
                       if type(record) is dict and type(record.get("owner")) is str}
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    _write_records(output / "errors.jsonl", errors)
    _write_records(output / "orphans.jsonl", orphans)

    journal_path = output / "summary.jsonl"
    posted = set()
    if journal_path.exists():
        posted = {_line(json.loads(line)) for line in
                  journal_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    with journal_path.open("a", encoding="utf-8") as journal:
        for owner, summary in sorted(summaries.items()):
            if owner in existing_owners:
                continue
            response = _request(ledger, "POST", "/v1/summaries", payload=summary,
                                headers={"Idempotency-Key": "sync-" + owner})
            if response.status not in (200, 201):
                raise HttpClientError("POST %s returned %s" % (response.url, response.status))
            line = _line(summary)
            if line not in posted:
                journal.write(line)
                journal.flush()
                posted.add(line)
    return 0
