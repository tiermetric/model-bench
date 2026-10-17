# The TIER Model Report — `orgsync` task (frontier / MEDIUM tier)

**Purpose:** frontier discriminating task for Edition 2. Work-class: SERVICE-INTEGRATION + BROWNFIELD (complete a partially built tool inside an existing codebase, against two local fixture services). Difficulty axes: multi-service reconciliation under stated general invariants whose concrete consequences must be DERIVED (inactive-owner orphans, malformed-account fallout, duplicate delivery, zero-item accounts), plus operational load (pagination, rate limits, idempotent writes, error paths). NOT tested and not claimed: "product sense" — this measures requirements reconciliation under conflict, and the published report must say exactly that.

**Difficulty lever: L2** (full conflict matrix; 429s on; duplicate delivery on). L0/L1 are dataset/service-behavior reductions of this same prompt (see `design/orgsync/FIXTURES.md`); L3/L4 add stated rules and are therefore NEXT-VERSION levers — this prompt is immutable after its first scored run.

**Status: SEALED.** SHA-256 goes in the public `TASKS.md` ledger at first service; content reveals at graduation/retirement. Calibration (haiku+luna lanes, N=3/rung) gates any panel. The judge checks ONLY behaviors stated below the separator; the seeded instances live in `design/orgsync/FIXTURES.md` (sealed) and are deliberately NOT enumerated in the task text — deriving them is the work. Everything above the first `---` is stripped by `run.sh` and never seen by the model.

---

You are working in an existing Python project — a partially built command-line
tool called `orgsync`. Your job is to complete it. Use only the Python standard
library — no third-party packages. This task is OFFLINE: do not use any web
search or web fetch tool. The only network you may touch is the two local
services described below.

## The services

`config.json` in the repo root (do not modify it) contains the base URLs of two
local HTTP services:

```json
{"directory_url": "http://127.0.0.1:<port>", "ledger_url": "http://127.0.0.1:<port>"}
```

**Directory** — authoritative for work items and their content:

- `GET <directory_url>/v1/items?page=N` (N starts at 1) returns
  `{"items": [...], "next_page": M}` where `next_page` is an integer or `null`
  on the last page.
- A **well-formed item** is `{"id": str, "owner": str, "status": "open" or
  "done", "points": int >= 0}` — JSON types exactly (an int is a JSON integer,
  not a string). Extra fields never make a record malformed; ignore them.
- Item ids are unique: if the same item id is delivered more than once, it is
  the same item delivered twice.

**Ledger** — authoritative for accounts: which owners exist and whether they
are active:

- `GET <ledger_url>/v1/accounts?page=N` returns
  `{"accounts": [...], "next_page": ...}`, same pagination scheme.
- A **well-formed account** is `{"id": str, "owner": str, "active": true or
  false}` (a JSON boolean). Extra fields are ignored. Among well-formed
  accounts, `owner` is unique.
- `GET <ledger_url>/v1/summaries?page=N` returns
  `{"summaries": [...], "next_page": ...}` — the summaries already posted.
- `POST <ledger_url>/v1/summaries` with header `Idempotency-Key` and JSON body
  `{"owner": str, "open_items": int, "done_items": int, "total_points": int}`.
  Responses: `201` accepted · `200` replay (same key and same body as an
  earlier accepted post) · `409` same key with a different body · `422` invalid
  (the ledger rejects any summary whose `owner` is not the owner of an active,
  well-formed account).

Either service may answer **any** request with `429` and a
`Retry-After: <seconds>` header.

## What `sync` must do

`python3 -m orgsync sync` currently raises `NotImplementedError`. Implement it
so that one invocation does all of the following:

1. **Read everything.** Retrieve every page of accounts, items, and existing
   summaries by following `next_page` until it is `null`.
2. **The ledger decides who counts.** For every active, well-formed account
   there is exactly one summary: `open_items` = the number of that owner's
   well-formed items with status `open` (each item counted once, however many
   times it was delivered), `done_items` = the same for `done`, and
   `total_points` = the sum of `points` over those items. An active account
   with no items gets a summary with all three values `0`. Inactive accounts
   get no summary.
3. **Items without an active owner are orphans.** A work item whose `owner` is
   not the `owner` of an active, well-formed account belongs in no summary;
   write each such item, exactly once, as one JSON line (the item as received)
   in `out/orphans.jsonl`.
4. **Malformed records never stop the run and never enter any count.** A
   record that is not well-formed (missing field, wrong JSON type, or
   out-of-range value) is skipped and written, exactly once, as one JSON line
   `{"service": "directory" or "ledger", "record": <the record as received>}`
   in `out/errors.jsonl`. A malformed account is not an account — its owner is
   not thereby active.
5. **Post what the ledger is missing, and only that.** For every required
   summary whose owner has no summary in the ledger yet, POST it with header
   `Idempotency-Key: sync-<owner>`. A passing run makes **no rejected write**:
   never send a POST that the ledger *rejects* — that is, one it answers `400`,
   `409` or `422`. A `429` is not a rejection; it is the rate limit of rule 6,
   and the same POST is retried after waiting. Record each summary you post as
   one JSON line (exactly the POST body) in `out/summary.jsonl`.
6. **Honor rate limits.** After a `429` from a service, send that service no
   further request until at least `Retry-After` seconds have elapsed, then
   retry. A `429` never counts as data and never abandons the work.
7. **Sync is idempotent.** Running `sync` again with the services unchanged
   must issue no write requests and must leave the three output files with the
   same content.

Create `out/` if it does not exist. Within each output file, order does not
matter and each entry appears exactly once. Exit code `0` on success.

Worked example of the arithmetic only: an active account with owner `kim`
whose well-formed items are `(open, 3)`, `(open, 4)`, `(done, 2)` gets
`{"owner": "kim", "open_items": 2, "done_items": 1, "total_points": 9}`.

## Ground rules

- Modify or add whatever you need under `orgsync/`, but files under
  `tests/existing/` must not be edited, and
  `python3 -m unittest discover tests/existing` must still pass.
- Do not modify `config.json`.
- Also write `test_orgsync.py` in the repo root: a standard-library `unittest`
  suite covering the behaviors above (it may talk to the two services).
  `python3 -m unittest test_orgsync` must pass with zero failures.
- Acceptance: your tool is judged in a fresh environment by starting fresh
  services, running `python3 -m orgsync sync` **twice**, and checking the
  behaviors stated above — the output files, the requests your tool made (both
  services log every request), and the `tests/existing` suite.
