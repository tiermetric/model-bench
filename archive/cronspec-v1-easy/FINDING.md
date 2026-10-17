# cronspec v1 (easy) — record, 2026-07-22

**Result: 7/7 models PASSED** the deviated-cron task (0 edge failures). Frontier models
read the 3 deviations (DOM∧DOW intersection, range-anchored steps, dow=7 invalid) correctly
— it discriminated on COST/EFFORT, not reliability. Cost spread 34× ($0.053 luna → $1.815 fable);
Claude models used 3–9× more output than Codex (haiku 36,387 output tokens, yet cheap at $0.374).

**Action (the operator ruling B, 2026-07-22):** hardened to cronspec v2 (added wrap-around ranges,
step-over-wrap, L-last-day, leap/month-length rollover) to push pass-rate into the 40–85% band.
This "task saturated → hardened" event is a reportable methodology-transparency item.
