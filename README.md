# TIER Model Report — public kit

The Model Report measures cost per passing outcome on published coding tasks:
total spend across attempts, derived from captured token usage at the recorded
list rates, divided by outcomes accepted by the task's tests. Each result describes a model running through a
particular CLI and configuration. These are historical case studies, not a
ranking of general model ability or a measure of production quality.

Read the [Model Report](https://tiermetric.org/reports/) on the
[public TIER site](https://tiermetric.org).

This kit contains the reports, run bundles, redacted usage evidence, revealed
prompts and judges, verification tools, and screened harness source. Start with
[July's report](editions/2026-07/REPORT.md) or
[October's report](editions/2026-10/REPORT.md), and read their
[corrections](ERRATA.md) alongside them. No private repository, vendor CLI,
credentials, or model access is needed to verify the shipped evidence.

## Verify an edition

Open a terminal in the downloaded kit's root directory. Ordinary verification
uses Python 3's standard library and works offline. It checks archived evidence,
hashes, token accounting, recorded prices, published figures, and errata chains;
it executes no submitted programs and calls no models.

```bash
python3 verify.py
python3 verify.py editions/2026-10
```

The root command verifies July's legacy run layout and checks the retained errata
chains for both editions. The October command verifies October's run bundles and
figures. Run both commands to check both reports; the root command alone does
not verify October's run bundles. In an exported kit, directing the verifier to
July's edition directory additionally checks that its mirror is byte-identical
to the root bundles. That mirror is created by export and is absent from the
private source checkout.

A FAIL produces a nonzero exit. By default, a SKIP remains unverified but can
still accompany exit 0. Require every check to run with strict mode:

```bash
python3 verify.py --require-no-skips
python3 verify.py editions/2026-10 --require-no-skips
```

A self-consistent errata chain cannot prove that its newest revisions were
included. Compare the current pins in [ERRATA.md](ERRATA.md) with values obtained
independently from the report index or site, then require those values with
`--expect`. These are the revision 2 handoff pins recorded in ERRATA.md:

```bash
python3 verify.py --require-no-skips --expect 2026-07=2:9981ab9e6e736a72e18f1318688a92e51f30cd687f13cacfb9d1eb768efebc42 --expect 2026-10=2:48974d598739785904d59938cbf4ba10e6a44af1aba2b8249145d6ed417e3e7f
python3 verify.py editions/2026-10 --require-no-skips --expect 2026-10=2:48974d598739785904d59938cbf4ba10e6a44af1aba2b8249145d6ed417e3e7f
```

A requested edition that was not checked, a different revision, or a different
manifest hash fails verification. `--expect` is repeatable. Pins copied only
from the same download establish consistency with that download, not freshness
or publisher authenticity.

## What reproduction means

Here reproduction means **re-verification of shipped evidence**: checking the
recorded accounting and figures, and optionally re-executing the published
acceptance tests on archived submissions. It does not mean re-executing models
to obtain the same code, tokens, cost, or outcome. The kit cannot recreate the
original vendor service state, and the historical runtime checkout was not
fully captured.

Verification establishes consistency with the retained evidence. It does not
independently establish capture completeness, judge validity, unseen-task
performance, or production quality. The reports' dated corrections describe
these limits in detail.

## Re-judge safely

**Rejudging executes model-written code. A bare-host `--rejudge` runs that code
with your account and network access. Temporary copies and a reduced environment
are not an operating-system sandbox. Only the Docker launcher below provides
network isolation.**

Use the exported kit containing both editions, with Docker running and the
exact digest-pinned Python image in `rejudge-networkless.sh` already available
locally. Preparing that image may need network access. The launcher never pulls
an image. Run from the exported kit as an account that can read it; exports may
have a root directory mode of `0700`, and the container uses your numeric user
and group IDs.

```bash
bash rejudge-networkless.sh
```

The launcher disables external networking with `--network none`, mounts the kit
read-only, uses a read-only root filesystem, drops capabilities, prevents new
privileges, and limits processes, memory, and writable temporary storage. Only
container loopback remains available for the integration judge's local fixture
services. No vendor CLIs, secrets, or private repositories are required.

Inside the container, `rejudge_workflow.py` uses Python's `-B` option and runs
ordinary verification and rejudging with `--require-no-skips` for each edition.
It requires these complete matching-verdict summaries:

```text
RE-JUDGE 63 bundles · PASS 63 · FAIL 0 · SKIP 0
RE-JUDGE 30 bundles · PASS 30 · FAIL 0 · SKIP 0
```

These are 93 archived outcomes, counting July's byte-checked mirror once.
A rejudge PASS means the new verdict matches the recorded verdict, including
a recorded rejection; July includes 60 accepted and 3 rejected submissions.
Missing required artifacts or unavailable judges produce SKIP. Strict mode
rejects every SKIP; a zero exit from a non-strict command alone is insufficient.

The workflow also copies an archived passing submission and flips only its
recorded verdict. It requires exit 1 and the explicit disagreement
`recorded FAIL, re-judged PASS`. An environment error or a SKIP cannot satisfy
this negative control. Published files are untouched. Exit 0 requires both
editions and the negative control to pass, ending with:

```text
REJUDGE WORKFLOW PASS: 93 outcomes; zero SKIPs; flipped verdict detected.
```

[REJUDGE-RECEIPT.json](REJUDGE-RECEIPT.json) records dated maintainer measurements
from an exported kit on a bare host, without OS network isolation; its
`docker_launcher_receipt` object records the Docker launcher measurement.
**No third-party clean-rejudge receipt has
been established.** Matching archived verdicts does not validate the judges.

## Understand the judge controls

Judge controls test whether the acceptance tests accept a reference solution
and reject deliberately wrong implementations. From the kit root:

```bash
python3 control/check-cronspec-selftest.py
python3 design/orgsync/controls/control_check.py
```

The cronspec control requires its reference to pass and its wrong variants to
fail the intended rules. The orgsync control checks the reference and the wrong,
empty, skeleton, and no-output cases against their expected verdicts. Both
commands are offline and use disposable copies; the integration control needs
local loopback services. They run shipped control implementations, not model
invocations.

These controls answer a different question from the flipped-verdict control:
they exercise the judges' intended distinctions, while the flipped verdict
checks that rejudging detects a disagreement with a recorded result. Neither
proves the task specification complete or establishes production quality.

## Inspect harness lineage

[HARNESS-LINEAGE.json](HARNESS-LINEAGE.json) maps published run bundles and cited
comparison revisions to screened, content-addressed source in `harness/blobs/`.
Each revision records paths, published blob IDs and hashes, transformations,
and dependencies absent at that revision. The files are inspection evidence,
not a complete replay environment.

Follow [harness/README.md](harness/README.md) for hash-checked comparisons using
only shipped blobs. Private commit IDs are provenance identifiers; those Git
objects need not be available publicly. Legacy stamps did not capture harness
commits. October attribution follows the report's harness-head record; July
uses the last committed runner before each stamp time, which does not establish
the exact runtime working tree. Transformed copies document their changes and
do not expose the private originals' content addresses.

## Find errata

[ERRATA.md](ERRATA.md) explains dated corrections, current handoff pins, and
verification limits. Published reports, original manifests, metrics, and run
bundles retain their bytes. Corrections are append-only revision sidecars:
`editions/<ed>/ERRATA.v<N>.json` and `editions/<ed>/MANIFEST.v<N>.json`.
The highest contiguous retained revision is current. Each successor manifest
binds its predecessor and errata by hash; the original manifest remains the
authority for runs and figures. Read the sidecars with the report, and obtain
the current tip pin independently when freshness matters.

## Maintainers

Public readers can complete the workflows above using only the exported kit.
The following material concerns kit maintenance and acceptance testing.

### Export and private source

Build public deliverables through `export.py` and `.export-exclude` from the
private source checkout. The exporter's `--edition all` with `--out` builds a
local kit containing both editions; `--dry-run` validates the complete payload
without writing. The output directory must be empty. Neither operation publishes
or pushes. Use the exporter's help for its argument syntax.

Supply `EXPORT_IDENTITY_MAP` through a private environment or secret store
before export or lineage regeneration: a nonempty JSON object mapping reviewed
private identity strings and checkout phrases to public replacements. Never
put the real mapping in tracked or exported files. Missing, empty, or malformed
mappings refuse sanitization. Controls supply synthetic mappings.

The exporter selects an explicit allowlist, validates errata chains, and checks
paths and staged bytes against the sealed-content and identity boundaries.
`ARCHIVE-EXPORT.jsonl` enumerates reviewed archive copies and transformations;
new files do not ship automatically. Private originals and private Git history
remain private. Lineage regeneration through `harness_lineage.py` also uses only
the reviewed boundary; public kits inspect the checked-in artifacts.

### Exported controls

**This acceptance recipe includes rejudging and executes model-written programs
with the host account and network if run on a bare host.** Use an isolated Linux
environment for submissions. `control/exported_controls.py` requires a real
exported kit without private Git history, Python 3, Bash, Git, and standard Unix
tools. It prepares disposable runner fixtures and a disposable kit, scrubs the
caller environment, and supplies vendor stubs that record and refuse invocation.
No paid model calls are made. Run `python3 control/exported_controls.py`
inside that isolated environment to execute the recipe.

The pinned recipe below is executed by that acceptance script in its fixtures.
The fixture labels select archived rate bytes; the batch fixture aligns its
rate pin to exercise the fresh roster. Run these dry paths through the acceptance
script's fixtures: the closed edition's root prices intentionally differ from
some run pins. The acceptance check requires command displays, a complete fresh
dry batch, and zero vendor invocations. Published pins and evidence stay intact.

<!-- exported-controls:start -->
```bash
# Fixture: v11
OPENCODE_PROVIDER=ollama-cloud EDITION=2026-10 ALLOW_EXPENSIVE=1 DRY_RUN=1 ./run.sh opencode glm-5.3 orgsync 1
# Fixture: v11
EDITION=2026-10 ALLOW_EXPENSIVE=1 DRY_RUN=1 ./run.sh opencode glm-5.3 orgsync 1
# Fixture: v11
EDITION=2026-10 ALLOW_EXPENSIVE=1 DRY_RUN=1 ./run.sh muse muse-spark-1.3 orgsync 1
# Fixture: v11
EDITION=2026-10 ALLOW_EXPENSIVE=1 DRY_RUN=1 ./run.sh claude-code haiku orgsync 1
# Fixture: v11
EDITION=2026-10 ALLOW_EXPENSIVE=1 DRY_RUN=1 ./run.sh codex gpt-6-luna orgsync 1
# Fixture: v13
EDITION=2026-10 ALLOW_EXPENSIVE=1 DRY_RUN=1 ./run.sh opencode glm-5.3-flash orgsync 1
# Fixture: v13
DRY_RUN=1 ./preflight.sh
# Fixture: batch
EDITION=2026-10 ALLOW_EXPENSIVE=1 DRY_RUN=1 ./batch.sh
# Fixture: v11
EDITION=2026-07 ALLOW_EXPENSIVE=1 DRY_RUN=1 ./run.sh codex gpt-5.6-luna baseline 1
```

```bash
python3 -m unittest discover -s control -p 'test*.py' -v
bash check-calibration-gate.sh
python3 control/check-cronspec-selftest.py
python3 design/orgsync/controls/control_check.py
python3 verify.py
python3 verify.py editions/2026-10
python3 verify.py --rejudge
python3 verify.py editions/2026-10 --rejudge
python3 verify.py --require-no-skips  # fail on any SKIP; also works with --rejudge
python3 verify.py --rejudge --require-no-skips  # all-submissions rejudge receipt
python3 evidence.py --check
bash -n run.sh batch.sh preflight.sh edition.sh check-calibration-gate.sh
```
<!-- exported-controls:end -->

The control suite also resolves documented repository paths against the kit
allowlist, tests sealed-content and identity refusals with synthetic inputs,
and checks the shipped lineage. Controls that need private historical Git
objects check freshness only when those revisions resolve.
