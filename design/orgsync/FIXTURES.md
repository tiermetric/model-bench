# orgsync — fixture contract (SEALED until graduation; never enters the run repo)

This file is the build contract for the two fixture services, the seeded
dataset, the repo skeleton, and the harness integration. The authoring agents
build to it; the judge reads it. It is SEALED: it enumerates the derived
conflict instances the prompt deliberately does not, so it must never be
visible to a model under test and never exported publicly before graduation.

## 1. Implementation constraints

- Python **stdlib only** (`http.server` / `socketserver`), single file per
  service or one module serving both on two ports. Deterministic: all state
  from the seeded dataset below; no randomness, no wall-clock dependence
  except `Retry-After` timing.
- Both services keep **in-process state** (posted summaries) for the lifetime
  of the process. The judge's double-invocation test depends on this: the
  fixture process persists across the two `sync` runs.
- Every request is appended as one JSON line to a **request log**:
  `fixture-logs/<service>.jsonl` under the run dir, fields:
  `{"n": <1-based per-service request count>, "ts": <epoch float>,
  "method": ..., "path": ..., "query": ..., "idempotency_key": <or null>,
  "body": <or null>, "status": <int returned>}`. The log is judge evidence
  and ships in the run bundle.

## 2. Transport and harness integration

- Preferred transport: HTTP on `127.0.0.1`, two OS-assigned free ports.
  **Gated by the A1 spike** (does codex `workspace-write` sandbox permit
  loopback under our exact flags?). Fallbacks, in order: scoped codex network
  config for loopback only → fixture started by the model from inside the
  workspace (`python3` module shipped in-repo — requires moving fixture code
  into the repo skeleton and re-checking seal implications) → file-based
  transport (last resort; changes the prompt, i.e. a new version).
- `run.sh` gains a per-task setup hook: for `orgsync` it (a) copies the repo
  skeleton (§5) into the fresh run dir before `git init`, (b) starts the
  fixture process, (c) writes `config.json` with the live URLs, (d) tears the
  fixture down after the run, archiving `fixture-logs/`. The judge starts its
  **own fresh fixture instance** and rewrites `config.json` — the model's
  dev-time state is never judged.

## 3. Service behaviors

- Pagination: `?page=N`, 1-based. Page sizes: accounts **3**, items **4**,
  summaries **10**. `next_page` = N+1 or `null`.
- Unknown paths → 404. Malformed POST JSON → 400 (never seeded; defensive).
- POST `/v1/summaries` validation order: (1) owner not an active well-formed
  account → **422** with `{"error": "owner_not_active"}`; (2) known
  `Idempotency-Key` with identical body → **200** (replay; NOT recorded as a
  new summary; not a "rejected write"); (3) known key, different body →
  **409**; (4) else **201**, summary stored (appears in subsequent
  `GET /v1/summaries`).
- **429 schedule (per service, deterministic):** request numbers **2 and 8**
  (1-based, counting every request received by that service, including ones
  answered 429) are answered `429` with `Retry-After: 1` and no body. The
  count runs for the process lifetime, so the schedule is deterministic across
  the judge's double invocation as well.

## 4. Seeded dataset (L2) and expected outcomes

### Ledger accounts (3 pages: 3+3+1)

| # | record | note |
|---|---|---|
| 1 | `{"id":"acct-01","owner":"amara","active":true}` | normal |
| 2 | `{"id":"acct-02","owner":"bo","active":true}` | normal |
| 3 | `{"id":"acct-03","owner":"chen","active":false}` | inactive WITH items → orphans |
| 4 | `{"id":"acct-04","owner":"dee","active":true}` | active, ZERO items → zero summary |
| 5 | `{"id":"acct-05","owner":"eli","active":true}` | normal |
| 6 | `{"id":"acct-06","owner":"fen","active":"yes"}` | **MALFORMED** (string, not bool) → errors; fen's items → orphans |
| 7 | `{"id":"acct-07","owner":"gus","active":false}` | inactive, zero items → nothing anywhere |

