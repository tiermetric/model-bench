# Cross-model / cross-tool TIER demo — `cronspec` task (medium tier)

**Purpose:** the SAME task run on Codex and on four Claude Code models (Fable, Opus, Sonnet, Haiku), each in a fresh repo, captured by TIER, to compare token-spend-per-passing-outcome. This is a demonstration of TIER's measurement, not a rigorous benchmark. This task is deliberately harder than `baseline`: it defines a cron-like scheduler with THREE non-standard rules that a from-memory "real cron" implementation will get wrong. Read the whole spec — the deviations are stated below and are what the hidden judge checks. Paste verbatim into every run.

---

You are working in a fresh, empty Python project. Implement a cron-like scheduler and a standard-library test suite for it. Use only the Python standard library — no third-party packages. This task is OFFLINE: do not use any web search / web fetch tool; everything you need is in this spec.

Create `cron.py` with exactly one public function:

```
next_fire(expr: str, after: datetime) -> datetime
```

It returns the earliest datetime STRICTLY AFTER `after` that matches the cron
expression `expr`. All times are UTC, tz-naive `datetime` objects; there is no
DST and no timezone math — treat every datetime as naive wall-clock UTC. The
result has `second == 0` and `microsecond == 0` (cron fires on minute
boundaries). `after` may have nonzero seconds/microseconds; the first candidate
considered is the next whole minute strictly after `after`.

### Expression grammar

`expr` is exactly FIVE whitespace-separated fields, in this order:

| # | field         | domain                          | names |
|---|---------------|---------------------------------|-------|
| 1 | minute        | 0–59                            | —     |
| 2 | hour          | 0–23                            | —     |
| 3 | day-of-month  | 1–31                            | —     |
| 4 | month         | 1–12                            | `jan feb mar apr may jun jul aug sep oct nov dec` |
| 5 | day-of-week   | 0–6, **0 = Sunday … 6 = Saturday** | `sun mon tue wed thu fri sat` |

Leading/trailing whitespace around the whole expression is ignored, and fields
may be separated by one or more spaces. Each field is one of:

- `*` — every value in the field's domain. This is the only value that counts as
  UNRESTRICTED (see Deviation 1).
- a single value: a number in-domain, or (month/dow only) a lowercase name.
- a range `A-B` (inclusive), where `A` and `B` are single values and `A <= B`.
- a step `*/s` or `A-B/s` — see Deviation 2. `s` is a positive integer.
- a comma list of any of the above: `1,3,5`, `mon,wed,fri`, `0-10/2,30`.

Month/day-of-week names are **lowercase only** (`jan`, `fri`), never numeric for
month if you prefer names, and never uppercase. Names and numbers may be mixed
across a list (`jan,jul`, `1,fri`).

### THREE non-standard rules (this is the whole point of the task)

**Deviation 1 — day-of-month ∧ day-of-week is an INTERSECTION.**
When BOTH field 3 (day-of-month) and field 5 (day-of-week) are restricted (i.e.
neither is a bare `*`), a day matches only if it satisfies BOTH constraints.
(Standard vixie cron takes the UNION here — that is the trap.) If exactly one of
the two is `*`, the other one alone decides the day. If both are `*`, every day
matches. A field like `*/2` or `1-5` counts as restricted — only a bare `*` is
unrestricted.

- `0 0 13 * fri` → fires only on a Friday that is also the 13th (Friday-the-13th),
  NOT on every Friday and NOT on every 13th.
- `0 0 1 * mon` → fires only on a day that is both the 1st and a Monday.

**Deviation 2 — a step is anchored to the RANGE START, not to 0.**
For `A-B/s`, the matching values are `A, A+s, A+2s, …` up to and including `B`
(start at `A`, stride `s`). This is NOT "every multiple of `s` inside `[A,B]`".
For `*/s`, the range is the field's full domain, so the anchor is the domain
floor (0 for minute/hour, 1 for day-of-month/month, 0 for day-of-week).

- `5-30/10` → `5, 15, 25` (start 5, +10), NOT `10, 20, 30`.
- `*/15` on minute → `0, 15, 30, 45` (domain floor 0 — coincides with standard cron;
  the deviation only bites on an explicit `A-B/s`).
- `10-50/15` on minute → `10, 25, 40`.

**Deviation 3 — strict domain and strict rejection.**
Every value must lie in the field's domain in the table above. In particular
day-of-week is **0–6 only; `7` is INVALID** (standard cron accepts `7` as an
alias for Sunday — here it is a hard error). Raise `ValueError` for any of:

