# The Task Ledger

Every task the TIER Model Report has ever scored, with its full lineage.

This file is **append-only**. Entries are never edited or removed — a correction
is a new dated entry, and a task leaving service gets a status event, not a
deletion. This is the receipt for "did you pick the tasks that make your point?"

Identity rules are in `PROTOCOL.md` §5.5. In short: the versioned unit is the
whole bundle (prompt + judge + reference + controls + metadata) hashed together;
identifiers are ordinals like `cronspec/v2`, never semver; a version's hashed
contents never change once it has a scored run; status is append-only and
separate from identity.

**Status values:** `active` (in service on the frontier) · `anchor` (frozen,
graduated, still priced each edition) · `graduated` (saturated, revealed) ·
`retired` (withdrawn below the band, revealed) · `sealed` (no longer scored,
bundle stays public).

---

## Families

### `duration` — EASY

Parse and format human-readable duration strings against a stated spec. Measures
nothing but cost: it is the designed floor, and everyone is supposed to pass it
(`PROTOCOL.md` §3.3 exempts EASY from the band). Its job is to price identical
accepted work across models and across time.

| version | rung | status | prompt sha256 | judge sha256 | editions |
|---|---|---|---|---|---|
| `duration/v1` | floor | anchor | `6927e499c8ee…` | `f07718acdcc2…` | 0 (pilot, N=1) · 1 |

### `cronspec` — MEDIUM

Implement a cron dialect that diverges from standard cron in precisely specified
ways. Difficulty by *stated deviation* from a familiar system, so recall from
memory fails and careful reading succeeds.

| version | rung | status | prompt sha256 | judge sha256 | editions |
|---|---|---|---|---|---|
| `cronspec/v1` | 1 | graduated | *(see archive)* | *(revealed)* | 0 (pilot, N=1) |
| `cronspec/v2` | 2 | active → see Edition 1 | *(in stamp)* | *(in stamp)* | 1 |

### `orgsync` — anchor, GRADUATED 2026-07-23 (revealed)

Complete a partially built CLI inside an existing codebase, reconciling two
local HTTP services that disagree. Work-class: service integration + brownfield.
Difficulty comes from **deriving** the consequences of generally-stated
invariants across a conflict matrix the model never sees enumerated, under
operational load (pagination, rate limits, idempotent writes, error paths).

**What it measures:** requirements reconciliation under conflict.
**What it does not measure, and will never be claimed to:** product sense. A
binary judge can check judgment's consequences; it cannot certify judgment.

| version | rung | status | prompt sha256 | judge sha256 | editions |
|---|---|---|---|---|---|
| `orgsync/v1` | L2 | **graduated — anchor (revealed)** | `94950b00fd82cc86…` | `b948f904bfd72ffc…` | 1 |

**The prompt hash above is published before the task has ever been run.** That
is the commitment: if the task later produces failures, anyone can check that
the task we ran is the task we committed to, authored before we knew who would
fail it. Content reveals at graduation or retirement (`PROTOCOL.md` §5.4).

Supporting artifacts — **revealed at graduation 2026-07-23**. The hashes below
were published before the task ever ran; each was re-verified against its
published value at graduation and all five matched.

| artifact | sha256 |
|---|---|
| `design/orgsync/FIXTURES.md` (service contract + seeded dataset) | `c03595a92ec80b36…` |
| `design/orgsync/JUDGE_SPEC.md` (assertions ↦ the prompt sentence stating each) | `a03c23f610cb1875…` |
| `design/orgsync/CONTROLS.md` (reference + three wrong implementations) | `d4206322e35c11b8…` |

---

## Events

Chronological. `occurred` is when the thing happened; where an entry was written
up later, `recorded` says so — the gap is deliberate and visible.

**2026-07-23 · `orgsync/v1` → GRADUATED to the Anchor Set.**

Verdict recomputed from the published per-run results at graduation, so a reader
can check we applied our own rule: **6 of the 7 roster models passed every one of
their three runs**; only `claude-haiku-4-5` did not (0/3). PROTOCOL §3.3 —
*"all but at most one roster model passes every one of its runs"* — is therefore
met. **SATURATED.** Cost spread across the six scored models: 36.16×.

**Pre-commitment re-verified before revealing anything.** All five sealed
artifacts hash to the values published in this file before the task was ever
run:

| artifact | published sha256 | at graduation |
|---|---|---|
| `prompts/orgsync.md` | `94950b00fd82cc86…` | ✓ match |
| `acceptance_orgsync.py` (judge) | `b948f904bfd72ffc…` | ✓ match |
| `design/orgsync/FIXTURES.md` | `c03595a92ec80b36…` | ✓ match |
| `design/orgsync/JUDGE_SPEC.md` | `a03c23f610cb1875…` | ✓ match |
| `design/orgsync/CONTROLS.md` | `d4206322e35c11b8…` | ✓ match |

