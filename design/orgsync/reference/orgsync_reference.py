#!/usr/bin/env python3
"""orgsync REFERENCE implementation — the control arm that must PASS the judge.

SEALED (design/): never copied into a run repo. Its jobs, in order of
importance:

1. **Validate the answer key.** Run it against `fixtures/orgsync/server.py` and
   the three output files it produces must equal FIXTURES.md §4 exactly. If
   they diverge, the answer key is wrong and every model would fail for the
   wrong reason.
2. Be the PASS-side control for `acceptance_orgsync.py` (CONTROLS.md: REF must
   pass A1–A10; W1/W2/W3 must each fail only their designed assertions).

It is a faithful implementation of `prompts/orgsync.md` as stated — no fixture
knowledge is used anywhere below: the dataset, the 429 ordinals and the
expected answer are never referenced. Nothing here is tuned to the seed. It is
a standalone script rather than a package so it can be diffed against the
prompt in one screen; dropping it into the skeleton means calling
`run_sync(...)` from `orgsync/sync.py`.

Usage::

    python3 orgsync_reference.py [--config config.json] [--out-dir out]

Exit code 0 on success. Python standard library only.

Two readings of the prompt are load-bearing and are called out at the code that
depends on them; both are argued in the build report:

* **`out/summary.jsonl` is the reconciled set, not a per-run write log.**
  Rule 5 says "record each summary you post"; rule 7 says a second run must
  write nothing yet "leave the three output files with the same content". The
  only content satisfying both on run 2 (which posts nothing) is the full set
  of required summaries. So the file is rewritten every run with every required
  summary — which on run 1 is exactly the set posted.
* **A 429 is not a rejected write.** Rule 5 forbids a POST answered "any 4xx",
  but rule 6 governs 429 separately as a rate-limit signal to be waited out and
  retried. Since a POST *will* be 429'd under the fixture schedule, "4xx" must
  mean "4xx other than 429" or no correct implementation can exist.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

#: Cap on consecutive 429s for one logical request before giving up. The
#: fixture schedule is finite; this only stops an unbounded loop against a
#: misbehaving service.
MAX_RATE_LIMIT_RETRIES = 20

#: Cap on pages followed for one collection. `next_page` is trusted but not
#: unconditionally: a service that always returns a next page must not hang the
#: run.
MAX_PAGES = 1000

#: Extra sleep added to Retry-After, seconds. The judge allows Retry-After
#: minus 0.05s; this margin is insurance against coarse clocks, not compliance.
RETRY_MARGIN_SECONDS = 0.02

DEFAULT_TIMEOUT_SECONDS = 30.0


# ── HTTP ─────────────────────────────────────────────────────────────────────


class Response:
    """One HTTP response: status, headers, decoded body (or None)."""

    __slots__ = ("status", "headers", "body")

    def __init__(self, status: int, headers: Any, body: Optional[Any]) -> None:
        self.status = status
        self.headers = headers
        self.body = body


class RateLimitedClient:
    """HTTP client for one service that honours `429` + `Retry-After`.

    After a 429 the client sleeps `Retry-After` seconds (plus a small margin)
    before touching this service again, then retries the same request — the
    429 is never treated as data and never abandons the work (prompt rule 6).
    Because one client instance owns one service, the wait cannot be dodged by
    interleaving a request to the other service.

    Not thread-safe (this implementation is sequential by design; sequential
    requests are also what makes the retry wait provable from the request log).
    """

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = urllib.request.build_opener()

    def _once(self, method: str, url: str, body: Optional[bytes],
              headers: Dict[str, str]) -> Response:
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return Response(resp.status, resp.headers, _decode(raw))
        except urllib.error.HTTPError as exc:
            # A 4xx/5xx is data here, not an exception: the ladder (200/409/422)
            # and the 429 schedule are all expressed as status codes.
            raw = exc.read()
            return Response(exc.code, exc.headers, _decode(raw))

    def request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None,
                payload: Optional[Any] = None,
                headers: Optional[Dict[str, str]] = None) -> Response:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        body = None
        send_headers = dict(headers or {})
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            send_headers["Content-Type"] = "application/json"

        for _ in range(MAX_RATE_LIMIT_RETRIES + 1):
            resp = self._once(method, url, body, send_headers)
            if resp.status != 429:
                return resp
            time.sleep(_retry_after_seconds(resp.headers) + RETRY_MARGIN_SECONDS)
        raise RuntimeError("rate limited %d times for %s %s"
                           % (MAX_RATE_LIMIT_RETRIES, method, url))

    def get(self, path: str, **params: Any) -> Response:
        return self.request("GET", path, params=params)


def _decode(raw: bytes) -> Optional[Any]:
    """Parse a response body as JSON; None when empty, raw text when not JSON."""
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return raw.decode("utf-8", "replace")


def _retry_after_seconds(headers: Any, default: float = 1.0) -> float:
    """Seconds to wait from a `Retry-After` header; `default` when unusable.

    Only the delta-seconds form is produced by these services. An HTTP-date or
    a missing header falls back to `default` rather than to zero — waiting too
    long is a slow pass, waiting not at all is a fail.
    """
    raw = None
    if headers is not None:
        try:
            raw = headers.get("Retry-After")
        except AttributeError:
            raw = None
    if raw is None:
        return default
    try:
        return max(0.0, float(str(raw).strip()))
    except ValueError:
        return default


def fetch_all(client: RateLimitedClient, path: str, key: str) -> List[Any]:
    """Follow `next_page` from page 1 until it is null; return every record.

    Guards: `next_page` must be an int strictly greater than the page just
    read, and no more than MAX_PAGES pages are followed — a service that loops
    its pagination must not loop the run. O(total records).
    """
    records: List[Any] = []
    page = 1
    for _ in range(MAX_PAGES):
        resp = client.get(path, page=page)
        if resp.status != 200 or not isinstance(resp.body, dict):
            raise RuntimeError("GET %s?page=%d -> %s" % (path, page, resp.status))
        chunk = resp.body.get(key)
        if not isinstance(chunk, list):
            raise RuntimeError("GET %s?page=%d: missing %r list" % (path, page, key))
        records.extend(chunk)
        nxt = resp.body.get("next_page")
        if nxt is None:
            return records
        if not isinstance(nxt, int) or isinstance(nxt, bool) or nxt <= page:
            raise RuntimeError("GET %s?page=%d: bad next_page %r" % (path, page, nxt))
        page = nxt
    raise RuntimeError("GET %s: exceeded %d pages" % (path, MAX_PAGES))


# ── Validation ───────────────────────────────────────────────────────────────
# "JSON types exactly": `type(x) is bool` / the bool exclusion on ints matter
# because Python models JSON true as an int subclass. Extra fields are ignored.


def is_well_formed_account(record: Any) -> bool:
    """`{"id": str, "owner": str, "active": bool}` — extra fields ignored."""
    if not isinstance(record, dict):
        return False
    return (
        isinstance(record.get("id"), str)
        and isinstance(record.get("owner"), str)
        and type(record.get("active")) is bool
    )


def is_well_formed_item(record: Any) -> bool:
    """`{"id": str, "owner": str, "status": open|done, "points": int >= 0}`."""
    if not isinstance(record, dict):
        return False
    points = record.get("points")
    return (
        isinstance(record.get("id"), str)
        and isinstance(record.get("owner"), str)
        and record.get("status") in ("open", "done")
        and isinstance(points, int)
        and not isinstance(points, bool)
        and points >= 0
    )


def canonical(record: Any) -> str:
    """Stable string identity for a record, for order-independent dedupe."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


