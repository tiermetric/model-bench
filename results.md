# Cross-tool / cross-model TIER diagnostics — prompt: `baseline`

The same task (`prompts/baseline.md`, everything after the first `---`), run once
per model in a fresh git repo, judged by the same hidden acceptance suite
(`acceptance_test.py`, 19 tests covering only prompt-stated behavior), spend
captured from each tool's own session log and priced at LIST rates from TIER's
`internal/store/prices.yaml` (v7, effective 2026-07-13).

This is a demonstration of TIER's measurement, not a benchmark. **No winner
verdict is drawn.**

## Reproducibility stamp

Every published edition of this report carries this stamp; per-run copies are
written to `<tool>/<model>/<prompt>/stamp.json` by `run.sh`.

| field | value |
|---|---|
| TIER version | **v0.1.0** (public tag, tiermetric/tier) — private working tree `commit-6e8d80b` (no tags on the private repo; see gaps below) |
| prices.yaml | version **7**, effective 2026-07-13, sha256 `0acdc014936b…` |
| prompt | `prompts/baseline.md`, sha256 `6927e499c8ee…` |
| judge | `acceptance_test.py` (19 tests; control-tested pass/fail) |
| tools | claude 2.1.217 (Claude Code) · codex-cli 0.145.0 |
| models | claude-fable-5 · claude-opus-4-8 · claude-sonnet-5 · claude-haiku-4-5 · gpt-5.6-sol · gpt-5.6-terra · gpt-5.6-luna |
| date / machine | 2026-07-22 · Darwin 25.5.0 arm64 (single laptop) |

**Version-stamp gaps found while building this:** (a) `go install`-built tierd
prints `tierd dev` (ldflags not applied on that path — fix via
`debug.ReadBuildInfo`); (b) the private working tree has zero tags, so
`git describe` cannot name the TIER version that a local run used. Both should
land before the first *published* report cites "TIER vX".

**Flywheel:** this harness is re-run per TIER release, not only per model
launch — release → re-run reports → publish → announce. The stamp is what
makes each edition citable and comparable to the previous one.

## Caveats (required reading before quoting any number)

- **One task, N=1 per model** — this pass is exploratory ("see what we see").
  Any published version of this table requires **N=3 per model** with all runs
  reported (no cherry-picking), and this caveat block retained.
- Default tool configs, single machine (the operator's laptop), single day.
- Token *totals* are not comparable across tools as "efficiency" without the $
  column: tokenizers, system prompts, and caching behavior differ per tool.
- Claude reasoning tokens are billed inside output and not separately reported
  in the session JSONL (`n/a*`); Codex reports reasoning as a **subset** of
  output (verified: total = input + output). Never add reasoning on top.
- gpt-5.6-sol was run **interactively** by the operator (2026-07-22); its wall-clock
  (~9m48s log span) includes operator time and is not comparable to headless runs.
- Cache-read % = cache-read tokens ÷ total input-side tokens.

## Results

| tool | model | own | acc | edge | input(uncached) | cache-read | cache-write | output | reasoning | cache% | $ (list, std) | wall |
|---|---|:--:|:--:|:--:|--:|--:|--:|--:|--:|--:|--:|--:|
| codex | gpt-5.6-luna | PASS | PASS | 0f+0e | 12,259 | 45,056 | 0 | 2,661 | 628 | 78.6% | $0.0327 | 58s |
| codex | gpt-5.6-terra | PASS | PASS | 0f+0e | 7,444 | 55,296 | 0 | 2,436 | 638 | 88.1% | $0.0690 | 53s |
| claude-code | haiku | PASS | PASS | 0f+0e | 26 | 103,909 | 26,552 | 17,393 | n/a* | 79.6% | $0.1505 | 154s |
| codex | gpt-5.6-sol | PASS | PASS | 0f+0e | 9,942 | 71,424 | 0 | 2,239 | 358 | 87.8% | $0.1526 | 9m48s‡ |
| claude-code | sonnet | PASS | PASS | 0f+0e | 10 | 192,357 | 23,103 | 4,469 | n/a* | 89.3% | $0.2634 | 44s |
| claude-code | opus | PASS | PASS | 0f+0e | 8 | 105,520 | 18,208 | 4,374 | n/a* | 85.3% | $0.3442 | 51s |
| claude-code | fable | PASS | PASS | 0f+0e | 6 | 78,129 | 19,776 | 4,328 | n/a* | 79.8% | $0.6901 | 45s |

† **RESOLVED in prices.yaml v8 (#461, merged):** the three gpt-5.6 rows were added (sol $5/$30, terra $2.50/$15, luna $1/$6, OpenAI standard-tier list); all Codex legs now price natively. Original finding for the record — `prices.yaml` v7 has **no
rows for `gpt-5.6-sol`, `gpt-5.6-terra`, or `gpt-5.6-luna`** (verified
2026-07-22 — zero `gpt-5.6` matches; nearest entries are `gpt-5.3-codex`
$1.75/$14 and `gpt-5.1-codex` $1.25/$10, cache_read_mult 0.10). TIER cannot
price a model it has no row for — in tierd these would fall through to the
`self-hosted-medium` guessed fallback with a WARN. The fix is adding audited
rows (needs a `version` bump, v8); do **not** substitute a 5.3/5.1 rate as if
it were 5.6 pricing. All four Claude model IDs ARE priced (`claude-fable-5`
$10/$50, `claude-opus-4-8` $5/$25, `claude-sonnet-5` $3/$15 list,
`claude-haiku-4-5` $1/$5; anthropic cache: read 0.10×, write 5m 1.25× / 1h 2×).
Once the v8 rows land, re-price the codex rows with:
`python3 score.py --format codex --log <rollout.jsonl> --model-key gpt-5.6-<x> ...`
(drop `--allow-unpriced`).

‡ Interactive session — not comparable to headless wall-clocks.

*Sol formula for when a rate exists:*
`$ = (81,366−71,424)·in + 71,424·in·0.10 + 2,239·out` (per-token rates in $/M ÷ 10⁶).

## How each row is produced

```
./run.sh codex gpt-5.6-terra        # fresh repo at codex/gpt-5.6-terra/baseline/
./run.sh codex gpt-5.6-luna
./run.sh claude-code fable          # fresh repo at claude-code/fable/baseline/
./run.sh claude-code opus
./run.sh claude-code sonnet
./run.sh claude-code haiku
```

Each run prints its markdown row (paste it into the table above) and writes
`<tool>/<model>/baseline/result.json` with the full breakdown. Honesty rules
baked into `run.sh`/`score.py`: fresh repo enforced; control arm asserts no
session log before and exactly one after, and spend > 0; the acceptance suite
was itself control-tested (passes sol's real output 19/19; fails a
deliberately broken implementation with 237 subtest failures).

## Extending the harness (recurring model-efficiency report)

- **New model:** `./run.sh <tool> <model>` — a new dir and a new row.
- **Harder task:** add `prompts/<name>.md` (task after the first `---`), then
  `./run.sh <tool> <model> <name>` — runs land in `<tool>/<model>/<name>/`.
- **Re-run (N=3):** move the old run dir aside first; `run.sh` refuses to
  overwrite an existing run.
- **New tool:** add a case to `run.sh` (model flag mapping + session-log
  discovery + a `score.py --format` parser for its log shape).