**Revealed:** the judge source and all three design artifacts. **Status
correction recorded honestly:** this row previously read *"sealed — not yet in
service"*, which was stale — the task served in Edition 1 with 21 runs. The
status is corrected here rather than silently.

**Consequences.** `orgsync` joins the Anchor Set alongside `duration/v1` (2 of a
maximum 3), is **never edited** from this point — any change mints a new version
(§5.5) — and runs at **N=1 on rotation**, escalating to N=3 on any failure. It
remains the regression tripwire it earned by being the only task in Edition 1 to
produce a failure. A replacement frontier task is now owed (§5.1).

**2026-07-22 · `cronspec/v1` measured — 7/7 PASS (N=1 pilot).**
All seven panel models passed with zero edge-case failures. Cost spread 34×.
Verdict: **SATURATED** — the task discriminated on cost and effort, not on
reliability.

**2026-07-22 · `cronspec/v1` → graduated.**
Escalated to `cronspec/v2` by hand-authored hardening (wrap-around ranges,
step-over-wrap, `L` last-day, leap/month-length rollover). Recorded honestly:
this was a *reactive* hardening after observing 7/7, not a step along a
pre-registered ladder. The escrow and pre-flight-probe rules
(`PROTOCOL.md` §5.6) exist because of this event.
Archive: `archive/cronspec-v1-easy/` — prompt, judge, all seven results,
`FINDING.md`.

**2026-07-22 · `cronspec/v2` three-way control verified.**
Reference implementation PASS · a standard-cron implementation FAIL (101
failures) · a careful-but-naive implementation FAIL (20 failures), with failures
landing on exactly the deviations under test. The judge discriminates.

**2026-07-22 · `cronspec/v2` measured — 7/7 PASS (N=1 pilot).**
Zero edge failures. Cost spread 20.4× ($0.1004 → $2.0490). Verdict:
**SATURATED** — unanimous, so the trigger cannot be attributed to noise.
The hardened task saturated exactly as its predecessor did.

**2026-07-22 · Standing finding: the report has never measured reliability.**
Across every run ever made — `duration` 7/7, `cronspec/v1` 7/7, `cronspec/v2`
7/7 — **21 of 21 runs passed the independent judge. Zero failures.** The judges
are proven to discriminate (see the control records above); the models simply do
not fail this class of work. Two difficulty generations produced the same
result, so the finding is replicated, not incidental: **within single-file
spec-implementation, capability across frontier models has converged, and cost
has not — ~20× spread on identical accepted work, at both difficulty levels.**
The conclusion is that the discriminating axis is task *class*, not task
*difficulty*: escalation continues by changing the kind of work (integration,
discovery, tool use, error handling under partial information), not by adding
rules to a spec.

**2026-07-22 · Pilot runs demoted.**
All pre-Edition-1 runs are archived at `archive/pilot-2026-07-22/` and are
**pilot data, not a conformant edition**: they were produced by a harness that
could not run N=3, did not stamp the judge hash, did not archive the spend
evidence, and silently dropped runs whose agent exited non-zero. Retained in
full for the record; never cited as an edition.

**2026-07-22 · `orgsync/v1` prompt corrected BEFORE first service — hash superseded.**
Building the reference implementation exposed a contradiction in the prompt.
Rule 5 said a passing run never sends a POST the ledger answers with "any
`4xx`"; rule 6 says either service may answer any request with `429`, to be
waited out and retried. `429` is a `4xx`, and the fixture's rate-limit schedule
guarantees one lands on a write — every correct implementation makes the same
five ledger reads before its first POST, so the writes always occupy the
ordinals where the limit fires. A deliberately wasteful variant that re-reads
before every write was tested and lands on a write too. So the rule as worded
was unsatisfiable, and every model would have failed for a reason that says
nothing about the model.
Rule 5 now names rejection explicitly (`400`, `409`, `422`) and states that a
`429` is not a rejection. The 429-on-a-write is retained deliberately: it
catches an implementation that retries a write with a fresh idempotency key and
thereby double-posts.
Prompt sha256 `d149fc2c4f336eaa…` is **superseded** by `94950b00fd82cc86…`.
Permitted without a version bump because the task has **never had a scored
run** (`PROTOCOL.md` §5.5 freezes a version at its first scored run). Recorded
here rather than silently re-hashed — the superseded hash stays on the record.