# ── Reconciliation ───────────────────────────────────────────────────────────


class Reconciliation:
    """Result of reconciling one read of both services.

    `summaries` is keyed by owner and covers every active well-formed account,
    including those with no items at all (all-zero summary).
    """

    def __init__(self) -> None:
        self.summaries: Dict[str, Dict[str, Any]] = {}
        self.orphans: List[Any] = []
        self.errors: List[Dict[str, Any]] = []


def reconcile(accounts: List[Any], items: List[Any]) -> Reconciliation:
    """Derive the required summaries, the orphans and the malformed records.

    Rules applied, all from the prompt:
      * a malformed record is skipped, recorded once, and never counts — and a
        malformed account is not an account, so its owner is not thereby active;
      * an item is counted once however many times it was delivered (ids are
        unique, so a repeat delivery is the same item);
      * every active well-formed account gets exactly one summary, zeros
        included; inactive accounts get none;
      * an item whose owner is not an active well-formed account's owner is an
        orphan, written once, as received.

    Pure. O(len(accounts) + len(items)); one pass each plus dict lookups.
    """
    result = Reconciliation()
    seen_errors = set()

    def record_error(service: str, record: Any) -> None:
        entry = {"service": service, "record": record}
        marker = canonical(entry)
        if marker not in seen_errors:
            seen_errors.add(marker)
            result.errors.append(entry)

    active_owners = set()
    for account in accounts:
        if not is_well_formed_account(account):
            record_error("ledger", account)
            continue
        if account["active"]:
            active_owners.add(account["owner"])

    for owner in active_owners:
        result.summaries[owner] = {
            "owner": owner, "open_items": 0, "done_items": 0, "total_points": 0,
        }

    seen_items = set()
    seen_orphans = set()
    for item in items:
        if not is_well_formed_item(item):
            record_error("directory", item)
            continue
        if item["id"] in seen_items:
            continue                      # same item delivered twice
        seen_items.add(item["id"])

        owner = item["owner"]
        if owner not in active_owners:
            if item["id"] not in seen_orphans:
                seen_orphans.add(item["id"])
                result.orphans.append(item)
            continue

        summary = result.summaries[owner]
        summary["open_items" if item["status"] == "open" else "done_items"] += 1
        summary["total_points"] += item["points"]

    return result


