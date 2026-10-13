"""The `sync` command: reconcile the Directory and the Ledger.

Everything else in the package is in place — config loading, the HTTP client,
the record types. What lives here is the part that has to make decisions, and
it is not written yet.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from .config import Config
from .http_client import HttpClient, Response

#: Directory the command writes its output files into.
DEFAULT_OUT_DIR = "out"


def _is_item(record: Any) -> bool:
    """Return whether *record* has the Directory item shape exactly."""
    return (isinstance(record, dict)
            and type(record.get("id")) is str
            and type(record.get("owner")) is str
            and record.get("status") in ("open", "done")
            and type(record.get("points")) is int
            and record["points"] >= 0)


def _is_account(record: Any) -> bool:
    """Return whether *record* has the Ledger account shape exactly."""
    return (isinstance(record, dict)
            and type(record.get("id")) is str
            and type(record.get("owner")) is str
            and type(record.get("active")) is bool)


def _is_summary(record: Any) -> bool:
    """A summary read from Ledger must be a genuine summary to count as one."""
    return (isinstance(record, dict)
            and type(record.get("owner")) is str
            and all(type(record.get(field)) is int and record[field] >= 0
                    for field in ("open_items", "done_items", "total_points")))


def _record_key(record: Any) -> str:
    """A JSON value's stable identity, used to make diagnostics unique."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _json_lines(records: Iterable[Any]) -> str:
    return "".join(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
                   for record in records)


def _write_if_changed(path: str, content: str) -> None:
    """Avoid changing an output file on an idempotent invocation."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            if handle.read() == content:
                return
    except FileNotFoundError:
        pass
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _request_with_retry(client: HttpClient, method: str, path: str,
                        *, params: Optional[Mapping[str, Any]] = None,
                        payload: Any = None,
                        headers: Optional[Mapping[str, str]] = None) -> Response:
    """Make a request, observing this service's Retry-After responses."""
    while True:
        response = client.request(method, path, params=params, payload=payload,
                                  headers=headers)
        if response.status != 429:
            return response
        try:
            delay = float(response.headers.get("Retry-After", "0"))
        except (TypeError, ValueError):
            delay = 0.0
        # A malformed header is not permission to busy-loop, while valid zero
        # values remain valid and are retried immediately.
        time.sleep(max(0.0, delay))


def _get_pages(client: HttpClient, path: str, collection: str) -> List[Any]:
    """Fetch every ordinary paginated collection response from one service."""
    page = 1
    records: List[Any] = []
    while True:
        response = _request_with_retry(client, "GET", path, params={"page": page})
        if response.status != 200:
            raise RuntimeError("GET %s returned HTTP %d" % (response.url, response.status))
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get(collection), list):
            raise RuntimeError("invalid page returned by %s" % response.url)
        records.extend(body[collection])
        next_page = body.get("next_page")
        if next_page is None:
            return records
        if type(next_page) is not int or next_page < 1:
            raise RuntimeError("invalid next_page returned by %s" % response.url)
        page = next_page


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Run one reconciliation pass against the two services in `config`.

    Writes its output files under `out_dir`, creating it if absent, and
    returns a process exit code (0 on success).
    """
    os.makedirs(out_dir, exist_ok=True)
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)

    # Reading happens before deciding what to post, so the decision is based on
    # one complete view of each collection.
    accounts_raw = _get_pages(ledger, "/v1/accounts", "accounts")
    items_raw = _get_pages(directory, "/v1/items", "items")
    summaries_raw = _get_pages(ledger, "/v1/summaries", "summaries")

    errors: List[Dict[str, Any]] = []
    error_keys: Set[Tuple[str, str]] = set()

    def add_error(service: str, record: Any) -> None:
        key = (service, _record_key(record))
        if key not in error_keys:
            error_keys.add(key)
            errors.append({"service": service, "record": record})

    accounts: List[Dict[str, Any]] = []
    for account in accounts_raw:
        if _is_account(account):
            accounts.append(account)
        else:
            add_error("ledger", account)

    items: Dict[str, Dict[str, Any]] = {}
    for item in items_raw:
        if not _is_item(item):
            add_error("directory", item)
        elif item["id"] not in items:
            # Item IDs identify a work item, even if it was returned on more
            # than one page.
            items[item["id"]] = item

    existing_owners: Set[str] = set()
    for summary in summaries_raw:
        if _is_summary(summary):
            existing_owners.add(summary["owner"])
        else:
            add_error("ledger", summary)

    active_owners = {account["owner"] for account in accounts if account["active"]}
    totals: Dict[str, Dict[str, int]] = {
        owner: {"open_items": 0, "done_items": 0, "total_points": 0}
        for owner in active_owners
    }
    orphans: List[Dict[str, Any]] = []
    for item in items.values():
        owner = item["owner"]
        if owner not in active_owners:
            orphans.append(item)
            continue
        totals[owner][item["status"] + "_items"] += 1
        totals[owner]["total_points"] += item["points"]

    required = [
        {"owner": owner, "open_items": totals[owner]["open_items"],
         "done_items": totals[owner]["done_items"],
         "total_points": totals[owner]["total_points"]}
        for owner in sorted(active_owners)
    ]
    posted: List[Dict[str, Any]] = []
    for summary in required:
        if summary["owner"] in existing_owners:
            continue
        response = _request_with_retry(
            ledger, "POST", "/v1/summaries", payload=summary,
            headers={"Idempotency-Key": "sync-" + summary["owner"]})
        if response.status not in (200, 201):
            raise RuntimeError("POST %s returned HTTP %d" % (response.url, response.status))
        posted.append(summary)

    _write_if_changed(os.path.join(out_dir, "errors.jsonl"), _json_lines(errors))
    _write_if_changed(os.path.join(out_dir, "orphans.jsonl"), _json_lines(orphans))

    # This log represents writes made by this tool, rather than a snapshot.
    # Keep prior lines on a repeat run, and append only genuinely new posts.
    summary_path = os.path.join(out_dir, "summary.jsonl")
    try:
        with open(summary_path, "r", encoding="utf-8") as handle:
            prior = handle.read()
    except FileNotFoundError:
        prior = ""
    # A log may survive a ledger reset.  A repeated body still has one line:
    # JSONL outputs are sets of observed records, not an unbounded event log.
    prior_keys: Set[str] = set()
    for line in prior.splitlines():
        try:
            prior_keys.add(_record_key(json.loads(line)))
        except ValueError:
            # The tool only writes valid JSONL itself.  Preserve manually
            # supplied content rather than discarding it while appending.
            pass
    new_posts = [summary for summary in posted if _record_key(summary) not in prior_keys]
    separator = "\n" if prior and not prior.endswith("\n") and new_posts else ""
    _write_if_changed(summary_path, prior + separator + _json_lines(new_posts))
    return 0
