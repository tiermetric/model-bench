# The TIER Model Report — measurement harness and Edition 1 evidence

A neutral, reproducible measurement of what AI-assisted coding work **costs per
accepted outcome**, published by [Intenteon](https://intenteon.com), which
operates it for its own model-routing decisions.

- **`PROTOCOL.md`** — the standard. Published before the results it governs.
- **`MASTER-RESULTS.md`** — every run, every number.
- **`results.jsonl` / `results.csv`** — one row per run. Aggregation is a
  group-by *you* perform, so nobody has to trust our summary.
- **`publish/`** — per-run evidence: redacted session logs, rate-table
  snapshots, stamps.
- **`evidence/`** — content-addressed store of the exact prompt, judge and rate
  table each run used.

## Check our arithmetic without trusting us

```
python3 verify.py publish
```

Offline. No network, no vendor access, standard library only.

**Expect `PASS 210 · FAIL 0 · SKIP 0`.** Every run is independently checkable:
`orgsync` graduated on 2026-07-23, so its prompt, judge and design artifacts are
revealed here — each re-verified against the SHA-256 published in `TASKS.md`
before the task was ever run.

## What this claim is, precisely

**Re-verification, not re-execution.** Model runs are not reproducible in
principle — sampling is stochastic, CLIs update, models retire. What is
checkable, offline and indefinitely, is the arithmetic: given these session logs
and this rate table, these costs are correct.

Session logs are redacted to the fields the cost derives from, on an allowlist
(`redact.py`). A raw log is a full transcript — prompts, reasoning, tool output,
file diffs, operator paths, and the vendor's proprietary system prompt — and
publishing all of that to prove an arithmetic claim would disclose far more than
the claim requires, and is not ours to disclose. `python3 redact.py --check`
fails on an unredacted tree, so it is a real check rather than a rubber stamp.

## Licence and scope

Results are a measurement of a moment, not a property of the models. No overall
winner is ever crowned and no composite score is ever published (`PROTOCOL.md`
§1). Scores are per task.
