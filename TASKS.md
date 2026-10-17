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
| `duration/v1` | floor | active | `6927e499c8ee…` | `f07718acdcc2…` | 0 (pilot, N=1) · 1 |

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


**2026-07-29 · `duration/v1` status CORRECTED — `anchor` → `active`. It does not
graduate.**

This row read **`anchor`**, and the 2026-07-23 graduation event above states that
`orgsync` "joins the Anchor Set alongside `duration/v1` (2 of a maximum 3)". Both
were wrong, and they contradicted `PROTOCOL.md` §3.3 in public:

> **The EASY tier is exempt from the band.** It exists as a designed cost floor —
> everyone is *supposed* to pass it — so it is never declared saturated and never
> graduates on that basis.

`duration/v1` is the EASY floor. It ran 21/21 PASS in Edition 1, which meets the
SATURATED arithmetic — but the EASY tier is explicitly exempt from that
arithmetic, so the trigger never applied to it. The error was applying a rule to
the one tier the protocol carves out of it.

**The earlier event is NOT edited.** It stays on the record as written, and this
entry supersedes its Anchor-Set count. That is the ledger's rule: a correction is
a new dated entry, never a rewrite.

**Why the exemption is right, and not a technicality.** An anchor and an EASY
floor are different instruments doing a superficially similar job. An anchor is a
frozen graduate: it runs **N=1 on rotation** (§5.1) and is prunable when its
spread collapses. The EASY floor runs at **full N every edition** and is never
rotated. Graduating `duration` would therefore make it *worse* at its own stated
job — §4.1 notes that at N=1 cost dispersion cannot be computed at all, and a
cost figure is `duration`'s entire output. It would also leave the EASY tier with
no task, and fill the 3-slot Anchor Set after a single edition.

**Anchor Set after this correction: {`orgsync/v1`} — 1 of a maximum 3.**

`cronspec/v2`'s graduation is a separate open question: the unpublished Edition 1
draft declares it graduated, but no graduation event was ever recorded here and
its judge reveal (§5.4) is therefore still owed. It is NOT counted in the Anchor
Set above until that is resolved.

Ruled by the operator, 2026-07-29.


**2026-07-29 · Edition 2 escrow — PARTIAL registration. §5.6 is NOT yet satisfied,
and this entry exists so that is on the record rather than assumed.**

Edition 1 **consumed its own escrow.** §5.6 requires each edition to publish the
hash of the *next* edition's frontier task and judge before the current edition's
results exist. The hash Edition 1 would have pointed at is `orgsync/v1` — which
turned out to be **Edition 1's own task**, and is now graduated and revealed. A
revealed task can never serve as a future frontier task: its answer key is public.

**Pre-committed now**, so these artifacts are fixed in the ledger from this date
and cannot be silently rewritten later. Content stays sealed (`.export-exclude`);
only hashes are published, per §5.4:

| task | artifact | sha256 |
|---|---|---|
| `cutover` | `prompts/cutover.md` (prompt) | `0f9952c539243512…` |
| `cutover` | `design/cutover/JUDGE_SPEC.md` (judge spec) | `a64d8770fa214ad6…` |
| `cutover` | `design/cutover/FIXTURES.md` (fixtures) | `6821027e9423d1bb…` |
| `cutover` | `design/cutover/CONTROLS.md` (controls) | `acbdd7a085246315…` |
| `foreman` | `prompts/foreman.md` (prompt) | `4d1ac3727328bb55…` |
| `foreman` | `design/foreman/FIXTURES.md` (fixtures) | `dec4cf53c0a06863…` |
| `foreman` | `design/foreman/CONTROLS.md` (controls) | `86b82f48ea1361b7…` |

**What this does NOT do, stated plainly:**

1. **It does not name Edition 2's frontier task.** `PROTOCOL.md` §3.1 line 104
   names **`walstore`** as Edition 2's HARD task. **`walstore` does not exist** —
   zero files anywhere in this repository. So §3.1 currently names a task that has
   never been authored, and the two artifacts above are candidates, not a decision.
   Choosing between them (or authoring `walstore`) is an open ruling. Note §5.6's
   own replacement clause: if `walstore` is dropped, it must be "revealed in full,
   with the reason" — which is unsatisfiable for a task with no content, so §3.1's
   naming is itself the defect to correct.
2. **It does not register a judge.** §5.6 requires the task **and judge** hash.
   Neither candidate has one: there is no `acceptance_cutover.py` and no
   `acceptance_foreman.py`. `cutover` has a `JUDGE_SPEC.md` (hashed above) but a
   spec is not a judge. Until a judge exists and is hashed, the escrow is
   incomplete by the rule's own terms.
