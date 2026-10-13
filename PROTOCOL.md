# The TIER Model Report — Measurement Protocol

**Version 1.5 · published 2026-07-22, amended 2026-10-13 · Edition 2 rulings apply prospectively; Edition 1 remains frozen.**

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
declared model roster, fixed in the edition manifest **before any scored run** except for the R-2026-10-13-1 late-admission ruling,
and capped at **8 scored models** for Edition 1. **Edition 2 alone has a cap of 10** (R-2026-10-13-1, 2026-10-13; §9). Roster selection follows a published rule:
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

For Edition 2's ten-model panel (R-2026-10-13-1): **SATURATED = ≥9 of 10 models pass every
run; OVER-HARD = ≤4 of 10 produce any passing run.** Edition 2 anchors have **N=3 for every cell** under R-2026-10-12-24 and R-2026-10-13-1; the trigger counts models, using all
of each cell's declared runs.

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
> table `9ab9cba3`. Conformant. Publishes as scored.
>
> **CORRECTED 2026-07-29 — condition 4 was not met for one run, and this ruling
> previously overstated it.** The sentence here read "both hashes published in
> `TASKS.md` under commit-reveal before any model saw the task." That is true of
> the prompt hash and **false of the judge hash**. Measured from this repository's
> own git log and run stamps:
>
> | event | time (UTC) |
> |---|---|
> | prompt `94950b00` committed to `TASKS.md` (`5f03656`) | 2026-07-23T00:33:36Z |
> | **`haiku/orgsync/rep1` verdict — a FAIL** | **2026-07-23T00:54:01Z** |
> | **judge `b948f904` committed to `TASKS.md` (`d85daa2`)** | **2026-07-23T00:58:35Z** |
> | `haiku/orgsync/rep2` verdict | 2026-07-23T01:24:18Z |
> | `haiku/orgsync/rep3` verdict | 2026-07-23T01:54:11Z |
>
> The judge's hash reached the public ledger **4m34s after rep1's verdict was
> known.** rep2 and rep3 ran after publication and are clean; rep1 is the whole
> of the defect.
>
> **This is not evidence of tuning, and the distinction is the point of the
> correction.** rep1's own `stamp.json` records `judge_sha256 = b948f904` — the
> final judge was already on disk when rep1 ran — and `acceptance_orgsync.py`
> still hashes to `b948f904` today, which `verify.py` checks every run. The judge
> did not change. What failed is the *ordering* that makes that fact checkable by
> a stranger: condition 4 exists so a reader need not trust our stamps, and for
> rep1 a reader must. For the other 20 runs they need not.
>
> Anyone can settle rep1 independently rather than taking this on trust: the judge
> is revealed, the submission is archived, and re-running the one against the
> other reproduces the verdict.
>
> The remedy is disclosure, not a quiet reword — the timeline is recoverable from
> our own git log, so hiding it would be both dishonest and futile. Going forward
> the harness should refuse to run a task whose judge hash is not already
> committed, which makes condition 4 structural instead of procedural.

---

## 4. Execution

### 4.1 Sample size

**Edition 2 ruling (R-2026-10-12-21, 2026-10-12): “If you did not run the
multiple runs yet. Then yes, let's just do one run each.”** This supersedes
R-2026-10-12-18 (“Yes, 3 runs each”) before any multiple paid runs were launched
by the fix pass. Edition 2 keeps the anchor rule: one run per model, expanding
a non-PASS or missing rep1 result to N=3. Its report publishes without run-to-run
range/dispersion and explicitly discloses that limitation; N=1 cells provide
point estimates. Existing valid rep1 bundles count; `batch.sh` skips any rep
with a `result.json` and never overwrites it. The sole declaration remains
`editions/2026-10/MANIFEST.json`; the temporary root amendment is removed.

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

A cell with **any unknown (`cost_list_usd: null`) run cost publishes NO SCORE**,
even if another replicate passed. No cost-per-accepted or TIER figure is derived
from partial spend; the offline verifier fails the cell explicitly.

### 4.2 Isolation

Each run gets a fresh directory and a fresh `git init`. The harness **refuses**
to reuse an existing run directory — there is no staging, no retry-in-place, and
no partial-credit rerun.

