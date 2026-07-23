# The TIER Model Report — Measurement Protocol

**Version 1.1 · published 2026-07-22, amended 2026-07-23 · applies from Edition 1 onward.**

This document is the *standard*. It is published **before** the results it
governs, and it is versioned independently of them. If a later edition changes
anything below, the change is recorded in §9 with the edition it took effect in
— never applied retroactively and never applied silently.

Edition 0 (2026-07-22, `results.md`) predates this document and was explicitly
labelled exploratory (N=1, one task). It is retained for the record but is
**not** a protocol-conformant edition.

---

## 1. What this measures — and what it does not

The TIER Model Report measures **cost per passing outcome**: the money a model
spends to produce work that an independent judge accepts.

It is **not** a capability leaderboard, not a "best model" ranking, and not a
speed benchmark. A model that fails a task has no cost-efficiency number for
that task — cost without a passing outcome is not efficiency, it is just spend.

The report is published by the maintainers of TIER, an open-source measurement
tool. Two commitments make that publishable rather than self-serving:

1. **Capture parity is proven before publication.** Every vendor in an edition
   is captured by a mechanism whose containment invariants have been verified
   (§4.3). A vendor whose spend cannot be captured honestly is *excluded and
   named as excluded* — never estimated. The inclusion bar is published, and a
   capture parser for an excluded vendor is accepted by pull request, so
   inclusion is a test anyone can make pass rather than an editorial favour.
2. **Results publish unedited, including when they are commercially
   inconvenient to us.** Edition 0's headline was that Codex swept the cheap end
   and Claude — the publisher's own stack — did not win. That demonstrates the
   *publishing behavior*; it is not offered as evidence about the measurement,
   since Edition 0 is explicitly non-conformant.
3. **Every scheduled edition publishes, including the unwelcome ones.** The
   fastest way to destroy a report like this is to skip the edition whose result
   we dislike, so a skipped trigger is published as a dated skip notice rather
   than as silence.

**No overall winner is ever crowned, and no composite score is ever published.**
Results are reported as a per-tier efficiency frontier: the set of models not
dominated on both cost and reliability. Ranking across difficulty tiers is
unsupported by construction. Any single number blending cost with reliability —
or blending tiers — is prohibited outright, because a composite is where hidden
editorial weighting lives: it is an overall winner wearing a lab coat.

What the reader gets instead is a decision: *for work that looks like this,
these are the efficient choices, at this expected cost per accepted outcome.*
Rankings **within** a tier on a **stated** axis are legitimate; any number that
collapses axes or tiers is not.

---

## 2. The unit of measurement

One **cell** = one (model × task) pair. One **run** = one execution of a cell in
a fresh, empty git repository.

A run produces exactly four measured facts:

| fact | how it is obtained |
|---|---|
| **outcome** | independent hidden judge: PASS / FAIL (binary) |
| **self-reported outcome** | the model's *own* test suite: PASS / FAIL (diagnostic only, never scored) |
| **spend** | the tool's own session log, priced at published list rates |
| **wall-clock** | harness-measured, reported, never scored |
| **cost dispersion** | within-cell variation across replicates, reported, never scored |

Only *outcome* and *spend* are scored. Wall-clock is reported because it is
cheap to capture and readers ask for it; it is not comparable across
interactive and headless runs and is never used in a ranking.

**The full outcome enum.** A run resolves to exactly one of:

| outcome | meaning |
|---|---|
| `PASS` | the judge accepted the work and capture was clean |
| `FAIL` | the judge rejected the work |
| `TIMEOUT` | the agent exceeded the tier's declared wall-clock cap |
| `CAPTURE-FAIL` | no attributable session log, so spend is unknown — no cost figure |
| `VOID` | a scored run touched a web tool (§4.4) |

**Every edition publishes the count of each.** A run that produced no row is
indistinguishable from a run that never happened, so an unpublished `VOID` or
`CAPTURE-FAIL` count is an invisible cherry-picking channel. `TIMEOUT` and
`CAPTURE-FAIL` are never silently folded into `FAIL`.

---

## 3. Task design

### 3.1 Difficulty tiers

Every scored task belongs to exactly one tier, fixed at authoring time:

| tier | intent | current task |
|---|---|---|
| EASY | floor — everyone should pass; isolates pure cost | `duration` |
| MEDIUM | the discriminating band | `cronspec` |
| HARD | frontier reliability | `walstore` (Edition 2) |
| RESEARCH | long-horizon comprehension over an authored spec | `spec-dig` (Edition 3) |

