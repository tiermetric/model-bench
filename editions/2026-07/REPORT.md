# The TIER Model Report — Edition 1

**2026-07 · seven models · three tasks · 63 runs · $53.4657 at list rates · 60 accepted / 3 not**

---

## The finding

**Cost per accepted outcome varied 24× on the easy task, 22× on the medium
one, and 36× on the integration task.** Six of seven models produced accepted work
on all three. One did not produce a single accepted outcome on the integration
task and therefore has **no score** for it — not a low score.

**The relative cost order changed on `orgsync`.** On `duration` and
`cronspec` the cheapest-to-dearest ordering was identical, model for model. On
`orgsync` it inverted at the top: `claude-opus-4-8` became the most expensive per
accepted outcome, passing `claude-fable-5`, which had been dearest on both
earlier tasks.

The judges test specific assertions. On these tasks, the dearer passing models
met the same judge assertions at up to 36 times the price.

## Terms used

**Anchor Set** — frozen tasks re-run to track cost and catch regressions.

**dispersion** — the sample standard deviation of run costs within a model–task cell, divided by their mean and expressed as a percentage. The sample calculation uses one fewer than the number of runs as its denominator.

**EASY-floor** — the easy task kept active at full replication to track cost and variation; it is exempt from saturation-based graduation.

**escrow** — a commitment to the next edition's frontier task and judge hashes before the current edition's results exist, with an independently anchored timestamp.

**flipped-verdict negative control** — a test that reverses a recorded verdict and checks that verification rejects the mismatch.

**judge** — the task's acceptance-test program.

**judge hash** — the judge file's SHA-256 fingerprint.

**panel consensus B** — the three-specialist decision panel's unanimous recommendation to publish Edition 1 after mechanical re-judgment and public-submission export fixes, disclosing the limitations that cannot be fixed.

---

## Known limitations

We publish Edition 1 under **R-2026-10-13-3 (publish after mechanical fixes, disclose unfixable limitations)**
(2026-10-13, panel consensus B),
with these limitations disclosed rather than claiming they can be repaired:

