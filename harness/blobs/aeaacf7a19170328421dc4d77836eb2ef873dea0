"""Vacuous-pass guard target: a `sync` that runs, exits 0, and writes nothing.

SEALED control. Installed into a repo skeleton as `orgsync/sync.py`. It is not
a wrong *implementation* — it is the degenerate submission the judge must never
report as a pass: it talks to both services, returns success, and produces no
`out/` directory and no output files at all.

It reads correctly — every page, waiting out every 429 — precisely so the only
assertions it trips are the ones about producing an answer.

Expected judge verdict: FAIL (A00 out-dir, A02/A03/A04/A05 files, A06 no write
made, A09 files, A10 keys).
"""

from __future__ import annotations

import time
from typing import Any, List

from .config import Config
from .http_client import HttpClient

DEFAULT_OUT_DIR = "out"


def _read(client: HttpClient, path: str, key: str) -> List[Any]:
    records: List[Any] = []
    page: Any = 1
    while page is not None:
        response = client.get(path, params={"page": page})
        while response.status == 429:
            time.sleep(float(response.headers.get("Retry-After") or 1) + 0.02)
            response = client.get(path, params={"page": page})
        if response.status != 200:
            return records
        payload = response.json()
        records.extend(payload.get(key) or [])
        page = payload.get("next_page")
    return records


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Look busy; decide nothing; write nothing; report success."""
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)
    _read(ledger, "/v1/accounts", "accounts")
    _read(directory, "/v1/items", "items")
    _read(ledger, "/v1/summaries", "summaries")
    return 0