- a value out of its field's domain (minute not 0–59, hour not 0–23,
  day-of-month not 1–31, month not 1–12, day-of-week not 0–6 — `7` is invalid);
- a step `s` that is not a positive integer (`*/0`, `*/-1`, `1-5/0`);
- a range whose start is greater than its end (`30-5`, `fri-mon`);
- an UPPERCASE name (`SUN`, `JAN`) or any unknown token (`1x`, `abc`, `mon-`);
- an empty field or empty list element (`""`, `1,,3`, `1,`);
- a step attached to a single value (`5/10` — a step may only follow `*` or a range);
- an expression that is not exactly 5 fields (after collapsing whitespace).

### Worked examples that must hold

(UTC, tz-naive. Import `datetime` from the standard library.)

```
next_fire("*/15 * * * *",       datetime(2026, 1, 1, 0, 0))   == datetime(2026, 1, 1, 0, 15)
next_fire("5-30/10 12 1 6 *",   datetime(2026, 6, 1, 0, 0))   == datetime(2026, 6, 1, 12, 5)    # Dev 2: 5, not 10
next_fire("5-30/10 12 1 6 *",   datetime(2026, 6, 1, 12, 5))  == datetime(2026, 6, 1, 12, 15)   # next: 15
next_fire("10-50/15 0 1 3 *",   datetime(2026, 3, 1, 0, 0))   == datetime(2026, 3, 1, 0, 10)    # Dev 2: 10,25,40
next_fire("0 0 13 * fri",       datetime(2026, 1, 1, 0, 0))   == datetime(2026, 2, 13, 0, 0)    # Dev 1: Fri the 13th, not Jan 2
next_fire("0 0 13 * fri",       datetime(2026, 2, 14, 0, 0))  == datetime(2026, 3, 13, 0, 0)
next_fire("0 0 1 * mon",        datetime(2026, 6, 2, 0, 0))   == datetime(2027, 2, 1, 0, 0)     # Dev 1: 1st AND Monday
next_fire("30 9 * * mon",       datetime(2026, 7, 22, 0, 0))  == datetime(2026, 7, 27, 9, 30)   # next Monday 09:30
next_fire("0 0 * * mon-fri",    datetime(2026, 7, 25, 0, 0))  == datetime(2026, 7, 27, 0, 0)    # Sat -> Mon (skips Sun)
next_fire("0 12 1 jan,jul *",   datetime(2026, 3, 1, 0, 0))   == datetime(2026, 7, 1, 12, 0)    # month list + names
next_fire("0 0 * * 0",          datetime(2026, 7, 22, 0, 0))  == datetime(2026, 7, 26, 0, 0)    # dow 0 = Sunday
next_fire("15,45 8-10 * * *",   datetime(2026, 7, 22, 9, 20)) == datetime(2026, 7, 22, 9, 45)   # lists + range
```

Error cases (each raises `ValueError`):

```
next_fire("",           <any datetime>)   # empty / not 5 fields
next_fire("* * * *",    <any datetime>)   # 4 fields
next_fire("* * * * * *",<any datetime>)   # 6 fields
next_fire("60 * * * *", <any datetime>)   # minute out of domain
next_fire("* 24 * * *", <any datetime>)   # hour out of domain
next_fire("* * 0 * *",  <any datetime>)   # day-of-month out of domain
next_fire("* * * 13 *", <any datetime>)   # month out of domain
next_fire("* * * * 7",  <any datetime>)   # Dev 3: dow 7 is INVALID
next_fire("* * * * SUN",<any datetime>)   # uppercase name
next_fire("* * * jan *",<any datetime>)   # NOTE: lowercase 'jan' in the MONTH field is VALID; 'JAN' is not
next_fire("*/0 * * * *",<any datetime>)   # step <= 0
next_fire("30-5 * * * *",<any datetime>)  # range start > end
next_fire("5/10 * * * *",<any datetime>)  # step on a single value
next_fire("1,,3 * * * *",<any datetime>)  # empty list element
next_fire("1x * * * *", <any datetime>)   # unknown token
```

Then create `test_cron.py` using Python's standard-library `unittest` (no
pytest) that covers every rule above, every worked example, each deviation, and
each error case.

Create only `cron.py` and `test_cron.py`. Do not create any other files. When
finished, `python3 -m unittest test_cron` must pass with zero failures.