A separate **companion** class exists for tasks that cannot be scored fairly —
currently `web-dig` (live web). Companion results are published **unscored**,
visually separated, and carry their own caveat. Live-web work was rejected as a
scored tier because tool web access differs by vendor, results are not
reproducible across days, and grading is not binary.

### 3.2 Authoring rules

- **Self-contained and offline.** The prompt states everything needed. Scored
  tasks forbid web tools; the harness *voids* any scored run whose log contains
  a web tool call (§4.4). **"Offline" means no traffic leaves the machine.** A
  task may ship a fixture service the harness starts on loopback — a canned API
  with scripted pagination, errors and malformed payloads is legitimate task
  environment, and model-authored code calling `127.0.0.1` is the work, not a
  violation. The fixture ships inside the task's archive, so the task stays
  re-verifiable in years to come. What is forbidden is a *live external
  dependency*: a task whose fixture is someone else's running service can never
  be re-verified, only remembered, and its behavior differs by vendor sandbox
  and by day.
- **Unambiguous.** Every rule the judge checks is stated in the prompt. If the
  judge tests behavior the prompt does not specify, the judge is wrong, not the
  model.
- **Deviation-based, not obscurity-based.** Difficulty comes from precisely
  specified divergence from a familiar system (e.g. a cron dialect with
  seven stated deviations), so that recall-from-memory fails and reading
  succeeds. Difficulty never comes from trick wording or hidden requirements.
- **Fixed artifact names.** The prompt dictates the implementation and test
  filenames the harness expects, so the judge needs no discovery heuristics.
- **Contamination resistance.** Tasks are authored for this report, not lifted
  from public benchmark sets.

### 3.3 The target band, and the triggers

A task is useful while it **discriminates**. The intended operating range is a
panel pass-rate of roughly **40–85%**. That range is published as a statement of
*intent*; it is not the trigger, because a raw percentage is the wrong
instrument for an irreversible decision.

**The panel is defined before it is measured.** The panel is the edition's
declared model roster, fixed in the edition manifest **before any scored run**,
and capped at **8 scored models**. Roster selection follows a published rule:
each vendor's current flagship, each vendor's current value tier, and anything
released since the last edition. This matters because an undefined panel makes
the trigger depend on who we invited — dropping a weak model would "move the
failure edge" with no change in any model or task.

**The triggers are stated in models, not runs.** With 7 models at N=3, "85%"
means 18 of 21 runs — so a single flaky run would decide whether a task
graduates, and graduation is irreversible. Worse, a run-percentage cannot tell
apart two panels that both read 18/21: six models perfect plus one total failure
(a saturated task with one outlier) and four models perfect plus three splitting
(a task discriminating beautifully). Those demand opposite responses.

> **SATURATED** — all but at most one roster model passes **every** one of its
> runs. The task graduates (§5).
>
> **OVER-HARD** — fewer than half the roster models produce **any** passing run.
> Below that line there are too few passing cells to draw an efficiency frontier
> at all, which is the concrete harm. The task is re-authored or retired, and
> the event is logged and headlined exactly as a saturation event is.
>
> **IN-BAND** — anything else. The task keeps running unchanged.

Both triggers are arithmetic on the published per-run results: a reader can
recompute the verdict and check that we applied our own rule.

**Longitudinal claims use the like-for-like panel.** When comparing across
editions, the comparison is computed over **only the models present in both**,
with the full-roster figure shown alongside and roster changes annotated at the
date they occurred. Without this rule, ordinary roster churn would move a
published trend line with zero model improvement behind it.

**The EASY tier is exempt from the band.** It exists as a designed cost floor —
everyone is *supposed* to pass it — so it is never declared saturated and never
graduates on that basis. Its job is to price identical accepted work across
models and across time.

### 3.4 The calibration ladder — before a task enters service

No task reaches a scored panel without first being calibrated. This is
mandatory, and it exists because we have twice authored a task, run the full
panel, and discovered only afterwards that every model passed it.

Calibration climbs from the **weakest** model upward, because the weakest model
is the most sensitive difficulty detector available and the cheapest to run:

- **Two lanes, one per vendor, ordered by vendor capability tier** — not by
  price. Price is not a capability ordering: our cheapest model has also been
  among the most capable, so a price-sorted ladder would begin at the ceiling
  and mistake "our cheap model is good" for "the task is saturated".
- **Floor detectors** are the bottom rung of each lane. A rung is N=3.
- **Climb on failure** (a model fails its rung at ≥2/3 FAIL, so try the next
  model up that lane); **stop the lane on success** (≥2/3 PASS).
- **ACCEPT for a panel** when at least one model fails ≥2/3 *and* at least one
  passes ≥2/3 — the shape of a task that discriminates.
