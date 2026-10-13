# The TIER Model Report — Edition 2

**2026-10 · ten models · one task · 30 runs · $17.8206 at list rates · 30 accepted / 0 not**

*One task — the integration anchor only; per-task results, not a model ranking.*

---

## The finding

**Ten models were each handed the same half-built program and asked to finish
it, three times each. All 30 attempts finished it well enough to pass the
task's hidden test suite (the judge). At list prices, the average cost of one
accepted outcome ran from $0.0062 (`gpt-6-luna`) to $3.1065
(`claude-fable-5-1`), a factor of 499 (3.106531 ÷ 0.006223 = 499.20, using the
six-decimal per-run costs), on this one task.**

Operator ruling R-2026-10-12-16, scope D (integration anchor only, per-task results),
limits this edition to the scope stated above. One task cannot tell you which
model is better at software work in general, and three runs per model is still
a small sample. So nothing below names a best model or a winner. On this
integration task, where every model passed every run, the price of accepted
work differed by more than two orders of magnitude, and the spread between
models was far larger than the spread between any one model's own runs.

One thing has changed since Edition 1 on this same task. `claude-haiku-4-5`
failed `orgsync` on all three of its Edition 1 runs. Its successor in name,
`claude-haiku-5-5`, passed all three of its runs here, including the idempotency
check, which every run of the earlier model failed. The two editions now
have the same sample size per model. They are still different models priced on
different rate cards, so the comparison below shows which way costs moved,
without measuring how much of the change comes from the model.

---

## Terms used

**Anchor Set** — frozen, graduated tasks re-run to track cost and catch regressions.

**billed tokens** — uncached input, cache read, cache write and output tokens, counted each time they are billed; reasoning is included in output.

**cache read / cache write** — input tokens read from an existing cache / input tokens written to a cache, priced at the table's respective rates.

**dispersion** — the sample standard deviation of a model's run costs divided by their mean, expressed as a percentage.

**effort** — a vendor-specific model setting; here each CLI used its shipped default, recorded only where session metadata made it observable.

**floor models** — designated starting models in the calibration ladder; non-floor models enter a new task only after a floor fails.

**idempotency** — running the job again must not post work already completed.

**integration anchor** — a graduated integration task, re-run as a cost reference and regression check.

**prompt/judge sha256** — file fingerprints identifying the exact prompt and acceptance suite recorded for a run.

**regression tripwire** — an anchor task whose repeated failure by a newly released model is reported as a regression; one failing run alone is not enough.

**saturation** — all but at most one roster model passes every one of its runs, triggering graduation of an active task outside the EASY tier.

**stamp** — a run's metadata record, written after judging, with task and price hashes and recorded environment facts.

**validity residual** — a remaining gap in the harness's checks; here it cannot detect a shell command reaching the internet.

**wall time** — elapsed run time recorded by the harness; environment-dependent and never used to rank models.

---

## How to read the tables

    TIER = points / (cost / $1,000)      HIGHER is better  ↑
    E[$/accepted] = cost / accepted       LOWER is better  ↓

**The difficulty → weight mapping is a protocol choice** (PROTOCOL
§7a, locked). TIER's weight scale is defined over merged pull requests, and a
benchmark task is not one. This mapping is fixed across editions and is printed
in every report that publishes a TIER score:

| task difficulty | weight | scale point |
|---|---|---|
| easy | **1.0** | s |
| medium | **3.0** | m |
| integration | **5.0** | l |

For each model, cost is the sum of all three runs' costs, including any that
failed (none did). E[$/accepted] is that sum divided by the number of accepted
runs. TIER is 5.0 × accepted runs ÷ (that sum ÷ $1,000); with three accepted runs
it is 15,000 ÷ the three-run dollar total. **Run-to-run range** is the cheapest
to the dearest of the three run costs. **Dispersion** follows the sample convention
(N−1 denominator) of the published Edition 1 report; `aggregate.py` currently uses
population variance, which would give different figures.

**NO SCORE.** A cell with no accepted run has no cost-efficiency figure,
because there is nothing to divide the cost by. No cell in this
edition is NO SCORE. The rule is printed because Edition 1 had one, and the
comparison below uses it.

---

## Results