Runs are executed **headless** and non-interactively. Exact commands and
network/sandbox postures for all four legs are in `README.md`; `DRY_RUN=1`
prints their actual argv and clean-config environment without invoking a CLI.
Codex retains `workspace-write` and session logging (never `--ephemeral`).

**Effort (R-EFFORT D, v1.3): each model runs at its CLI's shipped default under
guarded configuration.** Codex and OpenCode receive fresh home/XDG config trees;
only auth credentials may be copied. Codex copies `auth.json` from the operator's
`CODEX_HOME` to clean `CODEX_HOME`; OpenCode copies only the selected provider's
`auth.json` entry to clean XDG data. Credentials are never printed.
Claude and Muse use the operator's **real HOME and default config/auth locations**
with real USER/LOGNAME resolved from the OS account: their logins are bound to the
macOS keychain, and copying an auth metadata file cannot carry the secret.
Neither leg receives XDG overrides, `MUSE_AUTH_PATH`, `CLAUDE_CONFIG_DIR` or API
keys. The rest of their `env -i` allow-list is PATH, LANG/LC_ALL, TERM/NO_COLOR
and TMPDIR. Claude bills its logged-in subscription; Muse its logged-in account.
Per the operator's 2026-10-12 ruling, Claude uses **`--safe-mode`, not
`--bare`**, because `--bare` disables OAuth/keychain login. Customisation remains
disabled by `--safe-mode --setting-sources '' --strict-mcp-config`, an empty MCP
config, and Task/Agent denial. `auth_policy` and `config_policy` in each stamp
record this distinction. Claude passes `--session-id <run-UUID>` and captures
exactly `~/.claude/projects/<cwd-with-/.-replaced-by-->/<run-UUID>.jsonl`.
Codex uses `--ignore-user-config --ignore-rules`. OpenCode's injected
policy is the only user config: clean XDG prevents merging operator settings;
project config, external skills and Claude skills are disabled; `--pure` skips
external plugins. Muse retains `--no-foreign-personal-context`, reminders/web
disabling, native sandbox and approval/contributor controls. Capture reads
`~/.local/share/muse/sessions/YYYY/MM/DD/<run-UUID>/session.jsonl`, requiring that
UUID plus exact workspace metadata and explicit child links. The stamps record
both keychain-bound auth policies. `clean_config` is true only for Codex/OpenCode,
false for Claude/Muse. Muse explicitly selects `--provider meta`; only paid Muse
legs (including its preflight probe) audit JSON settings key names outside
`{schema_version, model, provider}`, including effort/MCP/instruction keys.
Auth, trust, lock/dot-lock and non-`.json` files are excluded. Malformed or
unreadable real JSON settings still refuse; values are never reported. Muse
settings do not block the other tools' legs. Effort
observed without a source is `observed-unsourced`. Existing four valid Claude
rep1 PASS bundles retain their original stamps; the report must disclose their
legacy hard-coded `clean_config: true` as operator-home runs.
Codex enables `sandbox_workspace_write.network_access=true`; Muse uses
`--sandbox-network enabled`. Both permit full network including loopback with
filesystem sandboxes retained. OpenCode/Claude permit shell network; web tools
remain forbidden. Each new stamp records `network_policy` and `trust_policy`.
Muse has no documented inverse to `--trust-workspace`: inherited trust is a
recorded residual, with no trust flag passed; trust state is excluded from the settings audit.
Copied Codex/OpenCode credentials are deleted on exit after capture for all runs.
Inherited effort/reasoning/variant environment overrides and managed configs
that could override isolation cause a pre-spend refusal. No effort flag is set.

**Observed effort is recorded per run**, read only from structured session
metadata or request fields. The stamp/result records `effort`, `effort_source`
(the recorded source, or `observed-unsourced` when absent), and the evidence field/source checked.
If absent, it records **not observable** plus the source checked. A documented
default is never substituted for an observation. Runs made under an inherited
operator setting are **VOID**. The **2026-10-12 first paid Edition 2 attempt
was VOID**, a harness auth failure: all Claude legs reported “Not logged in ·
Please run /login” with zero tokens; Codex/OpenCode/Muse never ran. It is **not
part of Edition 2's record**. The operator moved the void bundles out of the
tree. Four later valid Claude rep1 orgsync bundles (Fable 5.1, Opus 5.5, Sonnet 5.5 and Haiku 5.5) are PASS and retained unchanged; they count as the N=1 anchor runs.