- **If the floor passes everything, do not climb — harden.** Climbing would only
  buy the expensive confirmation of something already known. Read the
  transcripts first to find out *what made it easy* — that finding is worth more
  than the task — then pull one pre-designed difficulty lever and re-run the
  floor alone.
- **If the whole ladder fails**, the task is over-hard; reduce one lever.

Calibration runs are **authoring data**. They are never published as scored
results, and they are recorded in the task's finding at reveal.

**The cheap-first rule is binding, and it is enforced by the harness, not by
memory.** No expensive model may be run against a task until a cheaper model
has already failed it. A task with no calibration record cannot be run on
anything above a floor detector — the harness refuses. This exists because the
opposite is the expensive mistake: we twice ran a full roster, flagship models
included, against a task that every model passed, and learned only afterwards
that the cheapest model in the panel could have told us the same thing for a
fiftieth of the money.

Calibration decides *whether* a task deserves a panel. It never replaces one: a
published edition runs the **full roster**, because the report's product is the
comparison across models, and a ladder that stops at the first passing model
produces no frontier, no cost spread, and no decision for a reader to make. It
is also why inversions stay visible — see §7.

#### 3.4.1 When a ladder IS a panel — the completion rule

A ladder that does not stop early, and instead continues until every model in
the declared roster has been run at the declared N, **is an edition panel** and
publishes as scored. Run order is not a property of the resulting data.

This needs saying because "calibration runs are authoring data" (above) is
otherwise read as a statement about *provenance*, and it is not — it is a
statement about *incompleteness*. Calibration data is unpublishable because a
ladder normally stops the moment it has learned what it needed, leaving a
partial roster at mixed N. Both of those are visible in the data itself, and
both are what make it unscoreable. Neither is caused by having run the cheap
models first.

A ladder becomes a panel when **all four** hold, and every one of them is
checkable after the fact from the archived bundles:

1. **Declared roster, complete.** Every model in the roster declared before the
   first run has a cell. No model was added because it did well, and none was
   dropped because it did badly.
2. **Uniform N**, at the declared sample size, in every cell.
3. **Like-for-like artifacts.** Every run in the panel used the identical prompt,
   judge and rate table — verified by comparing `prompt_file_sha256`,
   `judge_sha256` and `prices_yaml_sha256` across all bundles, not asserted.
4. **Pre-published task hashes.** The prompt and judge SHAs were committed to
   `TASKS.md` before the first run (§4.5 commit-reveal), so neither could have
   been tuned to the results.

**What this rule does NOT permit, and the reason the four conditions are
individually necessary:** a ladder that *changed* its roster or its N mid-flight
is not a panel and never becomes one. That is optional stopping — sampling until
the number is agreeable — and it is prohibited outright by §4.1. The sin is
deciding *when to stop based on what you saw*; running a fixed, pre-declared
design in cheapest-first order is not that, and paying flagship prices a second
time to relabel identical data would buy nothing but a different filename.