- **§5.6 escrow was not met.** We consumed our own escrow: `orgsync/v1` became
  this edition's task. Edition 2 has since run, so the required commitment
  before results cannot be satisfied retroactively (#5).
- **No unforgeable timestamp anchor exists for any task.** Our commitments
  rest on commit history alone, not an independently anchored timestamp proof.
- **`orgsync` rep1's judge hash was published 4m34s after its first verdict
  (#4).** The stamped hash matches the revealed judge; the ledger ordering
  guarantee was not met for that run.
- **The duration judge (`acceptance_test.py`) is public while duration remains
  an active EASY-floor task.** Its answer key can contaminate future runs.
- **The `cronspec/v2` judge became public without a recorded reveal event.**
  Graduation was recorded on 2026-10-13; we cannot backdate the missing reveal.

---

## How to read the tables

    TIER = points / (cost / $1,000)      HIGHER is better  ↑
    E[$/accepted] = cost / accepted       LOWER is better  ↓

**The difficulty → weight mapping is a protocol choice, not something derived from data** (PROTOCOL
§7a, locked). TIER's weight scale is defined over merged pull requests, and a
benchmark task is not one. This mapping is fixed across editions and is printed
in every report that publishes a TIER score:

| task difficulty | weight | scale point |
|---|---|---|
| easy | **1.0** | s |
| medium | **3.0** | m |
| integration | **5.0** | l |

**NO SCORE is not a low score.** With no accepted outcomes, cost per accepted
outcome is undefined. The cell is never rendered
as a large number, a zero, or a blank.

---

## Results

### EASY — `duration` (weight 1.0)
Parse and format human-readable duration strings against a stated spec.

| model | outcome | TIER ↑ | E[$/accepted] ↓ | run-to-run range | dispersion |
|---|---|---|---|---|---|
| gpt-5.6-luna | 3/3 | 33,140 | $0.0302 | $0.0292 – $0.0312 | 3.4% |
| gpt-5.6-terra | 3/3 | 13,838 | $0.0723 | $0.0638 – $0.0803 | 11.4% |
| gpt-5.6-sol | 3/3 | 6,272 | $0.1594 | $0.1461 – $0.1700 | 7.7% |
| claude-haiku-4-5 | 3/3 | 4,941 | $0.2024 | $0.1944 – $0.2097 | 3.8% |
| claude-sonnet-5 | 3/3 | 3,533 | $0.2831 | $0.2793 – $0.2906 | 2.3% |
| claude-opus-4-8 | 3/3 | 2,607 | $0.3836 | $0.3751 – $0.3942 | 2.5% |
| claude-fable-5 | 3/3 | 1,379 | $0.7253 | $0.6958 – $0.7433 | 3.6% |

**Spread 24.04×.** Verdict: **cost floor holding — does NOT graduate.** All seven
models passing is the designed outcome here, not a saturation signal: PROTOCOL
§3.3 exempts the EASY tier from the band precisely because everyone is *supposed*
to pass it. `duration` stays active at full N every edition, which is what keeps
the dispersion error bar on the one number this tier exists to publish.

### MEDIUM — `cronspec` (weight 3.0)
Parse and evaluate cron expressions against a stated spec, including edge cases.

| model | outcome | TIER ↑ | E[$/accepted] ↓ | run-to-run range | dispersion |
|---|---|---|---|---|---|
| gpt-5.6-luna | 3/3 | 33,353 | $0.0899 | $0.0811 – $0.0985 | 9.7% |
| gpt-5.6-terra | 3/3 | 22,175 | $0.1353 | $0.1234 – $0.1484 | 9.3% |
| gpt-5.6-sol | 3/3 | 12,530 | $0.2394 | $0.2316 – $0.2440 | 2.9% |
| claude-haiku-4-5 | 3/3 | 6,876 | $0.4363 | $0.3253 – $0.5640 | 27.6% |
| claude-sonnet-5 | 3/3 | 2,862 | $1.0482 | $0.9964 – $1.1106 | 5.5% |
| claude-opus-4-8 | 3/3 | 2,358 | $1.2723 | $1.1656 – $1.4406 | 11.6% |
| claude-fable-5 | 3/3 | 1,509 | $1.9877 | $1.8750 – $2.0758 | 5.2% |

**Spread 22.10×.** Verdict: **saturated** — all seven models passed every run, so
the §3.3 trigger is met. `cronspec/v2` **graduated to the Anchor Set on
2026-10-13**, recorded in the append-only Task Ledger. Its judge was already
public on tiermetric/model-bench without a recorded reveal event; we do not
backdate a §5.4 reveal.

### INTEGRATION — `orgsync` (weight 5.0)
Complete a partially built tool inside an existing codebase by reconciling two
services that disagree, under pagination, rate limits and error paths.

| model | outcome | TIER ↑ | E[$/accepted] ↓ | run-to-run range | dispersion |
|---|---|---|---|---|---|
| gpt-5.6-luna | 3/3 | 45,724 | $0.1094 | $0.0989 – $0.1232 | 11.4% |
| gpt-5.6-terra | 3/3 | 17,727 | $0.2820 | $0.2480 – $0.3322 | 15.7% |
| gpt-5.6-sol | 3/3 | 8,730 | $0.5728 | $0.5625 – $0.5913 | 2.8% |
| claude-haiku-4-5 | **0/3** | **NO SCORE** | **NO SCORE** | — | — |
| claude-sonnet-5 | 3/3 | 2,627 | $1.9035 | $1.6603 – $2.1910 | 14.1% |
| claude-opus-4-8 | 3/3 | 1,264 | $3.9542 | $3.1607 – $4.4309 | 17.5% |
| claude-fable-5 | 3/3 | 1,464 | $3.4161 | $2.9127 – $4.1220 | 18.4% |

**Spread 36.16× across the six models that scored.** Verdict: **saturated
(6 of 7 perfect)** — under §3.3 a task is saturated when all but at most one
roster model passes every one of its runs. `orgsync` graduated to the Anchor Set
on 2026-07-23 with its prompt, judge and design artifacts revealed, each
re-verified against the recorded SHA-256. The judge-hash publication ordering
exception for rep1 is disclosed above and in PROTOCOL §3.4.1.

`claude-haiku-4-5` failed all three replicates on the **same single assertion** —
`A9.file_summary` (`TestA09Idempotent.test_second_run`), the run-invariance check:
a second invocation must not re-post work the first already completed. Its own test
suite returned `OK` on all three runs. This **specific deficit repeated in every
recorded calibration run**: one model, one assertion, three times, while
everything the model wrote to check itself passed. It is also the reason this task was admitted to
service at all. See below.

---

<!-- section:how-models-worked -->
## How each model worked

This section was added on 2026-10-13, derived from the unchanged July bundles, and changes no published Edition 1 number.

**Recorded environment.** From the bundles’ `stamp.json` files only:

| field | recorded value |
|---|---|
| machine / OS / architecture | Darwin 25.5.0 arm64 / Apple M5 Max |
| CLI versions | 2.1.218 (Claude Code); codex-cli 0.145.0 |
| dates (UTC) | 2026-07-22; 2026-07-23 |
| machine load | not recorded |
| concurrency | not recorded |
| network conditions / vendor load | not recorded |
| effort settings | not recorded |

Wall time depends on the machine and network, is shown for context, and is never used to rank.

Each task is shown separately, in manifest roster order. Summaries give the median and full observed range per run on that task. E[$/accepted] retains the cost of failed runs; zero accepted outcomes mean NO SCORE. There is no combined or cross-task score.

For Claude Code, model calls / responses come from `tokens.api_calls`. The count is checked against the session’s usage records, with repeated message IDs counted once. For Codex, the count comes from cumulative `token_count` records, with adjacent identical usage snapshots counted once using the shared Edition 2 derivation. This measures model responses; it does not establish HTTP request counts. The CSV records the method for each run.

Wall time comes from each run’s `result.json.wall_seconds`. Token usage is recalculated from the archived session log, and list cost from that bundle’s pinned price snapshot. Billed tokens include each cache read. Output already includes reasoning; adding it separately would double-count it. Unknown reasoning is shown as not reported and left empty in `metrics.csv`, as in Edition 2. Bundle paths in the CSV are relative to the repository or archive root.

Six-decimal derived versus recorded list costs differ for `claude-code/opus/cronspec/rep1`: derived $1.165633, recorded $1.165634.

At this head, `python3 verify.py` reports **366 checks: 366 PASS · 0 FAIL · 0 SKIP** and `python3 verify.py --rejudge` reports **429 checks: 429 PASS · 0 FAIL · 0 SKIP**; these counts include the checks this section adds, and the earlier 342-check figure is the count before this section existed.

### `baseline` — duration/v1

<!-- table:how-models-worked:baseline -->
| model | E[$/accepted] ↓ | wall seconds median (range) | model calls / responses median (range) | billed tokens median (range) |
|---|---:|---:|---:|---:|
| claude-fable-5 | $0.7253 | 54 (46 – 54) | 4 (4 – 4) | 133,337 (131,711 – 133,937) |
| claude-haiku-4-5 | $0.2024 | 188 (173 – 195) | 10 (10 – 11) | 385,366 (374,250 – 427,896) |
| claude-opus-4-8 | $0.3836 | 63 (57 – 65) | 3 (3 – 4) | 103,820 (103,463 – 133,847) |
| claude-sonnet-5 | $0.2831 | 53 (50 – 56) | 4 (4 – 5) | 184,150 (183,963 – 226,401) |
| gpt-5.6-luna | $0.0302 | 54 (54 – 55) | 5 (5 – 5) | 74,817 (74,183 – 74,894) |
| gpt-5.6-sol | $0.1594 | 63 (52 – 64) | 5 (5 – 5) | 82,922 (80,697 – 84,313) |
| gpt-5.6-terra | $0.0723 | 52 (47 – 54) | 5 (4 – 5) | 80,694 (64,229 – 81,349) |
<!-- /table:how-models-worked:baseline -->

<!-- table:per-run:baseline -->
| model | rep | outcome | wall seconds | model calls / responses | uncached input | cache read | cache write | output (incl. reasoning) | of which reasoning | all tokens billed | list cost $ |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| claude-fable-5 | 1 | PASS | 46 | 4 | 8 | 108,729 | 18,725 | 4,249 | not reported | 131,711 | 0.695759 |
| claude-fable-5 | 2 | PASS | 54 | 4 | 8 | 109,599 | 19,429 | 4,901 | not reported | 133,937 | 0.743309 |
| claude-fable-5 | 3 | PASS | 54 | 4 | 8 | 109,142 | 19,388 | 4,799 | not reported | 133,337 | 0.736932 |
| claude-haiku-4-5 | 1 | PASS | 173 | 10 | 61 | 323,561 | 30,383 | 20,245 | not reported | 374,250 | 0.194408 |
| claude-haiku-4-5 | 2 | PASS | 195 | 10 | 60 | 332,294 | 31,770 | 21,242 | not reported | 385,366 | 0.203039 |
| claude-haiku-4-5 | 3 | PASS | 188 | 11 | 65 | 373,842 | 32,573 | 21,416 | not reported | 427,896 | 0.209675 |
| claude-opus-4-8 | 1 | PASS | 65 | 4 | 8 | 108,632 | 19,357 | 5,850 | not reported | 133,847 | 0.394176 |
| claude-opus-4-8 | 2 | PASS | 63 | 3 | 6 | 78,509 | 19,356 | 5,949 | not reported | 103,820 | 0.381570 |
| claude-opus-4-8 | 3 | PASS | 57 | 3 | 6 | 78,448 | 19,294 | 5,715 | not reported | 103,463 | 0.375069 |
| claude-sonnet-5 | 1 | PASS | 56 | 4 | 8 | 154,005 | 24,019 | 5,931 | not reported | 183,963 | 0.279305 |
| claude-sonnet-5 | 2 | PASS | 53 | 5 | 10 | 196,327 | 24,370 | 5,694 | not reported | 226,401 | 0.290558 |
| claude-sonnet-5 | 3 | PASS | 50 | 4 | 8 | 154,004 | 24,325 | 5,813 | not reported | 184,150 | 0.279370 |
| gpt-5.6-luna | 1 | PASS | 54 | 5 | 8,573 | 63,232 | 0 | 2,378 | 420 | 74,183 | 0.029164 |
| gpt-5.6-luna | 2 | PASS | 54 | 5 | 7,925 | 64,256 | 0 | 2,636 | 574 | 74,817 | 0.030167 |
| gpt-5.6-luna | 3 | PASS | 55 | 5 | 9,020 | 63,232 | 0 | 2,642 | 623 | 74,894 | 0.031195 |
| gpt-5.6-sol | 1 | PASS | 63 | 5 | 8,885 | 72,448 | 0 | 2,980 | 417 | 84,313 | 0.170049 |
| gpt-5.6-sol | 2 | PASS | 64 | 5 | 8,740 | 71,424 | 0 | 2,758 | 265 | 82,922 | 0.162152 |
| gpt-5.6-sol | 3 | PASS | 52 | 5 | 7,921 | 70,400 | 0 | 2,376 | 335 | 80,697 | 0.146085 |
| gpt-5.6-terra | 1 | PASS | 54 | 5 | 12,170 | 66,304 | 0 | 2,220 | 325 | 80,694 | 0.080301 |
| gpt-5.6-terra | 2 | PASS | 47 | 4 | 6,722 | 55,296 | 0 | 2,211 | 360 | 64,229 | 0.063794 |
| gpt-5.6-terra | 3 | PASS | 52 | 5 | 7,522 | 71,424 | 0 | 2,403 | 329 | 81,349 | 0.072706 |
<!-- /table:per-run:baseline -->

<!-- figure:cost-per-accepted:baseline -->

<!-- figure:model-calls:baseline -->

<!-- figure:wall-time:baseline -->

<!-- figure:tokens:baseline -->

### `cronspec` — cronspec/v2

<!-- table:how-models-worked:cronspec -->
| model | E[$/accepted] ↓ | wall seconds median (range) | model calls / responses median (range) | billed tokens median (range) |
|---|---:|---:|---:|---:|
| claude-fable-5 | $1.9877 | 217 (199 – 248) | 4 (4 – 6) | 211,817 (200,272 – 300,046) |
| claude-haiku-4-5 | $0.4363 | 364 (283 – 552) | 21 (17 – 24) | 1,269,499 (838,443 – 1,272,981) |
| claude-opus-4-8 | $1.2723 | 259 (234 – 279) | 10 (7 – 13) | 518,446 (351,036 – 712,249) |
| claude-sonnet-5 | $1.0482 | 311 (282 – 327) | 10 (10 – 10) | 751,630 (738,392 – 787,971) |
| gpt-5.6-luna | $0.0899 | 138 (130 – 147) | 12 (8 – 17) | 280,633 (167,640 – 350,354) |
| gpt-5.6-sol | $0.2394 | 87 (85 – 92) | 5 (5 – 6) | 102,327 (102,056 – 123,068) |
| gpt-5.6-terra | $0.1353 | 103 (100 – 105) | 6 (6 – 8) | 126,102 (124,386 – 173,152) |
<!-- /table:how-models-worked:cronspec -->

<!-- table:per-run:cronspec -->
| model | rep | outcome | wall seconds | model calls / responses | uncached input | cache read | cache write | output (incl. reasoning) | of which reasoning | all tokens billed | list cost $ |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| claude-fable-5 | 1 | PASS | 217 | 6 | 12 | 241,752 | 38,129 | 20,153 | not reported | 300,046 | 2.012102 |
| claude-fable-5 | 2 | PASS | 199 | 4 | 8 | 143,071 | 37,593 | 19,600 | not reported | 200,272 | 1.875011 |
| claude-fable-5 | 3 | PASS | 248 | 4 | 8 | 149,000 | 40,456 | 22,353 | not reported | 211,817 | 2.075850 |
| claude-haiku-4-5 | 1 | PASS | 283 | 17 | 95 | 759,459 | 48,385 | 30,504 | not reported | 838,443 | 0.325331 |
| claude-haiku-4-5 | 2 | PASS | 364 | 24 | 132 | 1,178,338 | 56,969 | 37,542 | not reported | 1,272,981 | 0.419614 |
| claude-haiku-4-5 | 3 | PASS | 552 | 21 | 116 | 1,137,632 | 69,541 | 62,210 | not reported | 1,269,499 | 0.564011 |
| claude-opus-4-8 | 1 | PASS | 259 | 7 | 14 | 284,077 | 43,340 | 23,605 | not reported | 351,036 | 1.165633 |
| claude-opus-4-8 | 2 | PASS | 234 | 10 | 19 | 453,722 | 42,260 | 22,445 | not reported | 518,446 | 1.210681 |
| claude-opus-4-8 | 3 | PASS | 279 | 13 | 25 | 640,158 | 45,415 | 26,651 | not reported | 712,249 | 1.440629 |
| claude-sonnet-5 | 1 | PASS | 327 | 10 | 20 | 690,560 | 61,940 | 35,451 | not reported | 787,971 | 1.110633 |
| claude-sonnet-5 | 2 | PASS | 311 | 10 | 20 | 660,145 | 59,185 | 32,280 | not reported | 751,630 | 1.037413 |
| claude-sonnet-5 | 3 | PASS | 282 | 10 | 20 | 651,527 | 55,753 | 31,092 | not reported | 738,392 | 0.996416 |
| gpt-5.6-luna | 1 | PASS | 147 | 17 | 25,236 | 318,208 | 0 | 6,910 | 1,700 | 350,354 | 0.098517 |
| gpt-5.6-luna | 2 | PASS | 138 | 12 | 23,893 | 249,856 | 0 | 6,884 | 1,776 | 280,633 | 0.090183 |
| gpt-5.6-luna | 3 | PASS | 130 | 8 | 27,858 | 133,120 | 0 | 6,662 | 1,618 | 167,640 | 0.081142 |
| gpt-5.6-sol | 1 | PASS | 85 | 5 | 13,541 | 84,736 | 0 | 4,050 | 427 | 102,327 | 0.231573 |
| gpt-5.6-sol | 2 | PASS | 87 | 6 | 14,122 | 104,960 | 0 | 3,986 | 289 | 123,068 | 0.242670 |
| gpt-5.6-sol | 3 | PASS | 92 | 5 | 13,926 | 83,712 | 0 | 4,418 | 434 | 102,056 | 0.244026 |
| gpt-5.6-terra | 1 | PASS | 103 | 6 | 12,516 | 107,520 | 0 | 4,350 | 634 | 124,386 | 0.123420 |
| gpt-5.6-terra | 2 | PASS | 105 | 6 | 13,124 | 108,032 | 0 | 4,946 | 1,067 | 126,102 | 0.134008 |
| gpt-5.6-terra | 3 | PASS | 100 | 8 | 14,659 | 153,600 | 0 | 4,893 | 774 | 173,152 | 0.148443 |
<!-- /table:per-run:cronspec -->

<!-- figure:cost-per-accepted:cronspec -->

<!-- figure:model-calls:cronspec -->

<!-- figure:wall-time:cronspec -->

<!-- figure:tokens:cronspec -->

### `orgsync` — orgsync/v1

<!-- table:how-models-worked:orgsync -->
| model | E[$/accepted] ↓ | wall seconds median (range) | model calls / responses median (range) | billed tokens median (range) |
|---|---:|---:|---:|---:|
| claude-fable-5 | $3.4161 | 369 (309 – 501) | 14 (12 – 16) | 733,006 (683,803 – 1,007,514) |
| claude-haiku-4-5 | NO SCORE | 253 (244 – 261) | 51 (50 – 59) | 3,134,962 (2,560,248 – 3,142,646) |
| claude-opus-4-8 | $3.9542 | 691 (594 – 796) | 45 (29 – 45) | 3,614,972 (2,102,751 – 3,691,113) |
| claude-sonnet-5 | $1.9035 | 479 (463 – 521) | 26 (26 – 30) | 2,263,275 (2,044,001 – 2,910,257) |
| gpt-5.6-luna | $0.1094 | 191 (179 – 195) | 11 (11 – 12) | 295,020 (285,385 – 315,568) |
| gpt-5.6-sol | $0.5728 | 172 (167 – 189) | 13 (12 – 13) | 364,671 (356,010 – 383,173) |
| gpt-5.6-terra | $0.2820 | 187 (135 – 190) | 11 (9 – 20) | 295,314 (227,449 – 547,314) |
<!-- /table:how-models-worked:orgsync -->

<!-- table:per-run:orgsync -->
| model | rep | outcome | wall seconds | model calls / responses | uncached input | cache read | cache write | output (incl. reasoning) | of which reasoning | all tokens billed | list cost $ |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| claude-fable-5 | 1 | PASS | 369 | 12 | 23 | 598,732 | 54,596 | 30,452 | not reported | 683,803 | 3.213482 |
| claude-fable-5 | 2 | PASS | 501 | 16 | 248 | 903,639 | 65,514 | 38,113 | not reported | 1,007,514 | 4.122049 |
| claude-fable-5 | 3 | PASS | 309 | 14 | 26 | 657,535 | 50,577 | 24,868 | not reported | 733,006 | 2.912735 |
| claude-haiku-4-5 | 1 | FAIL | 244 | 59 | 336 | 3,061,543 | 61,059 | 19,708 | not reported | 3,142,646 | 0.527148 |
| claude-haiku-4-5 | 2 | FAIL | 261 | 50 | 289 | 3,040,287 | 70,946 | 23,440 | not reported | 3,134,962 | 0.563410 |
| claude-haiku-4-5 | 3 | FAIL | 253 | 51 | 296 | 2,480,879 | 59,542 | 19,531 | not reported | 2,560,248 | 0.465123 |
| claude-opus-4-8 | 1 | PASS | 691 | 45 | 85 | 3,449,340 | 106,184 | 59,363 | not reported | 3,614,972 | 4.271010 |
| claude-opus-4-8 | 2 | PASS | 594 | 29 | 55 | 1,963,459 | 86,817 | 52,420 | not reported | 2,102,751 | 3.160675 |
| claude-opus-4-8 | 3 | PASS | 796 | 45 | 85 | 3,523,061 | 102,018 | 65,949 | not reported | 3,691,113 | 4.430861 |
| claude-sonnet-5 | 1 | PASS | 479 | 26 | 52 | 1,924,328 | 79,056 | 40,565 | not reported | 2,044,001 | 1.660265 |
| claude-sonnet-5 | 2 | PASS | 463 | 26 | 52 | 2,126,633 | 91,968 | 44,622 | not reported | 2,263,275 | 1.859284 |
| claude-sonnet-5 | 3 | PASS | 521 | 30 | 60 | 2,760,145 | 98,663 | 51,389 | not reported | 2,910,257 | 2.191037 |
| gpt-5.6-luna | 1 | PASS | 195 | 11 | 29,244 | 257,280 | 0 | 8,496 | 2,642 | 295,020 | 0.105948 |
| gpt-5.6-luna | 2 | PASS | 191 | 12 | 47,133 | 260,096 | 0 | 8,339 | 2,650 | 315,568 | 0.123177 |
| gpt-5.6-luna | 3 | PASS | 179 | 11 | 26,335 | 251,136 | 0 | 7,914 | 2,443 | 285,385 | 0.098933 |
| gpt-5.6-sol | 1 | PASS | 172 | 13 | 34,706 | 322,304 | 0 | 7,661 | 1,372 | 364,671 | 0.564512 |
| gpt-5.6-sol | 2 | PASS | 189 | 12 | 33,815 | 313,344 | 0 | 8,851 | 1,090 | 356,010 | 0.591277 |
| gpt-5.6-sol | 3 | PASS | 167 | 13 | 30,405 | 344,832 | 0 | 7,936 | 715 | 383,173 | 0.562521 |
| gpt-5.6-terra | 1 | PASS | 135 | 9 | 40,130 | 180,480 | 0 | 6,839 | 1,520 | 227,449 | 0.248030 |
| gpt-5.6-terra | 2 | PASS | 187 | 11 | 25,284 | 260,864 | 0 | 9,166 | 1,756 | 295,314 | 0.265916 |
| gpt-5.6-terra | 3 | PASS | 190 | 20 | 29,666 | 508,928 | 0 | 8,720 | 2,255 | 547,314 | 0.332197 |
<!-- /table:per-run:orgsync -->

<!-- figure:cost-per-accepted:orgsync -->

<!-- figure:model-calls:orgsync -->

<!-- figure:wall-time:orgsync -->

<!-- figure:tokens:orgsync -->

<!-- /section:how-models-worked -->

---

## What this edition does and does not show

Read these findings with their limits.

1. **`orgsync`.** It produced the only failing model–task combination in 63
   published runs: `claude-haiku-4-5`/`orgsync`. All three runs of that
   combination failed. The maintainers report that
   every other demonstrated failure in this repository came from a control arm
   written to fail. Control arms check the harness's behavior. This result shows
   the judge catching a real assertion violation in model-written work.

2. **The admission rule.** This edition does not show that the benchmark
   discriminates between models. Under §3.4 a task is admitted to service only
   when at least one model fails it at calibration, and
   `calibration/orgsync.cleared` records `claude-haiku-4-5`'s three failing runs
   — the same three runs, at the same costs, that §3.4.1 then promoted into this
   scored panel. The probability that this panel contained a failure was
   **1, by construction**. Selection and inference rest on the same observation.
   The failure is real and the numbers are right, but the inference "the
   benchmark separates strong models from weak ones" is *not available from
   it*. `orgsync` separated one model on one assertion in the recorded
   calibration runs. Six of seven models passed every recorded run, meeting
   the saturation rule.

3. **Reliability.** Three runs show all-fail or all-pass in this sample at N=3;
   `claude-haiku-4-5`/`orgsync` is an all-fail combination. They cannot establish
   long-run reliability or put a useful interval on an intermediate pass rate.
   No model in this edition had mixed results.

4. **Code quality.** Accepted means the judge's assertions passed. Acceptance
   says nothing about code quality beyond those assertions.

---

## What happens next

`orgsync` is an anchor: frozen, never edited, and re-priced every edition from
here as a longitudinal cost anchor and a regression tripwire. A newly released
model failing it is worth reporting only if it replicates.

**Edition 1 has no conformant escrowed successor.** We consumed our own
escrow, and Edition 2 has since run. §5.6 cannot be satisfied retroactively;
publication proceeds under R-2026-10-13-3 with this limitation disclosed (#5).

We do not know whether the next task will discriminate. If every model passes it
too, we would report that, because it tests the admission-rule limit directly.

---

## Method, in brief

- **Seven models**, roster fixed in `batch.sh` before the first run: three OpenAI
  (`gpt-5.6-luna`, `-terra`, `-sol`) and four Anthropic (`claude-haiku-4-5`,
  `-sonnet-5`, `-opus-4-8`, `-fable-5`).
- **Three tasks**, each with a precise specification; `orgsync` works inside an
  existing codebase.
- **N=3 per cell**, declared in advance. No optional stopping.
- **An independent judge per task**, its SHA-256 committed to the Task Ledger
  under commit-reveal. Every run's bundle records the judge hash it ran against.
- **Cost at published list rates**, from a pinned rate table — never estimated,
  never a subscription price divided by usage.
- **Every run result here re-derives** from `results.jsonl` plus the archived run
  bundles. Nothing is copied from a summary.

⚠️ **One commit-reveal exception, disclosed:** the `orgsync` judge's hash reached
the Task Ledger 4m34s *after* `claude-haiku-4-5`/`orgsync`/rep1's verdict was
known. The judge did not change — rep1's own stamp records the final judge hash
and the file still hashes to it — but for that one run a reader must trust our
stamp rather than the ledger's ordering. rep2 and rep3 met the hash-publication
ordering requirement. The full
timeline is in PROTOCOL §3.4.1.

---

## Stamp

| field | value |
|---|---|
| edition | 2026-07, Edition 1 |
| runs | **63** (7 models × 3 tasks × N=3) |
| total spend | **$53.4657** (list rates) |
| outcomes | 60 accepted · 3 not accepted (all `claude-haiku-4-5`/`orgsync`) |
| prices table | v8, sha256 `9ab9cba3fca5fef0…` |
| tools | Claude Code · Codex CLI |
| machine | single laptop, Darwin arm64 |
| verification | `verify.py --rejudge` — **342 checks: 342 PASS · 0 FAIL · 0 SKIP** (all 63 recorded verdicts reproduced) |
| protocol | v1.2 |

**Reproduce the verification:** `python3 verify.py` for the default offline pass;
`python3 verify.py --rejudge` also executes the revealed judges on disposable
submission copies. No model CLI or keys are needed.
**`--rejudge` executes 63 model-written programs on your machine.** Judges receive
only PATH, LANG, LC_ALL, TARGET_DIR, PYTHONDONTWRITEBYTECODE and a temporary HOME.
The verifier recalculates each run's cost from that bundle's own pinned rate snapshot,
checks every judge hash, and — as of model-bench#3 — **compares the tables in this
document against the bundles**: the run count, the total spend, the accepted/not
split, and every per-cell `E[$/accepted]` and TIER score. A mismatch in any of
these fields fails verification (mutation-tested against five planted edits).

An optional re-judge check added for model-bench#6 executes each revealed,
stamp-matching judge on a temporary copy and compares its machine-readable
PASS/FAIL verdict with the bundle's recorded outcome. A flipped-verdict negative
control must exit non-zero; deleting the comparison makes that test fail.
For `orgsync`, the original judge saw the run directory, while `submission/`
archived only root Python files. Under the operator's target ruling, we reconstruct
that layout from `submission/` plus the same bundle's archived `orgsync/`,
`config.json` and `tests/existing/`. Nothing is supplied from another bundle or
the live skeleton. Missing required archives cause the verifier to report SKIP
and list the missing paths.

Latest re-judge summary (from the executed verifier):

    RE-JUDGE 63 bundles · PASS 63 · FAIL 0 · SKIP 0