### Directory items (3 pages: 4+4+4)

| # | record | note |
|---|---|---|
| 1 | `{"id":"item-101","owner":"amara","status":"open","points":3}` | |
| 2 | `{"id":"item-102","owner":"amara","status":"done","points":2}` | |
| 3 | `{"id":"item-103","owner":"bo","status":"open","points":5}` | duplicated on page 3 |
| 4 | `{"id":"item-104","owner":"chen","status":"open","points":8}` | orphan (inactive owner) |
| 5 | `{"id":"item-105","owner":"eli","status":"done","points":1}` | |
| 6 | `{"id":"item-106","owner":"zara","status":"open","points":4}` | orphan (owner unknown to ledger) |
| 7 | `{"id":"item-107","owner":"fen","status":"open","points":2}` | orphan (owner's account malformed) |
| 8 | `{"id":"item-108","owner":"bo","status":"done","points":1}` | |
| 9 | `{"id":"item-103","owner":"bo","status":"open","points":5}` | **duplicate delivery** of item-103, byte-identical |
| 10 | `{"id":"item-109","owner":"amara","status":"open"}` | **MALFORMED** (missing `points`) → errors |
| 11 | `{"id":"item-110","owner":"amara","status":"open","points":7}` | |
| 12 | `{"id":"item-111","owner":"eli","status":"open","points":2}` | |

### `GET /v1/summaries`: initially empty.

### Expected outcomes (the reference answer)

Summaries POSTed (exactly these four, each answered 201 on first run):

| owner | open_items | done_items | total_points |
|---|---|---|---|
| amara | 2 | 1 | 12 |
| bo | 1 | 1 | 6 |
| dee | 0 | 0 | 0 |
| eli | 1 | 1 | 3 |

`out/orphans.jsonl` = exactly {item-104, item-106, item-107} (as received).
`out/errors.jsonl` = exactly {ledger acct-06, directory item-109}.
`out/summary.jsonl` = exactly the four POST bodies.
Second invocation: **zero** POST requests; all three files byte-equivalent as
sets; every list endpoint may be re-read.

## 5. Repo skeleton (built by authoring agents; ~400–600 LOC)

- `orgsync/__init__.py`, `orgsync/__main__.py` (arg dispatch; `sync`
  subcommand raises `NotImplementedError`), `orgsync/http_client.py` (working
  stdlib GET/POST helper WITHOUT retry/pagination logic — the model must add
  judgment, not plumbing), `orgsync/models.py` (dataclasses, no validation).
- `tests/existing/` — unit tests for `http_client` and `models` and the CLI
  dispatch, all green pre-task. These are the brownfield invariant; judge
  reruns them.
- `config.json` — harness-owned. `README.md` — one paragraph, no task info.
- The repo skeleton contains NO fixture code and NO reference to this file.

## 6. Difficulty levers

Same-version levers (dataset/service-behavior only; the prompt already states
all governing rules): **L0** = drop acct-06, item-109, the item-103 duplicate,
item-106, dee; no 429s (single conflict class: inactive-owner orphan). **L1**
= L0 + 429 schedule + duplicate delivery. **L2** = full matrix above
(**calibration starts here**). Next-version levers (require new stated rules →
new task version, pre-designed only): **L3** = ledger snapshot-cutoff /
stale-summary precedence; **L4** = mid-POST connection drop with
no-double-write recovery.

## 7. Determinism note for the judge

All expected artifacts are computable from this file alone. The judge compares
JSONL files as **sets of parsed objects** (order-free, duplicates fatal), and
reads completeness/rate-limit/write behavior from `fixture-logs/*.jsonl` of
its OWN fixture instance. Timing tolerance for Retry-After: the next request
to a 429'd service must be ≥ `Retry-After − 0.05s` after the 429 was served.