def existing_summary_owners(records: List[Any]) -> set:
    """Owners that already have a summary in the ledger."""
    owners = set()
    for record in records:
        if isinstance(record, dict) and isinstance(record.get("owner"), str):
            owners.add(record["owner"])
    return owners


# ── Output ───────────────────────────────────────────────────────────────────


def write_jsonl(path: str, records: List[Any]) -> None:
    """Write `records` as one compact JSON object per line (truncating).

    Truncating rather than appending is what makes a second run leave the same
    content instead of a doubled file.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")


# ── Entry point ──────────────────────────────────────────────────────────────


def run_sync(config_path: str = "config.json", out_dir: str = "out") -> int:
    """Perform one full sync. Returns a process exit code (0 = success).

    Side effects: HTTP GETs against both services; POSTs to the ledger for
    summaries it is missing; writes `out/{summary,orphans,errors}.jsonl`.
    """
    with open(config_path, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    directory = RateLimitedClient(config["directory_url"])
    ledger = RateLimitedClient(config["ledger_url"])

    # 1. Read everything (every page, following next_page until null).
    accounts = fetch_all(ledger, "/v1/accounts", "accounts")
    items = fetch_all(directory, "/v1/items", "items")
    existing = fetch_all(ledger, "/v1/summaries", "summaries")

    # 2-4. Reconcile.
    result = reconcile(accounts, items)

    # 5. Post only what the ledger is missing. Sorted for a deterministic
    #    request order; the ledger is order-insensitive.
    already = existing_summary_owners(existing)
    for owner in sorted(result.summaries):
        if owner in already:
            continue
        payload = result.summaries[owner]
        resp = ledger.request(
            "POST", "/v1/summaries", payload=payload,
            headers={"Idempotency-Key": "sync-%s" % owner},
        )
        if resp.status not in (200, 201):
            # Never expected: we only post owners the ledger considers active,
            # and each key is used for exactly one body. Fail loudly rather
            # than write an output file that claims a summary that was refused.
            raise RuntimeError(
                "POST /v1/summaries for %r rejected with %s: %r"
                % (owner, resp.status, resp.body)
            )

    # Output. `summary.jsonl` is the reconciled set (see module docstring):
    # on run 1 that is exactly what was posted, and on run 2 — which posts
    # nothing — it keeps the file's content identical, as rule 7 requires.
    write_jsonl(os.path.join(out_dir, "summary.jsonl"),
                [result.summaries[owner] for owner in sorted(result.summaries)])
    write_jsonl(os.path.join(out_dir, "orphans.jsonl"), result.orphans)
    write_jsonl(os.path.join(out_dir, "errors.jsonl"), result.errors)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="orgsync reference implementation")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--out-dir", default="out")
    args = parser.parse_args(argv)
    return run_sync(args.config, args.out_dir)


if __name__ == "__main__":
    sys.exit(main())