> **Applied 2026-07-23 (Steve's ruling), first use:** the `orgsync` panel.
> Roster of 7 fixed in `batch.sh` before the first run; N=3 declared per cell;
> all 21 runs verified to share prompt `94950b00`, judge `b948f904` and rate
> table `9ab9cba3`; both hashes published in `TASKS.md` under commit-reveal
> before any model saw the task. Conformant. Publishes as scored.

---

## 4. Execution

### 4.1 Sample size

**N=3 runs per cell** for any active scored task, across the **full declared
roster**, and **all runs are reported** — including failures, including a cell
where runs disagree.

> **Sampling level: N=3, under review.** Editions run at N=3. Whether that
> continues is an open decision, to be taken once the report's reception is
> known — replication is the single largest cost in an edition, and it is worth
> paying only if the resulting rigour is what the audience is actually reading
> the report for.
>
> The trade is stated here so the decision can be made on the facts rather than
> re-derived later. At N=1 a cell has no pass *rate*, so a single failure cannot
> be distinguished from a flake and no reliability claim survives one edition;
> and **cost dispersion cannot be computed at all**, since dispersion needs more
> than one observation — the error bar on the published cost figure disappears
> with it, along with the leading-indicator signal that a model near its limit
> thrashes before it fails.
>
> Edition 1 is the reference point that makes the trade legible: at N=3 it
> recorded haiku failing `orgsync` 3 times out of 3 on the identical assertion,
> with its own tests passing every time. At N=1 that is one failure,
> indistinguishable from noise, and there would have been no basis to call it a
> finding. If a later edition drops to N=1, it reports cost as a point estimate,
> labels it as such, and makes no reliability claim from a single edition.

**Replication is spent where it buys something.** Cheap models are replicated;
expensive models are not, unless a result demands it. The reasoning is that
replication buys two things — a pass-*rate* and a cost *error bar* — and both
are worth most exactly where a model is near its limit, which is where cheap
models live. Confirming that a flagship can do work a mid-tier model already
does reliably is the lowest-information run available, and the most expensive.

So: a model in the **floor and mid tiers runs N=3**; a model in the **top tier
runs N=1** on a task the tier below has already passed. A top-tier model is
replicated to N=3 only when its single run FAILS, or when it is the only model
that passes — in both cases the third run is where the information is. Any cell
published at N=1 is labelled as such, and an edition mixing N is honest about it
rather than averaging over the difference.

Anchor Set tasks run at **N=1 per edition** (§5.1): they are saturated by
definition, so replicating them buys no reliability information — they are
there to price the same work over time and to trip if a model ever fails work
its predecessors passed. Any anchor failure escalates that cell to N=3
immediately. Calibration (§3.4) is fixed at N=3 per rung. There is no optional
stopping anywhere in this protocol. Cherry-picking a
"representative" run is prohibited. N is **fixed and declared before the runs**:
we never add replicates after seeing a result. Adding runs to a cell that looks
unsettled is optional stopping — it biases the pass-rate and invites the fair
accusation that we ran until the number looked right.

Where runs disagree, the pass-*rate* is the result.

### The headline cost figure

> **expected cost per accepted outcome = (total spend over ALL runs of the cell)
> ÷ (number of passing runs)**

Every run's spend counts, including runs that failed. Failures cost real money,
and a reader deciding which model to point at their work is buying an *accepted
outcome*, not an attempt.

The obvious alternative — the mean over passing runs only — is
survivorship-biased and systematically flatters unreliable models. A model
costing $0.10 a run that passes one time in three reports as "$0.10" under that
definition, beating a $0.26 model that passes every time; but obtaining one
accepted outcome actually costs $0.30 from the first and $0.26 from the second.
The cheap model is the expensive one. We report the mean over passing runs too,
labelled **"cost when it works"**, because it answers a different and legitimate
question — but it is never the headline and never the sort key.

**Cost dispersion is reported alongside every cell figure** — the within-cell
variation across replicates. It is never scored, but it is not decoration: it is
the error bar on the number this report publishes, and at N=1 it cannot be
computed at all. It also behaves as a leading indicator of the failure edge. In
Edition 1, mean within-cell variation was 4.0% on the EASY task and 8.4% on the
MEDIUM one — but one cell (haiku on `cronspec`) ran 22.5% while every other
model on that task sat between 2% and 10%. That model passed the task 3/3, and
failed the next task up 3/3. A model near the limit of its competence thrashes
before it fails, and the thrash is visible in the cost of work it is still
completing.

A cell with **zero** passing runs has no cost-efficiency figure at all: cost
without an accepted outcome is not efficiency, it is just spend. Such a cell
publishes its total spend and an explicit "no accepted outcome in N runs". It is
never extrapolated, and it never receives an imputed figure.

This metric assumes failure is cheaply **detectable** — that you have tests or
CI that tell you the work is wrong. Where detection requires human review, add
your own review cost; the figure below it is a floor, not a total.

Edition 0's N=1 is the reason it is not protocol-conformant.

### 4.2 Isolation

Each run gets a fresh directory and a fresh `git init`. The harness **refuses**
to reuse an existing run directory — there is no staging, no retry-in-place, and
no partial-credit rerun.

Runs are executed **headless** and non-interactively:

- Claude Code: `claude -p --model <id> --dangerously-skip-permissions "<task>"`
- Codex: `codex exec -m <model> -C <dir> -s workspace-write --skip-git-repo-check`

Codex runs sandboxed (`workspace-write`), never `--ephemeral` — the ephemeral
flag suppresses the rollout log that spend is read from. Any interactively-run
leg is **flagged in the results table** and its wall-clock is marked
non-comparable.

No custom system prompts, no project instruction files, no MCP servers, no
tuned settings. The harness asserts that no agent-context file is reachable from
the run directory, and the assertion is recorded in the stamp — "we didn't
configure anything" is checkable rather than promised.

Two departures from stock configuration are stated rather than hidden, because
both are visible in the commands above:

- **Permission posture is not identical across vendors.** Claude Code runs with
  its permission prompts disabled and Codex runs inside `workspace-write`. Both
  are non-default and both grant equivalent practical autonomy — a headless
  unattended run cannot stop to ask — but they are not the *same* sandbox. Where
  a vendor's sandbox blocks something the task legitimately needs, that is a
  harness fault and the arm is voided, never scored as a model failure.
- **Authentication path is recorded.** Whether a leg billed against a
  pay-as-you-go API key or a subscription is stamped, because vendor terms —
  including whether traffic may be retained or trained on — can differ between
  them, and because it determines whether our list-rate pricing matches the
  billing path actually exercised.

### 4.3 Spend capture

Spend is read from **each tool's own local session log** — not from a proxy, not
from vendor billing APIs, not estimated from the transcript.

| tool | source | containment invariants verified |
|---|---|---|
| Claude Code | `~/.claude/projects/<cwd-slug>/<session>.jsonl` | reasoning is billed inside output and not separately reported; cache-read and cache-write are reported separately |
| Codex | `~/.codex/sessions/**/rollout-*.jsonl` (`token_count`) | `total == input + output`; `reasoning ⊆ output`; `cached_input ⊆ input` |

The scorer **asserts** these invariants and dies loudly if they break (e.g. a
codex log with `cached > input` is a fatal error, not a warning). Reasoning
tokens are never added on top of output. Cached input is subtracted to obtain
the uncached share.

**Pricing** is TIER's own audited `internal/store/prices.yaml` at **public list
rates, standard tier** — the same table the tool ships to its users. Cache reads
are priced at their real multiplier (0.1× for Anthropic), *not* at full input
rate: inflating cache reads to full price would manufacture a larger and false
number. Anthropic cache writes are priced 1.25× (5m) / 2× (1h); OpenAI has no
cache-write SKU, so any nonzero write is billed at input rate and warned about.

A model with no pricing row is **not** substituted with a neighbouring model's
rate. It is either priced by adding an audited row (with a `prices.yaml` version
bump) or reported explicitly unpriced.

**List price is a common yardstick, not a claim about anyone's bill.** Discounts,
subscriptions, batch tiers, and enterprise agreements all move real cost. The
report measures the metered value of the tokens consumed, which is the only
figure comparable across vendors.

### 4.4 Control arms

The harness is control-armed in both directions, because a benchmark that can
only pass is not a measurement:

- **Before:** no attributable session log may pre-exist for the run directory.
  (Claude Code keys logs by working-directory path, so a stale log from an
  earlier run of the same path is a real contamination risk — it is a fatal
  error, and archival relocates the log.)
- **After:** exactly one new session log; spend must be **> 0**. A $0 run is a
  capture failure, never a free run.
- **Web audit:** any scored run whose log contains `WebSearch`/`WebFetch`
  (Claude) or `web_search` (Codex) is **voided**, loudly.
- **Judge:** every judge is control-tested **both ways** before use — it must
  pass a known-good implementation and fail a known-bad one on the specific
  rules under test (§4.5).
- **Unknown task:** a prompt with no matching judge file is a fatal error, never
  a silent pass.

### 4.5 The judge

Each task has one hidden acceptance suite (`acceptance_<task>.py`) that the
model never sees. It tests **only prompt-stated behavior**.

Before a task is used, its judge is validated against at minimum:

1. a **reference** implementation (must PASS), and
2. one or more **plausible-wrong** implementations (must FAIL) — specifically
   the failure modes the task is designed to catch, e.g. a standard-library
   implementation of the familiar system the task deviates from, and a careful
   but naive from-scratch attempt.

`cronspec` v2's three-way control is the model: reference PASS · standard-cron
implementation FAIL (101 failures) · naive-careful FAIL (20 failures), with the
failures landing on exactly the deviations under test.

**Commit-reveal.** A judge's SHA-256 is committed publicly *before* its runs and
carried in every run's stamp; the judge's source is revealed in full when its
task **graduates or is retired** (§5.5) — not at the end of each edition, which
would leak the constants of a task still in service. Until reveal, what is
published in place of the source is the judge's **control-test transcript**: the
reference implementation passing, and each plausible-wrong implementation
failing, with the failure counts and the rules they landed on.

Be precise about what this does and does not buy. Commit-reveal defends against
**judge-tuning** — it proves we did not adjust the grader after seeing who won.
It is **not** a contamination defense, and we do not present it as one.

---

## 5. How the standard evolves — the saturate-into-anchor cascade

**The standard is the protocol, not the task list.** Tasks are expected to be
outrun; the protocol is not.

### 5.1 Graduation

When a scored task **saturates** (§3.3), it does not get quietly deleted and it
does not get quietly edited. It **graduates** into the frozen **Anchor Set**:

- The Anchor Set holds **at most 3 tasks**.
- **Anchor tasks are never edited.** Not the prompt, not the judge, not a typo.
  A frozen task is a longitudinal instrument; editing it destroys the series.
  Any change whatsoever mints a new task version (§5.5).
- Anchor tasks keep running. They serve two purposes: a **longitudinal cost
  anchor** (the same work, priced across time and models) and a **regression
  tripwire**. A newly released model failing an anchor task is a **headline
  finding** — but only when it **replicates**: a single failing run out of three
  is published as a flake with its runs shown, never as a vendor regression.
- To keep an edition's cost bounded — per-edition cost must not grow with the
  number of tasks ever authored — anchors run at **N=1 on rotation**. Any anchor
  failure immediately escalates that cell to N=3 for confirmation.

**Pruning does not delete and does not edit.** When a fourth task graduates, one
anchor stops being *run* each edition. Its archive stays complete, public and
immutable, and its historical series stays published and citable; the series
simply ends, unaltered, at a stated date. Nothing is ever removed from the
record. Pruning selects the **least discriminating** anchor — the one whose
cross-model cost spread has collapsed, meaning it no longer separates anything —
never the oldest, because the oldest anchor has the longest series and the
longest series is the most valuable thing a longitudinal instrument owns.

The saturating task's replacement is authored fresh for the frontier. The
saturated version is archived complete — prompt, revealed judge, all results,
and a `FINDING.md` recording what it showed and why it graduated.

### 5.2 Saturation is a result, not an embarrassment

A task saturating means the models got better at that work. That is a
**capability finding and it is published as a headline**, in the edition where
it happened, with the pass-rate that triggered it.

### 5.3 Language rule

The report says **"the failure edge moved"**. It does not say "we made it
harder." The distinction is not cosmetic: the first describes the models, which
is what the report is about; the second describes us, and invites the suspicion
that difficulty is tuned to produce a desired ranking.

### 5.4 What is public, and when

The task archive is public. An unpublished benchmark is an unfalsifiable one,
and "trust our hidden tasks" is not a standard a vendor-adjacent publisher can
ask anyone to accept.

| artifact | policy |
|---|---|
| prompts | **public immediately**, versioned, archived on supersession |
| results, stamps, run bundles, difficulty metadata, the ledger | **public immediately**, always |
| **judges** | SHA-256 public immediately; **source revealed at graduation or retirement** |

The judge is the single sealed artifact, because the judge is the answer key.
Everything needed to see what we test against and how the bar has moved —
prompts, difficulty metadata, and the full pass-rate history — is public from
day one.

**Publication is the contamination channel, and we say so.** A published task
enters the next generation's training data, with repetition and an answer
attached. We accept that cost deliberately in exchange for falsifiability, and
we manage it two ways: the active frontier judge stays sealed until its task
graduates, and each scored task is paired with an **unpublished variant** —
identical in structure, differing in constants — so the instrument keeps
measuring capability after the public member is absorbed. A partial publication
delay is explicitly rejected: editions fire every few weeks while training
cutoffs move in 6–12 month steps, so a short embargo protects nothing while
costing transparency now.

**Anchor pass-rates carry a contamination caveat** from their third edition
onward. This is disclosed rather than defended: a task that is almost certainly
in a model's training data no longer cleanly measures capability. Two things
survive it — the **cost** anchor (a memorized task is a *cheaper* task, and
"familiarity reduced the price of this work by X%" is itself a legitimate
finding) and the **tripwire** (a model failing work it has almost certainly
seen is a louder alarm, not a quieter one).

### 5.5 Versioning

**The versioned unit is the bundle**, not its parts: prompt, judge, reference
implementation, the plausible-wrong controls, the control self-test, and the
difficulty metadata, hashed together. Versioning the prompt and judge separately
would create an identity no one can cite — "prompt v2 with judge v3" is not a
task.

- Public identifier: `<family>/v<N>` — `cronspec/v1`, `cronspec/v2` — plus the
  bundle's content hash. **Ordinals, never semver:** a dotted version implies a
  compatible patch, which invites exactly the "just fix the judge" thinking this
  section exists to prevent.
- **A version's hashed contents never change once it has a scored run.** Any
  change — including a typo — mints a new version. Errors are corrected by
  publishing a successor and a dated erratum, never by editing what is published.
- **Status is append-only and separate from identity.** Graduating `cronspec/v1`
  does not rename it; it appends a status event. Immutable bundles, append-only
  status. Statuses: `active`, `anchor`, `graduated`, `retired`, `sealed`.
- **There is no delete path.** `sealed` means a task stops being scored while its
  bundle stays public and its hash stays valid. Stating this now means later
  pressure to remove something meets a pre-committed answer instead of a
  judgment call.

**What permanence covers.** We guarantee the **ledger entry** is permanent —
prompt, judge, results, hashes, findings. We do **not** guarantee every version
stays *re-runnable* on current tooling: CLIs auto-update, models retire, and
dependencies rot. Promising perpetual re-runnability would be a broken promise
on a schedule (§8).

### 5.6 Escrow

From the edition at which it takes effect, **each edition publishes the hash of
the *next* edition's frontier task and judge** before the current edition's
results exist. This is what makes "you wrote a harder task once you saw who was
winning" not merely denied but arithmetically unavailable.

Because a pre-committed task can turn out to be wrong — a pre-flight probe may
show it saturates on contact — **replacement is permitted and silence is not**:
a replaced escrow task is revealed in full, with the reason, at the edition that
would have used it.

The commitment witness must not be something we alone control. "Trust our commit
history" is the weakest available claim for a report whose entire value is
neutrality, so the hash is additionally anchored to a timestamp proof we cannot
forge.

### 5.7 The lineage receipt

Every edition ships a **manifest** listing, for every task that has ever been
scored: its tier, the editions it ran in, its current status (active / core /
graduated / retired), and the SHA-256 of its prompt and judge. This is the
answer to "did you cherry-pick the tasks that make your point?" — the full
lineage is on the record, append-only, in `TASKS.md`.

---

## 6. Reproducibility stamp

Every edition — and every individual run, in `stamp.json` — carries:

| field | why |
|---|---|
| date (UTC) | rates and models both move |
| TIER version | the measuring instrument |
| `prices.yaml` version + SHA-256 | the rate table used, exactly |
| prompt name + SHA-256 | proves the task was not edited mid-edition |
| judge SHA-256 | commit-reveal: fixed before the runs |
| tool + version | `claude` / `codex` CLI build |
| model flag | the exact model ID billed |
| machine | single-machine caveat, stated |

---

## 7. Standing caveats (published with every edition)

- **Single machine, single operator, default configs.** Not a datacenter-scale
  evaluation.
- **Token totals are not comparable across tools without the dollar column.**
  Tokenizers, system prompts, and caching behavior differ per tool; comparing
  raw token counts across vendors is meaningless.
- **List-rate pricing** — see §4.3. Not anyone's actual invoice.
- **Wall-clock is not comparable** between headless and interactive runs, and is
  never scored.
- **Small N.** N=3 detects gross reliability differences, not small ones. No
  statistical significance is claimed, and none is implied by ordering. Be
  concrete about how weak this is: a cell's pass-rate has only four possible
  values, so a genuinely 70%-reliable model and a 90%-reliable one are not
  distinguishable at all. Runs within a cell are also correlated — a model
  either read the spec correctly or it didn't — so a 21-run panel carries
  materially less information than 21 independent trials. Tables sort by cost;
  **ordering is presentation, not ranking**, and adjacent rows should be read as
  indistinguishable unless their intervals plainly separate.
- **Rates are ours to get wrong.** Spend is *derived*: tokens are measured, and
  dollars are tokens times a rate table we maintain. Each row cites its vendor
  price page. A rate correction re-derives the dollar column from unchanged
  token evidence — it is not an edit to the measurement.
- **Models are moving targets.** An edition measures the models on its stated
  date, at their stated versions. It is a snapshot, not a standing claim.
- **Price is not capability, and we do not assume it is.** Nothing here treats a
  cheaper model as a weaker one. Our own record contains a model that is
  simultaneously the cheapest we run and among the most capable. Because every
  edition runs the full roster on every active task, an **inversion** — a dearer
  model failing work a cheaper model completed — is detected automatically
  rather than assumed away. An inversion is reported as a **headline finding**,
  not a footnote: it is the clearest single demonstration that spend and yield
  are different quantities, which is the premise of this entire report.

---

## 7a. The TIER score in reports — LOCKED (Steven Job, 2026-07-23)

**Every published report table carries the TIER score.** A report that publishes
only `E[$/accepted]` has published the *reciprocal* of the metric this
instrument exists to establish, and teaches a reader nothing about what a TIER
score is or what a good one looks like. `E[$/accepted]` may appear alongside it;
it may not appear instead of it.

    TIER = points / (cost / $1,000)          HIGHER is better
    E[$/accepted] = cost / accepted           LOWER is better

Column direction must be marked (↑ / ↓) wherever both appear.

### The difficulty → weight mapping — LOCKED

TIER's weight scale is locked (xs 0.5 · s 1 · m 3 · l 5 · xl 8) and is defined
over **merged pull requests**. A benchmark task is not a merged PR, so the
mapping below is a **declaration**, not a derivation:

| task difficulty | weight | scale point |
|---|---|---|
| easy | **1.0** | s |
| medium | **3.0** | m |
| integration | **5.0** | l |

**This mapping is fixed across editions and must be printed in every report that
publishes a TIER score.** Declared, the number is a real TIER score. Undeclared,
it is a number that merely looks like one — and a reader who checks will find it
asserted rather than derived, which is the failure this protocol exists to
prevent. Changing the mapping changes every score, so a change is an edition
event with a changelog entry, never a quiet edit.

Scores remain **per task** (§1). No combined TIER score across tasks, ever.

## 8. Publication rules

- Editions are **dated**, published at `/reports/<yyyy-mm>`, and never
  retroactively edited. Corrections are appended and dated, with the original
  text retained.
- Cadence is **on major model releases**, not on a calendar treadmill.
- Per-run artifacts ship with the edition: `result.json`, `stamp.json`, the
  **session log** the spend was read from, the **rate-table snapshot** it was
  priced against, the model's **submission**, agent output and judge log. The
  session log is not optional — without it the dollar half of the headline
  metric is an assertion rather than evidence.

- **The published session log is redacted to the fields the cost derives from**,
  and its original's SHA-256 ships alongside it in the evidence manifest. A raw
  session log is a full transcript: prompts, model reasoning, tool output, file
  diffs, the operator's home-directory paths, and the vendor's proprietary
  system prompt. Publishing all of that to prove an arithmetic claim would
  disclose far more than the claim requires, and it is not ours to disclose.

  The redaction keeps the usage counters, the model, the message or session
  identity, and the timestamps — nothing else — on an **allowlist**, so a new
  transcript field in a future CLI version cannot silently begin flowing
  through. Verifiability is unaffected because the cost arithmetic never
  depended on anything else: the verifier re-derives every published number
  from the redacted form, and a control arm asserts the published tree contains
  no home-directory path, no vendor prompt, and no transcript content — and
  fails on an unredacted log, so it is a real check rather than a rubber stamp.

  What a reader gives up, stated plainly: without holding the original they
  cannot confirm it hashes to what we published. They can still confirm that
  our numbers follow from the evidence we shipped, that the evidence is
  internally consistent, and that we did not retype it. That is the claim this
  section makes, and it survives intact.
- **The reproducibility claim is re-verification, not re-execution.** Model runs
  are non-reproducible in principle: sampling is stochastic, CLIs auto-update,
  and models retire. What *is* checkable, offline and indefinitely, is the
  arithmetic: given these session logs and this rate table, this cost is
  correct; given this code, this judge returns this verdict. A stdlib-only
  verifier ships with the evidence so any reader can re-derive every published
  number without trusting us. Re-running the harness checks the procedure; it
  will not reproduce the snapshot, and we do not claim it will.
- The harness and every revealed task and judge are open source.
- Intenteon properties **link** the report; they do not restate its numbers.
  A summary that drifts from the source is how neutral measurement dies.

---

## 9. Protocol changelog

This table starts at first publication. Edits made *before* v1.0 was published
are not listed individually — an unpublished draft has no readers to protect,
and pretending otherwise would inflate the record. From v1.0 onward, every
change is listed here, dated, and attributed to the edition it took effect in.

| version | date | edition it took effect | change |
|---|---|---|---|
| 1.1 | 2026-07-23 | Edition 1 (retroactive to publication) | Adds §7a: every published report table must carry the TIER score, not only its reciprocal `E[$/accepted]`, with column directions marked. Locks the benchmark difficulty->weight mapping (easy 1.0/s, medium 3.0/m, integration 5.0/l) as a **declaration** that must be printed in every report publishing a score, because the locked weight scale is defined over merged PRs and a benchmark task is not one. Ratified by Steven Job after a published launch artifact was found to contain no TIER score. |
| 1.0 | 2026-07-22 | Edition 1 | Initial publication. Codifies practice from Edition 0 plus: the weakest-first calibration ladder (§3.4); reported cost dispersion (§2, §4.1); replication spent on cheap models, N=1 for top-tier confirmation (§4.1); redaction of published session logs to the cost-bearing fields (§8); N=3 with no optional stopping; expected cost per accepted outcome as the headline metric; model-unit saturation and over-hard triggers against a pre-declared roster; the like-for-like panel rule for longitudinal claims; the saturate-into-anchor cascade with the Anchor Set; the public task archive with judges sealed until graduation; bundle versioning with append-only status; escrow of the next edition's task hash; re-verification (not re-execution) as the reproducibility claim; and the full outcome enum with published counts. |