Every Edition 2 results table carries: **“effort scales differ by vendor; each
model ran at its CLI's shipped default (stamped)”**. Edition 1 effort was
**unrecorded**; cross-edition comparisons hold only where defaults are unchanged,
with each vendor's dated release notes cited: [Claude Code changelog](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md),
[Codex releases](https://github.com/openai/codex/releases),
[OpenCode releases](https://github.com/anomalyco/opencode/releases), and [Muse Code changelog](https://dev.meta.ai/docs/muse-code/changelog)
(release entries must be obtained and dated before a longitudinal claim).
The dated CLI versions in stamps are the join keys; these links are not proof
that defaults stayed unchanged. No cross-edition effort equivalence is claimed
by this build. A **default-vs-high sensitivity study** is pre-declared as a
**separate later study**, scoped by a cost pilot first, outside this edition.

Any `mcp__*`, browser/computer-use, or web-tool invocation **voids** an offline
run. A Claude `subagents/` directory fails capture loudly because its spend
would otherwise be dropped. A sandbox denial recorded in the session log voids
the run with a reason. Capture failure is verified as capture failure even when
a partial native log was archived/redacted.

The operator's 2026-10-12 real preflight passed Codex (13,925 tokens) and
OpenCode (5,974 tokens), but Claude and Muse failed auth because redirected
config/auth locations prevented resolving keychain-bound login secrets. The
correction above preserves their real home/config and identity. Those probes
are separate from Edition 2's record; all four require a fresh operator preflight.

**Auth/capture preflight is mandatory before a paid batch.** `./preflight.sh`
calls each current tool once to fetch a local HTTP endpoint using a shell command, choosing the
cheap Claude/Codex floors and the sole OpenCode/Muse roster models. It runs in
throwaway directories through `run.sh`'s `PREFLIGHT=1` mode: the same environment,
argv shape and collectors as scored legs, without task fixtures/judges or scored
bundles. Each leg must exit 0, contain no login/auth prompt in its output
(`Not logged in`, `/login`, `authenticate`, `API key`), and capture an attributable
session whose summed tokens exceed zero. Token counts are printed; preflight's
small spend is separate from the edition record. A disposable server bound to
127.0.0.1 must observe each tool's request, and its random response body must
appear in that CLI's output. A tool unable to reach loopback fails preflight.

Only four passing probes write timestamped per-tool receipts to gitignored
`.preflight/pass.json`. A failed new attempt invalidates previous passes.
Non-`DRY_RUN` `batch.sh` refuses before launching a leg unless **all four tools
passed within the last 60 minutes**. Changed runner/auth-source context also
invalidates passes. The fingerprint hashes runner/preflight code, token_evidence.py,
score.py, run_audit.py, edition.sh and the edition manifest; receipt tool/model and
loopback success are required. Dry runs neither call models nor grant preflight passes.
The smoke roster is the current Edition 2 roster even before a historical batch;
this auth/capture check does not change frozen task bundles (§5.5).

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

GLM's `billing_route` is stamped as **zai-coding-plan subscription**. It is
**priced at the list per-token rate; the actual route is a subscription**.

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
are priced at their real multiplier from the rate table, *not* at full input
rate: inflating cache reads to full price would manufacture a larger and false
number. Anthropic cache writes are priced 1.25× (5m) / 2× (1h). OpenAI's pricing page
now lists a cache-write price (1.25× input, fetched 2026-10-12); until a rate
audit adopts it, any nonzero OpenAI write is billed at input rate and warned
about. Edition 2's three Codex runs recorded no cache writes.

A model with no pricing row is **not** substituted with a neighbouring model's
rate. It is either priced by adding an audited row (with a `prices.yaml` version
bump) or reported explicitly unpriced.

**Edition 2 pricing (R-PRICE A):** gpt-6.1-sol remains at OpenAI's announced
standard $2/$10 per million, cached input 0.05× ([OpenAI model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol),
as of 2026-10-12). The promotional-price claim is a **third-party, unverified
claim**; if true, cost is **understated**. Haiku 5.5 is confirmed by
[Anthropic's 2026-10-07 announcement](https://www.anthropic.com/claude-haiku-5-5):
$0.10/$0.50 for prompts ≤100K, reads 0.1×; above 100K it is $0.50/$2.50.
Every original roster row carries its source URL and as-of 2026-10-12;
GLM 5.3 Flash carries as-of 2026-10-13 under R-2026-10-13-1. OpenAI cache-write
multipliers are removed so this table and the existing no-write-SKU arithmetic
agree (operator ruling/Y3). The current OpenAI page lists a write price; adopting
that would require a separate protocol/rate audit, not a silent change here.
**Re-check on 2026-11-21**: if a list price changes, publish a **dated price-change
note**, re-deriving dollars from unchanged token evidence. **The edition's table
is not altered.** The pre-run benchmark snapshot is version 11, derived from
TIER v12 with these declared changes; Edition 1's v8 bytes remain frozen.

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
| 1.5 | 2026-10-13 | Edition 2 only; GLM 5.3 Flash late admission | **R-2026-10-13-1** admits GLM 5.3 Flash after the original 27 runs, with three reps on orgsync/v1 at v13, preserving the original v11 run pins. Edition 2's cap becomes 10; SATURATED requires ≥9/10 models to pass every run and OVER-HARD requires ≤4/10 to produce any passing run. This is the declared exception to fixing the roster before any scored run; every cell is N=3 together with R-2026-10-12-24. |
| 1.4 | 2026-10-12 | Edition 2 at N=3; Sonnet 5.5 report-table correction | First-hand operator rulings: **R-2026-10-12-24** (“let's run it three times really quickly.. run them in parallel if you can”) supersedes R-2026-10-12-21 — reps 2–3 ran for all nine models in four parallel per-tool lanes (each lane sequential, so no two runs of one CLI share capture paths), and the report prints run-to-run range and dispersion. **R-2026-10-12-25** re-prices `claude-sonnet-5-5` cache reads to 0.05× (Anthropic pricing page read 2026-10-12, footnote 2) as an operator-ruled exception to §4.3's frozen table, made after the last run and before publication: `prices.yaml` v12 is the edition's report table (MANIFEST `report_rate_table`; TIER_PIN retains the v11 run pin); every bundle keeps its v11 snapshot as evidence and `verify.py` checks both. **R-2026-10-12-26** places this work in PR #14. |
| 1.3 | 2026-10-12 | Edition 2 only; superseding N=1 ruling retains valid rep1 | First-hand operator ruling after decision panels: **R-CAP A** raises the cap to 9 only for Edition 2 (SATURATED ≥8/9 perfect; OVER-HARD ≤4/9 with any pass); **R-LADDER B** keeps only Haiku 5.5 / gpt-6-luna as floors, admits non-floors to new tasks only after a floor fails, and runs Edition 2 anchors under the logged ALLOW_EXPENSIVE override; **R-EFFORT D** uses each CLI's shipped default under clean Codex/OpenCode configs and guarded operator-home Claude/Muse configs, stamps observed effort/source or not observable, voids inherited settings, declares Edition 1 effort unrecorded and defaults-unchanged release-note limits on comparisons, and pre-declares a separate default-vs-high study after a cost pilot; **R-PRICE A** retains announced Sol $2/$10 and 0.05× reads, labels promotional claims third-party/unverified (if true, understated cost), confirms Haiku via Anthropic, removes OpenAI write multipliers under Y3, adds every roster row's URL/as-of, and pre-declares the 2026-11-21 price re-check and dated re-derivation note without altering the edition table. **R-2026-10-12-18**, “Yes, 3 runs each”, proposed N=3 for range/dispersion comparable with Edition 1, but is **superseded by R-2026-10-12-21**, first-hand quote “If you did not run the multiple runs yet. Then yes, let's just do one run each.” Edition 2 retains **N=1 anchors; non-PASS or missing rep1 expands to N=3**. No multiple paid runs were launched by the fix pass. The report publishes without run-to-run range/dispersion and explicitly says so; existing valid rep1 counts and any existing result is skipped. Anchors-only orgsync/v1 at N=1 with failure expansion, frozen task hashes, native capture and four-leg isolation are declared in the manifest/README. Ratified by Steven Job, 2026-10-12. |
| 1.2 | 2026-07-29 | Edition 1 (retroactive to publication) | Corrects §3.4.1's first-use ruling, which asserted that both the prompt and judge SHAs were committed to `TASKS.md` before any model saw `orgsync`. True of the prompt hash; **false of the judge hash**, which reached the ledger 4m34s AFTER `haiku/orgsync/rep1`'s verdict. Condition 4 of §3.4 was therefore not met for that one run. The judge itself did not change — rep1's stamp records the final `judge_sha256` and the file still hashes to it — so the failure is of the ORDERING that lets a stranger check that without trusting our stamps, not of the panel's integrity. rep2 and rep3 are clean. Disclosed rather than reworded, because the timeline is recoverable from this repository's own git log. Ratified by Steven Job after an internal audit measured the gap. |
| 1.1 | 2026-07-23 | Edition 1 (retroactive to publication) | Adds §7a: every published report table must carry the TIER score, not only its reciprocal `E[$/accepted]`, with column directions marked. Locks the benchmark difficulty->weight mapping (easy 1.0/s, medium 3.0/m, integration 5.0/l) as a **declaration** that must be printed in every report publishing a score, because the locked weight scale is defined over merged PRs and a benchmark task is not one. Ratified by Steven Job after a published launch artifact was found to contain no TIER score. |
| 1.0 | 2026-07-22 | Edition 1 | Initial publication. Codifies practice from Edition 0 plus: the weakest-first calibration ladder (§3.4); reported cost dispersion (§2, §4.1); replication spent on cheap models, N=1 for top-tier confirmation (§4.1); redaction of published session logs to the cost-bearing fields (§8); N=3 with no optional stopping; expected cost per accepted outcome as the headline metric; model-unit saturation and over-hard triggers against a pre-declared roster; the like-for-like panel rule for longitudinal claims; the saturate-into-anchor cascade with the Anchor Set; the public task archive with judges sealed until graduation; bundle versioning with append-only status; escrow of the next edition's task hash; re-verification (not re-execution) as the reproducibility claim; and the full outcome enum with published counts. |

### Edition 2 build declaration — see the dated v1.4 ruling above

The 2026-07-26 ruling selects an **anchors-only** Edition 2, with one rate table
per panel and the roster fixed before any scored run. The 2026-10-12 ruling adds
GLM 5.3 through OpenCode and Muse through its own CLI and names current Claude
and Codex IDs. `editions/2026-10/MANIFEST.json` declares the ten-model roster under R-2026-10-13-1,
`orgsync/v1` at N=1 with non-PASS/missing rep1 expansion to N=3 (R-2026-10-12-21 supersedes R-2026-10-12-18) — superseded by R-2026-10-12-24: N=3 for all nine, its unchanged task hashes,
and the rate-table hash. Only Haiku 5.5 / gpt-6-luna are floors; GLM and Muse
are never floors. A new task opens to non-floor models only after a floor
failed it; Edition 2 anchors run under **logged ALLOW_EXPENSIVE=1**.
R-2026-10-13-1 adds GLM 5.3 Flash through `zai-coding-plan` for three reps on
`orgsync/v1` in PR #14, making 30 planned runs across ten models. The existing
27 runs retain their v11 pins; Flash has a per-model late-admission v13 pin.
Reports price every run from the v13 report table. GLM 5.3 Highspeed is excluded.
All ten requested models are retained: this is an
Edition 2 exception to §3.3's eight-model cap, not a retroactive Edition 1 change.
The active EASY floor and unresolved cronspec graduation are not counted as
anchors. The sealed frontier escrow remains outside this fast panel; its
limitations in `TASKS.md` are not resolved by this harness build.

Every new run must supply an edition name. Edition 2 has a separate bundle root
and rate pin. Native-store capture, token invariants, sandbox/permission posture,
redaction, and remaining pricing questions are documented in `README.md`.
This fix pass makes no new scored execution or capability/capture-parity claim;
fixture tests establish parser behavior, not a real-model measurement.
