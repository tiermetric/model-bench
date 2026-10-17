"""W1 — "the naive read": the failure mode the orgsync task exists to catch.

SEALED control (CONTROLS.md). Installed into a repo skeleton as
`orgsync/sync.py`; it is deliberately WRONG and must FAIL the judge.

The shape of the mistake (the haiku-shaped hypothesis): treat the Directory as
the whole world. Summarize every owner that appears on a work item, consult the
Ledger only as a place to write to, and let the HTTP layer's status codes go
unread. Everything it does looks like progress; nothing it does is
reconciliation.

Concretely, against the prompt:

* rule 1 — reads every page of `/v1/items`, and NEVER reads `/v1/accounts` or
  `/v1/summaries` at all;
* rule 2 — "active" is never consulted, so inactive and malformed accounts are
  as good as active ones, and an active account with no items gets nothing;
* rule 3 — has no notion of an orphan; `out/orphans.jsonl` is never written;
* rule 4 — a malformed item is repaired (`points` defaults to 0) instead of
  skipped, so it enters the counts; `out/errors.jsonl` is never written; a
  duplicate delivery is counted twice, because ids are never deduplicated;
* rule 5 — posts a summary for every owner it invented, and never looks at the
  response, so the ledger's 422s are invisible to it;
* rule 6 — retries a 429 immediately on reads and drops it entirely on writes;
* rule 7 — recomputes and re-posts on every run.

Expected judge verdict: FAIL. This control is deliberately NOT precise — it is
the whole-board failure, and the assertions it trips are the map of what the
task measures.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from .config import Config
from .http_client import HttpClient

DEFAULT_OUT_DIR = "out"

#: Immediate re-attempts after a 429. No wait: "the service will probably let
#: me through next time" is exactly the reasoning this control encodes.
IMMEDIATE_RETRIES = 3


def _fetch_all(client: HttpClient, path: str, key: str) -> List[Any]:
    """Page through `path`, retrying a 429 straight away with no delay."""
    records: List[Any] = []
    page = 1
    while page is not None:
        response = client.get(path, params={"page": page})
        for _ in range(IMMEDIATE_RETRIES):
            if response.status != 429:
                break
            response = client.get(path, params={"page": page})   # no wait
        payload = response.json()
        records.extend(payload.get(key) or [])
        page = payload.get("next_page")
    return records


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Summarize the Directory and push the result at the Ledger."""
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)

    items = _fetch_all(directory, "/v1/items", "items")

    totals: Dict[str, Dict[str, Any]] = {}
    for item in items:
        owner = item.get("owner")
        if not isinstance(owner, str):
            continue
        summary = totals.setdefault(
            owner,
            {"owner": owner, "open_items": 0, "done_items": 0, "total_points": 0},
        )
        # A missing/odd `points` is "repaired" rather than treated as malformed,
        # and no id is remembered, so a duplicate delivery counts twice.
        points = item.get("points")
        if not isinstance(points, int) or isinstance(points, bool):
            points = 0
        if item.get("status") == "done":
            summary["done_items"] += 1
        else:
            summary["open_items"] += 1
        summary["total_points"] += points

    posted = []
    for owner in sorted(totals):
        body = totals[owner]
        # The response is never inspected: a 422 rejection and a 429 rate limit
        # are both silently treated as "sent, therefore done".
        ledger.post_json("/v1/summaries", body,
                         headers={"Idempotency-Key": "sync-%s" % owner})
        posted.append(body)

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "summary.jsonl"), "w", encoding="utf-8") as handle:
        for body in posted:
            handle.write(json.dumps(body, separators=(",", ":")) + "\n")
    return 0