### INTEGRATION — `orgsync` (weight 5.0)
Complete a partially built tool inside an existing codebase by reconciling two
services that disagree, under pagination, rate limits and error paths. This is
the same frozen task as Edition 1 (`orgsync/v1`; prompt sha256
`94950b00fd82cc86…`, judge sha256 `b948f904bfd72ffc…`, both recorded in every
run's stamp and in `MANIFEST.json`).

Rows follow the manifest's roster order. Dollars are from the report rate table, `prices.yaml` v13 (see Method).

<!-- table:results -->
| model | outcome | rep1 $ | rep2 $ | rep3 $ | TIER ↑ | E[$/accepted] ↓ | run-to-run range | dispersion |
|---|---|---:|---:|---:|---:|---:|---|---:|
| claude-fable-5-1 | 3/3 | 3.316186 | 2.496857 | 3.506550 | 1,610 | $3.1065 | $2.4969 – $3.5065 | 17.3% |
| claude-opus-5-5 | 3/3 | 0.720649 | 0.741420 | 0.697352 | 6,946 | $0.7198 | $0.6974 – $0.7414 | 3.1% |
| claude-sonnet-5-5 | 3/3 | 0.129601 | 0.138034 | 0.140551 | 36,748 | $0.1361 | $0.1296 – $0.1406 | 4.2% |
| claude-haiku-5-5 | 3/3 | 0.055716 | 0.049090 | 0.055868 | 93,357 | $0.0536 | $0.0491 – $0.0559 | 7.2% |
| gpt-6.1-sol | 3/3 | 0.128823 | 0.096269 | 0.107162 | 45,146 | $0.1108 | $0.0963 – $0.1288 | 15.0% |
| gpt-6-astra | 3/3 | 0.647136 | 0.573424 | 0.640904 | 8,058 | $0.6205 | $0.5734 – $0.6471 | 6.6% |
| gpt-6-luna | 3/3 | 0.005726 | 0.006519 | 0.006424 | 803,471 | $0.0062 | $0.0057 – $0.0065 | 7.0% |
| glm-5.3 | 3/3 | 0.569868 | 0.434633 | 0.796924 | 8,327 | $0.6005 | $0.4346 – $0.7969 | 30.5% |
| glm-5.3-flash | 3/3 | 0.067828 | 0.191741 | 0.149752 | 36,646 | $0.1364 | $0.0678 – $0.1917 | 46.2% |
| muse-spark-1.3 | 3/3 | 0.486373 | 0.437507 | 0.425737 | 11,114 | $0.4499 | $0.4257 – $0.4864 | 7.1% |
<!-- /table:results -->

*Effort scales differ by vendor; each model ran at its CLI's shipped default
(stamped). Claude cells record effort from session metadata, identically on all
three runs: `claude-fable-5-1` **high**; `claude-opus-5-5`, `claude-sonnet-5-5`,
`claude-haiku-5-5` **medium**. Codex, OpenCode and Muse cells: **not observable**
(no effort field in their session metadata or requests). See Method.*

The original nine models ran three times under R-2026-10-12-24, which required
three runs for every original panel model and superseded R-2026-10-12-21.
That earlier ruling specified one run per model, later superseded. GLM 5.3
Flash joined as the tenth model for three runs under R-2026-10-13-1 (admit
Flash after the original panel completed); its late admission is disclosed
under Method.

**Spread 499.20× across the ten models that scored**: dearest E[$/accepted] ÷
cheapest, from the six-decimal per-run costs (3.106531 ÷ 0.006223). On this one
task.

The run-to-run spread is small beside that. 8 of the 10 models’ three runs
stayed within 1.5× of each other, cheapest to dearest. The widest was
`glm-5.3-flash` at 46.2% dispersion: its dearest run cost
2.83 times its cheapest (0.191741 ÷ 0.067828).
The narrowest was `claude-opus-5-5` at 3.1%.

Verdict: **No model failed a run.** `orgsync` graduated to the Anchor Set in
Edition 1. An anchor is no longer a frontier test: it is re-run each edition as a
cost reference and a regression tripwire, and a current model failing it would be
a regression worth reporting. Ten of ten models passing every run also meets the
saturation arithmetic for this ten-model panel (≥9 of 10 pass every run,
PROTOCOL §3.3).

**What happens next.** All ten models passed every run of `orgsync`, the
integration task. On this task the current models no longer separate on whether
they finish the work, only on what it costs. The next edition, planned for
November 2026, therefore moves up a level: it adds a harder task above
`orgsync`, so the report again measures work that some models fail. `orgsync`
stays in every edition as the cost anchor, so prices can be compared over time.

The cost-per-accepted comparison chart uses the roster order and this task only.

<!-- figure:cost-per-accepted -->

Every run passed the judge's full suite: 12 tests, 34 checks, 0 failures, 0
errors (each bundle's `acceptance.log`).

---

## How each model worked

Each row puts the cost of accepted work next to the median and full observed
range of wall time, model calls / responses and billed tokens per run. These
describe the work on `orgsync/v1` separately; there is no overall score. A token
is counted each time it is billed, including a cache read; the count is not
a measure of unique text or work quality.

<!-- table:how-models-worked -->
| model | E[$/accepted] ↓ | wall seconds median (range) | model calls / responses median (range) | billed tokens median (range) |
|---|---:|---:|---:|---:|
| claude-fable-5-1 | $3.1065 | 468 (328 – 508) | 16 (9 – 18) | 722,989 (402,003 – 890,295) |
| claude-opus-5-5 | $0.7198 | 185 (179 – 189) | 10 (9 – 12) | 357,437 (333,632 – 436,750) |
| claude-sonnet-5-5 | $0.1361 | 46 (42 – 53) | 5 (4 – 6) | 119,483 (93,866 – 145,511) |
| claude-haiku-5-5 | $0.0536 | 264 (240 – 265) | 18 (15 – 19) | 1,154,388 (910,287 – 1,295,505) |
| gpt-6.1-sol | $0.1108 | 117 (76 – 132) | 9 (6 – 9) | 224,049 (139,918 – 225,501) |
| gpt-6-astra | $0.6205 | 120 (101 – 140) | 8 (7 – 9) | 196,960 (169,124 – 225,432) |
| gpt-6-luna | $0.0062 | 59 (54 – 65) | 10 (9 – 10) | 207,439 (188,391 – 215,053) |
| glm-5.3 | $0.6005 | 576 (483 – 585) | 26 (26 – 46) | 1,339,017 (908,681 – 2,255,372) |
| glm-5.3-flash | $0.1364 | 1,708 (1,071 – 1,741) | 48 (31 – 85) | 3,558,691 (1,319,460 – 5,105,787) |
| muse-spark-1.3 | $0.4499 | 252 (229 – 271) | 23 (17 – 25) | 1,486,926 (988,851 – 1,542,667) |
<!-- /table:how-models-worked -->

**Model calls / responses.** For Claude Code, OpenCode and Muse, use the
recorded `tokens.api_calls`, checked against usage records in the archived log.
Claude streaming entries are deduplicated by message ID. Codex records no
`api_calls`; count its session’s cumulative `token_count` records. Before
redaction, all nine Codex bundles were checked: every such record closed a
nonempty group of model response output items, with no trailing response group
and no adjacent repeated usage snapshots. The counts matched those response
groups (`CODEX-RESPONSE-AUDIT.json`). They are model responses, not a claim
about HTTP requests; a response may contain several text, reasoning or tool
items. The published column names that distinction, and the CSV records the
source for each run.

The model-calls comparison chart shows these counts per run in roster order.

<!-- figure:model-calls -->

The wall-time comparison chart shows the same runs. All runs used the same
MacBook (Apple M5 Max) with its normal background workload. Machine load was
neither controlled nor recorded, so wall time is environment-dependent and is
never used to rank models. The first 27 runs ran on 2026-10-12; the three Flash
runs ran on 2026-10-13. Runs 2–3 of the original panel also shared four tool
lanes, as disclosed under Method.

<!-- figure:wall-time -->

**Every run.** Wall time is the harness’s `result.json.wall_seconds` receipt;
usage is recalculated from the redacted session log. List cost uses the declared
v13 report table. `metrics.csv` has one row per run, including these fields and
the bundle path and call-count source. `verify.py` checks each CSV value and
generated table against the archived usage and harness receipts, so readers
can check that the published figures follow from the supplied evidence.
Unknown reasoning is printed as **not reported**, never as zero. Reasoning is included in output, never added again.

<!-- table:per-run -->
| model | rep | outcome | wall seconds | model calls / responses | uncached input | cache read | cache write | output (incl. reasoning) | of which reasoning | all tokens billed | list cost $ |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| claude-fable-5-1 | 1 | PASS | 468 | 16 | 482 | 621,944 | 62,409 | 38,154 | not reported | 722,989 | 3.316186 |
| claude-fable-5-1 | 2 | PASS | 328 | 9 | 258 | 321,906 | 52,605 | 27,234 | not reported | 402,003 | 2.496857 |
| claude-fable-5-1 | 3 | PASS | 508 | 18 | 546 | 786,319 | 62,233 | 41,197 | not reported | 890,295 | 3.506550 |
| claude-opus-5-5 | 1 | PASS | 179 | 10 | 20 | 301,227 | 38,623 | 17,567 | not reported | 357,437 | 0.720649 |
| claude-opus-5-5 | 2 | PASS | 189 | 12 | 24 | 380,098 | 38,938 | 17,690 | not reported | 436,750 | 0.741420 |
| claude-opus-5-5 | 3 | PASS | 185 | 9 | 18 | 279,898 | 36,085 | 17,631 | not reported | 333,632 | 0.697352 |
| claude-sonnet-5-5 | 1 | PASS | 42 | 4 | 8 | 71,410 | 17,006 | 5,442 | not reported | 93,866 | 0.129601 |
| claude-sonnet-5-5 | 2 | PASS | 46 | 5 | 10 | 95,756 | 18,122 | 5,595 | not reported | 119,483 | 0.138034 |
| claude-sonnet-5-5 | 3 | PASS | 53 | 6 | 12 | 123,454 | 15,378 | 6,667 | not reported | 145,511 | 0.140551 |
| claude-haiku-5-5 | 1 | PASS | 264 | 18 | 36 | 1,015,766 | 79,128 | 59,458 | not reported | 1,154,388 | 0.055716 |
| claude-haiku-5-5 | 2 | PASS | 240 | 15 | 30 | 783,486 | 73,777 | 52,994 | not reported | 910,287 | 0.049090 |
| claude-haiku-5-5 | 3 | PASS | 265 | 19 | 38 | 1,161,383 | 75,972 | 58,112 | not reported | 1,295,505 | 0.055868 |
| gpt-6.1-sol | 1 | PASS | 132 | 9 | 32,767 | 188,288 | 0 | 4,446 | 155 | 225,501 | 0.128823 |
| gpt-6.1-sol | 2 | PASS | 117 | 9 | 17,122 | 202,752 | 0 | 4,175 | 188 | 224,049 | 0.096269 |
| gpt-6.1-sol | 3 | PASS | 76 | 6 | 28,763 | 107,264 | 0 | 3,891 | 142 | 139,918 | 0.107162 |
| gpt-6-astra | 1 | PASS | 140 | 9 | 18,338 | 201,856 | 0 | 5,238 | 255 | 225,432 | 0.647136 |
| gpt-6-astra | 2 | PASS | 120 | 8 | 17,362 | 175,104 | 0 | 4,494 | 142 | 196,960 | 0.573424 |
| gpt-6-astra | 3 | PASS | 101 | 7 | 31,056 | 134,144 | 0 | 3,924 | 64 | 169,124 | 0.640904 |
| gpt-6-luna | 1 | PASS | 54 | 9 | 17,021 | 166,656 | 0 | 4,714 | 677 | 188,391 | 0.005726 |
| gpt-6-luna | 2 | PASS | 65 | 10 | 19,090 | 182,784 | 0 | 5,565 | 1,051 | 207,439 | 0.006519 |
| gpt-6-luna | 3 | PASS | 59 | 10 | 18,811 | 190,976 | 0 | 5,266 | 507 | 215,053 | 0.006424 |
| glm-5.3 | 1 | PASS | 585 | 26 | 65,562 | 1,237,952 | 0 | 35,503 | 23,723 | 1,339,017 | 0.569868 |
| glm-5.3 | 2 | PASS | 483 | 26 | 50,028 | 824,512 | 0 | 34,141 | 15,303 | 908,681 | 0.434633 |
| glm-5.3 | 3 | PASS | 576 | 46 | 68,768 | 2,154,688 | 0 | 31,916 | 16,784 | 2,255,372 | 0.796924 |
| glm-5.3-flash | 1 | PASS | 1071 | 31 | 66,914 | 1,209,536 | 0 | 43,010 | 28,783 | 1,319,460 | 0.067828 |
| glm-5.3-flash | 2 | PASS | 1741 | 85 | 98,297 | 4,950,528 | 0 | 56,962 | 26,680 | 5,105,787 | 0.191741 |
| glm-5.3-flash | 3 | PASS | 1708 | 48 | 102,042 | 3,391,232 | 0 | 65,417 | 37,570 | 3,558,691 | 0.149752 |
| muse-spark-1.3 | 1 | PASS | 229 | 23 | 89,812 | 1,356,982 | 0 | 40,132 | 26,560 | 1,486,926 | 0.486373 |
| muse-spark-1.3 | 2 | PASS | 271 | 25 | 75,600 | 1,437,080 | 0 | 29,987 | 17,751 | 1,542,667 | 0.437507 |
| muse-spark-1.3 | 3 | PASS | 252 | 17 | 88,459 | 856,464 | 0 | 43,928 | 31,204 | 988,851 | 0.425737 |
<!-- /table:per-run -->

---

## Where the money went

Token counts below are **sums over each model’s three runs**, from the same
shared evidence derivation as the per-run table and `metrics.csv`.

<!-- table:token-totals -->
| model | uncached input | cache read | cache write | output (incl. reasoning) | of which reasoning | all tokens billed |
|---|---:|---:|---:|---:|---:|---:|
| claude-fable-5-1 | 1,286 | 1,730,169 | 177,247 | 106,585 | not reported | 2,015,287 |
| claude-opus-5-5 | 62 | 961,223 | 113,646 | 52,888 | not reported | 1,127,819 |
| claude-sonnet-5-5 | 30 | 290,620 | 50,506 | 17,704 | not reported | 358,860 |
| claude-haiku-5-5 | 104 | 2,960,635 | 228,877 | 170,564 | not reported | 3,360,180 |
| gpt-6.1-sol | 78,652 | 498,304 | 0 | 12,512 | 485 | 589,468 |
| gpt-6-astra | 66,756 | 511,104 | 0 | 13,656 | 461 | 591,516 |
| gpt-6-luna | 54,922 | 540,416 | 0 | 15,545 | 2,235 | 610,883 |
| glm-5.3 | 184,358 | 4,217,152 | 0 | 101,560 | 55,810 | 4,503,070 |
| glm-5.3-flash | 267,253 | 9,551,296 | 0 | 165,389 | 93,033 | 9,983,938 |
| muse-spark-1.3 | 253,871 | 3,650,526 | 0 | 114,047 | 75,515 | 4,018,444 |
<!-- /table:token-totals -->

*Effort scales differ by vendor; each model ran at its CLI’s shipped default
(stamped).* “All tokens billed” is uncached input + cache read + cache write +
output. Reasoning is counted inside output for every tool, never added on top:
Codex and Muse report it as a subset of output, the OpenCode collector folds
GLM’s separately reported reasoning into output, and Claude Code reports no
separate reasoning count. The token comparison chart uses these components
per run; reasoning is a subset annotation, not an extra stacked component.

<!-- figure:tokens -->

**A run's list cost is each token type's billed count times its rate, summed.**
The taxi analogy helps with price and distance, but a token bill has several
rates. `claude-fable-5-1`'s list rates for
input and output are 100 times `gpt-6-luna`'s ($10 against $0.10 per million
input tokens; $50 against $0.50 per million output tokens), and its three runs
billed 3.30 times as many tokens (2,015,287 ÷ 610,883). Multiplying 100× rates
by 3.30× tokens gives about 330×; the remaining approximately 1.5× of the 499×
spread comes from token mix, chiefly Fable's one-hour cache writes (2× input) and
its larger output share, partly offset by its cheaper cache reads (0.025× input
against luna's 0.10×).

The analogy has limits: billed token counts reflect the model and its CLI tool
together, and this report does not separate the two. Re-reading context that
is already cached is billed at a fraction of the input rate, while writing to
the cache can cost more than plain input.

Fable 5.1's bill shows the cache-write cost: every Claude run wrote
to Anthropic's one-hour cache, which is priced at 2× the input rate. Across
Fable 5.1's three runs that is 177,247 tokens × $20 per million = $3.5449, or
38.0% of its three-run spend of $9.3196. None of the Codex, GLM or Muse runs
recorded any cache writes.

The two properties, cheap per token and sparing with tokens, are separate.
`claude-haiku-5-5` billed 3,360,180 tokens across its three runs, the
fourth-largest volume on the panel, and still came to $0.0536 per accepted
outcome, because its list rates are $0.10 in and $0.50 out per million.
`claude-sonnet-5-5` billed the fewest tokens of any model (358,860 across three
runs), finished each run in under a minute, and came to $0.1361 per accepted
outcome. A buyer who watches only the rate card misses the second property; one
who watches only token counts misses the first.

`glm-5.3-flash` billed 9,983,938 tokens across its
three accepted runs, with 164 model calls and no cache writes.
Its list rates are $0.15 in and $0.50 out per million, with cache reads at
$0.03 per million. Its cost per accepted outcome was $0.1364,
with a run-to-run range of $0.0678 – $0.1917. The bill is billed tokens
times each type's rate, summed. Call count only describes how many exchanges
happened. Neither ranks capability.

---

## Compared with Edition 1 (directional only)

PROTOCOL §3.3 computes longitudinal claims over **only the models present in
both editions**. No model ID appears in both rosters, so that panel is empty and
this edition makes **no longitudinal claim**. What follows pairs each Edition 1
model with the Edition 2 model of the same product-line name. That pairing is
ours, not a vendor statement, and a successor is a different model.

The sample sizes now match: both editions ran each model three times on
`orgsync`, so a range sits beside a range. Two other things could explain the
difference, which is why the comparison is directional only: the rate cards differ, and Edition 1
did not record effort.

| Edition 1 model | E1 outcome | E1 E[$/accepted] | E1 run-to-run range | E1 dispersion | E1 list in/out per M | Edition 2 model | E2 outcome | E2 E[$/accepted] | E2 run-to-run range | E2 dispersion | E2 list in/out per M |
|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-haiku-4-5 | **0/3** | **NO SCORE** | — | — | $1.00 / $5.00 | claude-haiku-5-5 | 3/3 | $0.0536 | $0.0491 – $0.0559 | 7.2% | $0.10 / $0.50 |
| claude-sonnet-5 | 3/3 | $1.9035 | $1.6603 – $2.1910 | 14.1% | $3.00 / $15.00 | claude-sonnet-5-5 | 3/3 | $0.1361 | $0.1296 – $0.1406 | 4.2% | $2.00 / $10.00 |
| claude-opus-4-8 | 3/3 | $3.9542 | $3.1607 – $4.4309 | 17.5% | $5.00 / $25.00 | claude-opus-5-5 | 3/3 | $0.7198 | $0.6974 – $0.7414 | 3.1% | $4.00 / $20.00 |
| claude-fable-5 | 3/3 | $3.4161 | $2.9127 – $4.1220 | 18.4% | $10.00 / $50.00 | claude-fable-5-1 | 3/3 | $3.1065 | $2.4969 – $3.5065 | 17.3% | $10.00 / $50.00 |
| gpt-5.6-luna | 3/3 | $0.1094 | $0.0989 – $0.1232 | 11.4% | $1.00 / $6.00 | gpt-6-luna | 3/3 | $0.0062 | $0.0057 – $0.0065 | 7.0% | $0.10 / $0.50 |
| gpt-5.6-sol | 3/3 | $0.5728 | $0.5625 – $0.5913 | 2.8% | $5.00 / $30.00 | gpt-6.1-sol | 3/3 | $0.1108 | $0.0963 – $0.1288 | 15.0% | $2.00 / $10.00 |
| gpt-5.6-terra | 3/3 | $0.2820 | $0.2480 – $0.3322 | 15.7% | $2.50 / $15.00 | *no same-name model in Edition 2* | — | — | — | — | — |
| *not in Edition 1* | — | — | — | — | — | gpt-6-astra | 3/3 | $0.6205 | $0.5734 – $0.6471 | 6.6% | $10.00 / $50.00 |
| *not in Edition 1* | — | — | — | — | — | glm-5.3 | 3/3 | $0.6005 | $0.4346 – $0.7969 | 30.5% | $1.40 / $4.40 |
| *not in Edition 1* | — | — | — | — | — | glm-5.3-flash | 3/3 | $0.1364 | $0.0678 – $0.1917 | 46.2% | $0.15 / $0.50 |
| *not in Edition 1* | — | — | — | — | — | muse-spark-1.3 | 3/3 | $0.4499 | $0.4257 – $0.4864 | 7.1% | $1.25 / $4.25 |

Sources: Edition 1 outcome, E[$/accepted], range and dispersion from
`editions/2026-07/REPORT.md` (`orgsync` table). Edition 1 list rates from the v8
rate snapshot in Edition 1's bundles (e.g.
`claude-code/opus/orgsync/rep1/prices.snapshot.yaml`). Edition 2 figures from the
table above; Edition 2 rates from `prices.yaml` v13. Cache-read multipliers also
changed for some pairs; the full Edition 2 rate card is under Method.

**Why one task cannot rank models: Edition 1 already showed the order changing
by task.** In Edition 1 the cheapest-to-dearest order per accepted outcome was
identical, model for model, on `duration` and `cronspec`, and inverted at the top
on `orgsync`: `claude-opus-4-8` became the dearest, passing `claude-fable-5`,
which had been dearest on both earlier tasks (`editions/2026-07/REPORT.md`, "The
finding"). A cost order measured on one task need not hold on another.

Three things can be read from this table, each with its limit attached.

**`claude-haiku-4-5` → `claude-haiku-5-5`: fail to pass, three runs each.** The
earlier model spent $1.5557 across its three `orgsync` runs (the sum of
`cost_list_usd` in `claude-code/haiku/orgsync/rep{1,2,3}/result.json`) and
produced no accepted outcome. All three failed the same assertion,
`TestA09Idempotent.test_second_run`: a second invocation must not re-post work
the first already completed. Its successor passed that assertion and the other
eleven tests on all three runs. Three of three is a better basis than one of one,
and it is still three.

**Four of the five pairs with an Edition 1 range came in entirely below it.**
Haiku has no Edition 1 range: it was NO SCORE. For Sonnet, Opus, Luna and Sol,
the Edition 2 model's dearest run was cheaper than the Edition 1 model's cheapest.
The exception is the Fable line: the ranges overlap ($2.4969 – $3.5065 against
$2.9127 – $4.1220), and each edition's mean lies inside the other's range. On
this evidence the Fable line's cost on this task did not move by more than its
own run-to-run variation.

**Lower list rates may explain part of the drop; how much has not been measured.**
List rates fell for five of the six pairs. `gpt-luna` output fell 12× ($6.00 →
$0.50 per million); input fell 10× for both `claude-haiku` and `gpt-luna`, and
`claude-haiku` output also fell 10×. Edition 1's own rate-table note applies: a
cross-edition dollar difference is "part model, part table" unless it is
re-derived under one rate table, and that re-derivation has not been done here.
Effort is a further confound: Edition 1 did not record effort, so its runs and
these may have used different defaults.

---

## Method

**Roster and task.** Ten models are declared in
`editions/2026-10/MANIFEST.json`: four through Claude Code
(`claude-fable-5-1`, `claude-opus-5-5`, `claude-sonnet-5-5`,
`claude-haiku-5-5`), three through Codex (`gpt-6.1-sol`, `gpt-6-astra`,
`gpt-6-luna`), two through OpenCode (`glm-5.3`, `glm-5.3-flash`) and one
through Muse (`muse-spark-1.3`). The original nine were fixed before any scored
run; Flash was admitted later under R-2026-10-13-1, as disclosed below. The
operator first raised the protocol’s older eight-model cap to nine (R-CAP A:
Edition 2 panel cap raised to nine) and then to ten for Edition 2 only (R-2026-10-13-1, PROTOCOL §3.3/§9).
Only `claude-haiku-5-5` and `gpt-6-luna` are designated floor models
(R-LADDER B: keep two floors; admit non-floors after failure;
R-2026-10-12-13: designated floors for the calibration ladder). Neither GLM
model nor `muse-spark-1.3` is a floor, whatever it costs on this task.

The edition is anchors-only: one task, `orgsync/v1`. Two other tasks were
deliberately left out: `cronspec/v2` was **not run in Edition 2**; its graduation
to the Anchor Set was recorded on 2026-10-13, after Edition 2's runs.
`duration/v1` was **not run under Edition 2's anchor-only scope**.

**Sample size, and when it was decided.** Edition 2 was first declared at one
run per model (R-2026-10-12-21). After every model's first run had passed, the
operator ruled for three (R-2026-10-12-24, first-hand): *"the report looks like
it needs run-to-run range dispersion. So I know I said before to only run it
once, but let's run it three times really quickly.. run them in parallel if you
can."* That ruling supersedes R-2026-10-12-21. Because it came after the first
runs' results were known, it is stated here: it applied to every model, whatever
its first result, and every valid run is reported, with none dropped. Two
voided attempts and the preflight probes are excluded and disclosed below.

**Environment.** All runs used the same MacBook (Apple M5 Max) with its normal
background workload. Machine load was neither controlled nor recorded; wall
time is environment-dependent and never used to rank models. The first 27
runs ran on 2026-10-12, and the three Flash runs on 2026-10-13. Every stamp
records `Darwin 25.6.0 arm64 / Apple M5 Max` and Python 3.14.8. CLI versions, as
stamped and identical across all three runs of each tool: Claude Code 2.1.296,
codex-cli 0.160.0, OpenCode 1.18.34, Muse Code 1.4.6. TIER instrument pin
`v0.1.0`, recorded in `TIER_PIN` and every stamp. A stamp is written after its
run has been judged, so stamp times mark the end of each run.

**Runs and harness heads.**

- **Run 1** ran one leg at a time. Stamps fall between 16:18:13Z
  (`claude-fable-5-1`) and 17:17:05Z (`muse-spark-1.3`). The four Claude runs ran
  at harness commit `14f21dc`; the three Codex runs, the OpenCode run and the Muse
  run at `ec9574f`. `14f21dc` was committed at 16:09:51Z, before the first Claude
  run began; `ec9574f` at 16:55:33Z, before the first Codex run. Between the two
  heads the Claude command line in `run.sh` did not change
  (`git diff 14f21dc ec9574f -- run.sh`). What changed for Claude was stamp
  metadata, which is why the four Claude run-1 stamps lack the `network_policy`
  and `trust_policy` fields every other stamp carries. The four Claude run-1
  cells were not re-run (R-2026-10-12-20: retain the four valid Claude first runs).
- **Runs 2 and 3** ran in four parallel lanes, one per tool (Claude Code, Codex,
  OpenCode, Muse). Within each lane the runs went one after another. Stamps fall
  between 19:03:09Z (`gpt-6.1-sol`, run 2) and 19:33:15Z (`claude-haiku-5-5`,
  run 3). Subtracting each run's wall time from its stamp puts the earliest start
  at about 19:01Z, and within every lane each run starts after the previous one's
  stamp. The checkout for these runs was `849c8cc` (committed
  18:55:19Z, before the first run started); during them only `verify.py`, its
  tests, `PROTOCOL.md` and this report were edited, uncommitted at first and then
  committed as `69e1843` at 19:15:39Z. The stamps do not record a harness commit.

  What can be checked is that `run.sh`, `batch.sh`, `edition.sh`, `score.py`,
  `token_evidence.py` and `run_audit.py` are byte-identical at `3a5c952`,
  `849c8cc` and `69e1843`.

**Flash late admission (R-2026-10-13-1).** GLM 5.3 Flash was added after the
original nine-model panel completed, before publication. All three runs are
reported; no other model was removed. They ran sequentially on 2026-10-13,
from about 07:45Z to the final stamp at 09:02:17Z, at harness head `66bd8ec`,
with the same frozen prompt and judge and a v13 run-table snapshot. Flash was
not part of the roster before the first 27 runs; this is an operator-ruled
exception, not a claim of a pre-declared ten-model panel. GLM 5.3 Highspeed was
not admitted. The original 27 v11 run pins remain intact.
Git comparison of `66bd8ec` and reviewed head `1ad7ea8`: `run.sh`, `score.py`,
`acceptance_orgsync.py`, `batch.sh`, `edition.sh`, `token_evidence.py`,
`run_audit.py`, `preflight.sh`, `preflight_check.py` and `preflight_http.py` are
byte-identical; `control/test_harness.py` changed only to add offline report-metrics tests.

Because runs 2 and 3 shared the machine with up to three other agents, their
wall times are not comparable with run 1's as a measure of speed.

**Prices.** Every dollar figure in this report's tables is list price, standard
tier, from one rate table for the whole panel: `prices.yaml` **version 13**,
sha256 `41000ebf0545b934…`, declared as the report rate table in `MANIFEST.json`;
`TIER_PIN` retains the v11 run-table pin. Every row cites its source: as of 2026-10-12
(R-2026-10-12-14: source URLs and dates for roster rates), except Flash, as of 2026-10-13 (R-2026-10-13-1):

| model | input $/M | cache read $/M | cache write $/M | output $/M | source (as of 2026-10-12) |
|---|---|---|---|---|---|
| claude-fable-5-1 | 10.00 | 0.25 | 20.00 (1-hour) | 50.00 | [platform.claude.com pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-opus-5-5 | 4.00 | 0.20 | 8.00 (1-hour) | 20.00 | [platform.claude.com pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-sonnet-5-5 | 2.00 | 0.10 | 4.00 (1-hour) | 10.00 | [platform.claude.com pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-haiku-5-5 | 0.10 | 0.01 | 0.20 (1-hour) | 0.50 | [Anthropic Haiku 5.5 announcement](https://www.anthropic.com/claude-haiku-5-5) |
| gpt-6.1-sol | 2.00 | 0.10 | none in table | 10.00 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |
| gpt-6-astra | 10.00 | 1.00 | none in table | 50.00 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |
| gpt-6-luna | 0.10 | 0.01 | none in table | 0.50 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |
| glm-5.3 | 1.40 | 0.26 | none in table | 4.40 | [Z.ai pricing](https://docs.z.ai/guides/overview/pricing) |
| glm-5.3-flash | 0.15 | 0.03 | none in table | 0.50 | [Z.ai pricing](https://docs.z.ai/guides/overview/pricing) (as of 2026-10-13) |
| muse-spark-1.3 | 1.25 | 0.15 | none in table | 4.25 | [Meta pricing](https://dev.meta.ai/docs/pricing-rate-limits) |

Cache-read prices are the table's input rate times its read multiplier.
Anthropic one-hour cache writes are 2× input; every Claude cache write in this
edition was a one-hour write. `claude-haiku-5-5` is $0.10/$0.50 for requests up
to 100K tokens and $0.50/$2.50 above, selected per request; its largest request
in any of its three runs was 90,388 tokens, so every request was priced at the
lower rate.

**The Sonnet 5.5 re-price (R-2026-10-12-25: correct Sonnet cache-read pricing after runs; carried by this edition's report
under R-2026-10-12-26: place report work in this PR).** The first 27 bundles carry the rate table they were scored
under: `prices.yaml` **version 11**, sha256 `b3aa945f75af6573…`. In v11 the Sonnet
5.5 row stated no cache-read multiplier, so cache reads took the Anthropic
default of 0.10×, $0.20 per million. Anthropic's pricing page, read 2026-10-12,
lists Sonnet 5.5 cache hits at $0.10 per million; its footnote 2 reads *"Cache
hits and refreshes on Claude Opus 5.5 and Claude Sonnet 5.5 are priced at 0.05x
the base input price"*. The operator ruled that the report table be corrected.
v12 changed that one row and nothing else; v13 retains that correction and
adds the Flash row. The correction was made after all 27
runs: the last stamp is 19:33:15Z, and the v12 commit `580ceb5` is 19:53:22Z.
PROTOCOL §4.3 says the edition's table is never altered after runs begin. This is
an exception to that rule, made by operator ruling before publication, and it is
disclosed here for that reason. Re-pricing changes no token count and no
outcome. It lowers Sonnet 5.5's three run costs:

| rep | v11 bundle snapshot $ | v13 report table $ | reduction $ |
|---|---:|---:|---:|
| rep1 | 0.136742 | 0.129601 | 0.007141 |
| rep2 | 0.147609 | 0.138034 | 0.009575 |
| rep3 | 0.152897 | 0.140551 | 0.012346 |

The original bundles’ v11 snapshots, recorded costs and stamps remain unchanged;
session logs are published in usage-only redacted form, with original hashes in
`EVIDENCE-MANIFEST.jsonl`. The first 27 recorded costs sum to $17.440365 at
v11. Those same runs cost $17.411303 at v13; the difference, $0.029062,
is exactly the three Sonnet reductions above. Flash’s three v13 bundles add
$0.409321. The 30-run report total is $17.820624
($17.8206 at list rates); all 30 recorded bundle costs, under their respective
v11/v13 pins, sum to $17.849686. Re-pricing changes no token count or outcome.
`verify.py` checks each bundle against its own snapshot and this report and
`metrics.csv` against v13, after checking its SHA-256 and version in the manifest.

Three further pricing disclosures:

- **OpenAI cache writes.** OpenAI's pricing page, fetched 2026-10-12, lists a
  cache-write price: $0.125 per million for `gpt-6-luna`, $12.50 for
  `gpt-6-astra`, $2.50 for `gpt-6.1-sol`. This edition prices OpenAI cache
  writes with no write multiplier (R-PRICE A: retain announced rates; remove
  OpenAI write multipliers; ruling Y3: remove OpenAI cache-write multipliers pending rate audit); adopting the listed
  write price would need its own rate audit. **All nine Codex runs recorded 0
  cache-write tokens**, so no Edition 2 dollar figure is affected.
- **`gpt-6.1-sol`** is priced at OpenAI's announced standard rate, $2/$10, with
  cached input at 0.05× ([model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol)).
  A third party has claimed that this rate is promotional. The claim is
  unverified. If that label is accurate and the standard rate is higher, Sol's
  cost here is understated.
- **Billing route.** Claude legs billed a Claude Code
  subscription, GLM a Z.ai coding-plan subscription, Muse a logged-in Muse
  account; Codex is stamped "credential-dependent". All are priced at per-token
  list rates. List price is a common yardstick, not a claim about anyone's bill.

**Price re-check.** On 2026-11-21 every roster rate is re-checked. If a list
price has changed, a dated price-change note will be published with dollars
re-derived from the unchanged token evidence. **The published table will not be
altered.**

**Effort (R-2026-10-12-11, using shipped defaults and recording observed effort;
R-EFFORT D: use shipped defaults under guarded CLI configurations).** Each
model ran at its CLI's shipped default; no effort flag was set, and an inherited effort setting would have
voided the run. Effort was recorded only where the session itself records it.
Claude Code's session logs carry an `effort` field: `high` for Fable 5.1,
`medium` for Opus 5.5, Sonnet 5.5 and Haiku 5.5, the same on all three runs.
Codex, OpenCode and Muse logs carry no effort field, so those six models' cells
read **not observable**. A documented default is never substituted for an
observation. Comparing defaults against high effort is a separate, later study,
scoped by a cost pilot first.

**Network posture.** Web-search and fetch tools were forbidden, and any use
would have voided the run. All 30 `result.json` files record
`web_tool_count: 0`. The task's two fixture services listen on 127.0.0.1, so
the Codex and Muse sandboxes had network access switched on to reach them
(`network_access=true` for Codex, `--sandbox-network enabled` for Muse). The
Claude Code and OpenCode legs run shell commands without an OS sandbox, so shell
network was available there too. ⚠️ **Validity residual:** a shell command that
reached the internet, rather than a web tool, would not be detected by the
harness.

**Configuration disclosures.**

- **(a) The four Claude run-1 stamps say `clean_config: true`, and for those runs
  that is wrong.** Every Claude leg ran in the operator's real home directory,
  because the Claude Code login was bound to the macOS keychain and could not
  be copied into a clean home directory in this setup. Customisation was disabled by flags:
  `--safe-mode --setting-sources '' --strict-mcp-config`, an empty MCP config, and
  Task/Agent tools denied. The stamps' own `auth_policy` and `config_policy`
  fields describe this correctly; in run 1 the `clean_config` flag was
  hard-coded. The harness was corrected after run 1, and the eight Claude stamps
  from runs 2 and 3 correctly say `clean_config: false`. The run-1 bundles were
  not edited.
- **(b) Muse** also ran in the operator's real home for the same keychain reason,
  and all three of its stamps correctly say `clean_config: false`. Its settings
  were audited against a key allow-list before each paid run. Muse has no
  documented way to switch off an inherited workspace-trust setting; that is a
  recorded residual.
- **(c) Codex and OpenCode** ran from fresh config directories with only
  credentials copied in (`clean_config: true`, correctly).
- **(d) Edition 1 did not record effort.** Vendors' defaults may have changed
  between editions, which is one more reason the cross-edition comparison is
  directional only.

**What was voided, and why.** Both voided attempts came before the valid run-1
legs. Neither is part of this edition's record, and neither is in its totals.

1. **The first paid attempt (2026-10-12)** failed on authentication: every
   Claude leg reported "Not logged in · Please run /login" and used zero tokens;
   Codex, OpenCode and Muse never started. The operator moved those bundles out
   of the tree.
2. **A `gpt-6.1-sol` run** was voided because Codex's `workspace-write` sandbox
   denied the localhost connections the task's fixture services need. This
   report treats that sandbox denial as a harness fault, not a model failure.
   The run was quarantined. The harness then enabled sandbox
   network for Codex and Muse and made an auth, capture and loopback preflight
   mandatory before any paid batch. The voided run's own capture was not priced;
   a second `gpt-6.1-sol` run, stopped while the fault was being diagnosed, cost
   $0.0130 at list, from the quarantined bundle outside the edition tree
   (not shipped). Neither figure is in the edition’s $17.8206.

Preflight probes spend a small amount on every tool and are kept separate from
the edition record.

---

## What this does not show

Read these findings with their limits.

1. **Model ranking.** One task, three runs per model. A different task could
   change the cost order, and Edition 1 showed it changing between tasks.

2. **Reliability.** Three runs each now give a range and a dispersion for every
   model, and all 30 runs passed. Three is still a small sample. If runs are
   independent and the failure rate is constant, a model that fails this task
   one time in ten would pass three runs in a row about 73% of the time
   (0.9³ = 0.729, an illustration, not a measurement), so three of three does
   not rule out an occasional failure. Likewise, a dispersion computed from
   three costs is a rough estimate, not a tight interval.

3. **Task discrimination.** Every model passed every run. As an anchor,
   `orgsync` is now a cost reference and a tripwire, and this time it did not
   trip. These results do not show the task separating models.

4. **Trends.** No model appears in both editions, rate cards changed, and effort
   was unrecorded in Edition 1. Equal sample sizes remove one confound; the
   others still prevent a trend claim.

5. **Prior exposure.** `orgsync`'s prompt and judge were revealed when it
   graduated on 2026-07-23. A model trained after that date could have seen
   them. We have no way to test whether any did.

6. **Speed.** Wall times come from the same MacBook (Apple M5 Max) with its
   normal background workload. Machine load was neither controlled nor
   recorded, vendor load is unknown, and runs 2–3 shared the machine with
   other agents. Wall time is environment-dependent and never used to rank.

7. **Code quality.** Accepted means the judge's 12 tests and 34 checks passed.
   Acceptance says nothing about code quality beyond those assertions.

8. **Effort.** Each CLI ran its own default; effort was not controlled across
   models. Effort is visible for the four Claude models and not observable
   for the other six, and effort scales are not comparable across vendors.

9. **Shell internet access.** The harness cannot detect an internet call made
   from a shell. See the network residual under Method.

---

## Stamp

| field | value |
|---|---|
| edition | 2026-10, Edition 2 |
| runs | **30** (10 models × 1 task × N=3) |
| total spend | **$17.8206** (list rates, report table `prices.yaml` v13; sum of 30 six-decimal run costs, $17.820624; recorded v11/v13 bundle costs sum to $17.849686) |
| outcomes | 30 accepted · 0 not accepted |
| task | `orgsync/v1` (integration, weight 5.0), anchor |
| prices table | report: v13, sha256 `41000ebf0545b934…` · bundle evidence: original 27 at v11 `b3aa945f75af6573…`, Flash 3 at v13 `41000ebf0545b934…` |
| tools | Claude Code 2.1.296 · codex-cli 0.160.0 · OpenCode 1.18.34 · Muse Code 1.4.6 |
| harness | run 1: `14f21dc` (Claude legs) · `ec9574f` (Codex, OpenCode, Muse legs) · runs 2–3: `849c8cc` (harness files identical to `3a5c952`) · Flash: `66bd8ec` |
| execution | run 1 one leg at a time; runs 2–3 in four parallel tool lanes, sequential within each lane; Flash sequential the following day |
| machine | same MacBook, normal background workload, load uncontrolled and unrecorded, Darwin 25.6.0 arm64, Apple M5 Max |
| date | 2026-10-12 (original 27) · 2026-10-13 (Flash 3) |
| protocol | v1.5 |

**Reproduce the verification:** `python3 verify.py editions/2026-10`, offline,
with no network and no keys. For each of the 30 bundles it checks the redacted
session log against its recorded hash, checks the judge hash against the judge
on disk, re-derives usage, and re-derives cost against that bundle’s pinned
v11 or v13 snapshot. For each cell it checks that E[$/accepted] follows
PROTOCOL §4.1.

It also checks the v13 report-table SHA-256 and version against `MANIFEST.json`,
re-derives every `metrics.csv` value (including wall time from the harness
receipt), and matches the generated results, per-run, token-total and “How each
model worked” tables. Missing, duplicate or changed CSV rows fail. It compares
headline run count, total spend, accepted/not-accepted counts, E[$/accepted] and
TIER. Regenerate the CSV and numeric tables with
`python3 edition_report.py editions/2026-10`. Charts are rendered by the website
from `metrics.csv` at the stable figure placeholders; this repository does not
draw them.

**Evidence preparation.** As in Edition 1 (PROTOCOL §8), `redact.py` publishes
only allowed session identities, usage counters, model IDs and timestamps,
with native tool identifiers retained for the existing web audit. Raw text,
reasoning, Muse `encrypted_content`, tool arguments and vendor system prompts
are excluded. `EVIDENCE-MANIFEST.jsonl` records each original and redacted log’s
hash; `evidence.py` stores the exact prompt, revealed judge and two price-table
versions by hash. Submissions, agent output, own-test and judge logs, fixture
artifacts, results, stamps and snapshots are kept, matching Edition 1’s committed
artifact types. Auxiliary home paths and result log paths are sanitized; numeric
results and stamped facts are unchanged. Edition 1 committed no embedded `.git`
metadata. Here the 30 original workspaces, including their `.git` directories,
remain in the gitignored `publish/edition2-raw/` archive; the committable bundles
contain ordinary files and no embedded repositories. No verifier evidence was
deleted. Credential/home-path scans apply to the files Git would commit.

The limits remain visible: a reader without an original cannot prove its
pre-redaction hash, and wall time rests on the harness receipt. Narrative
arithmetic, the directional Edition 1 comparison and the historical Sonnet
re-price disclosure are not all text-diffed by the verifier. As in Edition 1,
it checks the judge’s hash but does not re-run it against the submission, so
the accepted column still rests on the harness’s word. The task-ledger escrow
and timestamp-proof limitations in `TASKS.md` remain unresolved.
