# Calibration records

A `<task>.cleared` file here means a floor detector has **failed** that task —
so it has earned the expensive models. `run.sh` refuses to run any non-floor
model against a task with no record (PROTOCOL §3.4).

Floor detectors: `haiku` (Claude lane) and `gpt-5.6-luna` (Codex lane).

Do not create these by hand. A record is written when calibration measures a
real failure. Creating one to unblock a run defeats the only mechanism that
stops us paying flagship prices to learn what the cheapest model already knew.