3. **It is not anchored to an unforgeable timestamp.** §5.6 requires the witness to
   be "something we alone control" — it must not be, and the hash must be "anchored
   to a timestamp proof we cannot forge". No such anchor exists in this repository
   for any task, including `orgsync`. So the strongest claim available today is
   "trust our commit history", which §5.6 explicitly calls the weakest available
   claim. That mechanism appears never to have been implemented.

**Consequence for publication.** Until (1)-(3) are resolved, no edition may claim
the §5.6 pre-commitment property. Edition 1's authored report states this in its
own "What happens next" section rather than asserting a guarantee it does not have,
and it is deliberately unpublished for that reason. Tracked as model-bench#5.


**2026-10-13 · `cronspec/v2` → GRADUATED to the Anchor Set (recorded now).**

Edition 1's archived results show **21/21 PASS**: all seven roster models passed
all three replicates. **SATURATED** under PROTOCOL §3.3 and §5.2. This entry
records graduation now, not a backdated reveal or graduation in July; it
supersedes the open graduation question in the 2026-07-29 correction above.

The judge, `acceptance_cronspec.py`, was **already public on
tiermetric/model-bench without a recorded reveal event**. We cannot restore
its former secrecy or claim a timely §5.4 reveal. Its SHA-256 is
`a847b421cf8234cd953aaefe264ed72745ef123f4c43abaa24decca746e2e5a7`, matching
all 21 Edition 1 stamps and the revealed file. The prompt SHA-256 is
`d6b188dca51a2842e99f35b373c414372dc2d3f75fea60d20e1863a4b4a32274`.
The version's contents remain frozen (§5.5); graduation changes its status.

**Anchor Set after this event: {`orgsync/v1`, `cronspec/v2`} — 2 of a maximum 3.**
`duration/v1` remains the active EASY floor, outside the Anchor Set. Graduated
tasks run on the anchor rotation with escalation on failure (§5.1).

**2026-10-13 · `duration/v1` public-judge disclosure.**

`acceptance_test.py` is public on tiermetric/model-bench while `duration/v1`
remains an **active EASY-floor task**. This is a §5.4 disclosure, not a
retirement or graduation: the EASY exemption still applies. Judge SHA-256
`f07718acdcc279b349e7393144a79cbf7f1e62a88f089c10288cfe05e84e5b53` matches
all 21 Edition 1 stamps and the public file. The answer key's publication
cannot be undone; the cost-floor results carry that contamination limitation.

**2026-10-13 · Edition 1 publication gate reversed — R-2026-10-13-3,
panel consensus B.**

The operator authorizes publication of Edition 1 (2026-07, 63 runs, seven
models, three tasks) after the mechanical re-judgment and public-submission
export fixes, with the unfixable limitations disclosed in its report. This
supersedes the earlier **"deliberately unpublished"** gate; it does not assert
§5.6 compliance. Edition 1 consumed its own escrow, and Edition 2 has since
run, so the missing pre-result escrow cannot be satisfied retroactively.
No unforgeable timestamp anchor exists for any task; only commit history does.
The `orgsync` rep1 judge-hash publication gap (#4) and duration's public active
judge remain disclosed. No recorded verdict, cost, or other result is changed.

Implementation limitation recorded now: the 21 `orgsync` bundles archive only
`test_orgsync.py` inside `submission/`; the runnable package and brownfield
files are adjacent in each bundle. Re-judgment of those targets awaits an
operator ruling on assembling a temporary copy from those adjacent archives.
The publication authorization above does not turn that pending check into a PASS.


**2026-10-13 · Edition 1 orgsync target ruling and mechanical fixes completed.**

The operator rules that re-judgment reconstructs **exactly the original target
layout**, using `submission/` plus `orgsync/`, `config.json` and
`tests/existing/` from the **same archived bundle**. This supersedes the pending
ruling noted above. No file may be supplied from the live skeleton or another
bundle; a required archived path that is missing SKIPs loudly, naming the path.

The historical harness (`da2561f:run.sh`, lines 176–205, 321 and 390–391)
laid down the package and existing tests, wrote the config, judged the run
directory, then archived only its root Python files in `submission/`.
`da2561f:score.py`, line 337, recorded the verdict; it did not assemble a target.
The replay follows that layout on a temporary copy, never in the archive.

Actual validation across all 63 bundles: RE-JUDGE 63 bundles · PASS 63 · FAIL 0 · SKIP 0.
The revealed judges reproduce the recorded 60 accepted / 3 not accepted,
including all three `claude-haiku-4-5`/`orgsync` failures. The public kit carries
all 63 submission directories and the adjacent orgsync archives, with sealed
paths excluded. The report's verification stamp is derived from this executed
verifier output. These mechanical fixes satisfy the publication condition in
R-2026-10-13-3; the escrow, timestamp-anchor, rep1 ordering and public-active-
judge limitations remain disclosed, and no result figure changes.
