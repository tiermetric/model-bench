# Cross-model / cross-tool TIER demo — `cronspec` task (harder tier)

**Purpose:** the SAME task run on Codex and on four Claude Code models (Fable, Opus, Sonnet, Haiku), each in a fresh repo, captured by TIER, to compare token-spend-per-passing-outcome. This is a demonstration of TIER's measurement, not a rigorous benchmark. This task is deliberately hard: it defines a cron-like scheduler whose semantics DIVERGE from standard cron in seven precisely-specified ways. Every rule below is unambiguous; a careless or from-memory "real cron" implementation will get several wrong. Read the whole spec — the deviations and hardening rules are what the hidden judge checks. Paste verbatim into every run.

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
| 3 | day-of-month  | 1–31 (or the literal `L`)       | —     |
| 4 | month         | 1–12                            | `jan feb mar apr may jun jul aug sep oct nov dec` |
| 5 | day-of-week   | 0–6, **0 = Sunday … 6 = Saturday** | `sun mon tue wed thu fri sat` |

Leading/trailing whitespace around the whole expression is ignored, and fields
may be separated by one or more spaces. Each field is one of:

- `*` — every value in the field's domain. This is the only value that counts as
  UNRESTRICTED (see Deviation 1).
- a single value: a number in-domain, or (month/dow only) a lowercase name.
- a range `A-B` (inclusive) — see Deviation 2 and Rule 4 (wrap).
- a step `*/s` or `A-B/s` — see Deviation 2 and Rule 5. `s` is a positive integer.
- a comma list of any of the above: `1,3,5`, `mon,wed,fri`, `0-10/2,30`.
- day-of-month only: the literal `L` — see Rule 6.

Month/day-of-week names are **lowercase only** (`jan`, `fri`), never uppercase.
Names and numbers may be mixed across a list (`jan,jul`, `1,fri`).

### THREE original deviations from standard cron

**Deviation 1 — day-of-month ∧ day-of-week is an INTERSECTION.**
When BOTH field 3 (day-of-month) and field 5 (day-of-week) are restricted (i.e.
neither is a bare `*`), a day matches only if it satisfies BOTH constraints.
(Standard vixie cron takes the UNION here — that is the trap.) If exactly one of
the two is `*`, the other one alone decides the day. If both are `*`, every day
matches. A field like `*/2`, `1-5`, or `L` counts as restricted — only a bare
`*` is unrestricted.

- `0 0 13 * fri` → fires only on a Friday that is also the 13th (Friday-the-13th).
- `0 0 1 * mon` → fires only on a day that is both the 1st and a Monday.

**Deviation 2 — a step is anchored to the RANGE START, not to 0.**
For `A-B/s`, the matching values are `A, A+s, A+2s, …` up to and including `B`
(start at `A`, stride `s`). This is NOT "every multiple of `s` inside `[A,B]`".
For `*/s`, the range is the field's full domain, so the anchor is the domain
floor (0 for minute/hour, 1 for day-of-month/month, 0 for day-of-week).

- `5-30/10` → `5, 15, 25` (start 5, +10), NOT `10, 20, 30`.
- `*/15` on minute → `0, 15, 30, 45` (floor 0 — coincides with standard cron;
  the deviation only bites on an explicit `A-B/s`).
- `10-50/15` on minute → `10, 25, 40`.

**Deviation 3 — strict domain and strict rejection.**
Every value must lie in the field's domain in the table above. In particular
day-of-week is **0–6 only; `7` is INVALID** (standard cron accepts `7` as Sunday
— here it is a hard error). See the error contract below for the full list.

### FOUR hardening rules

**Rule 4 — ranges WRAP when start > end.**
In any field, a range `A-B` with `A > B` wraps through the field's maximum back
down to `B`: it matches `{A, A+1, …, hi} ∪ {lo, lo+1, …, B}`, where `lo`/`hi` are
the field's domain bounds. `A` and `B` must both be in-domain. (`A > B` is
therefore VALID here — it is NOT the "start > end" error it would be in standard
parsers.)

- minute `50-10` → `{50,51,…,59, 0,1,…,10}`.
- month `nov-feb` → `{nov, dec, jan, feb}` = `{11, 12, 1, 2}`.
- day-of-week `fri-mon` → `{fri, sat, sun, mon}` = `{5, 6, 0, 1}`.

**Rule 5 — a step over a wrapped range is anchored at the range start and counts
THROUGH the wrap.**
For `A-B/s` with `A > B`, list the wrapped range in forward order starting at `A`
(`A, A+1, …, hi, lo, …, B`), then take every `s`-th element beginning with `A`.

- minute `50-10/5` → the wrapped order is `50,51,…,59,0,…,10`; every 5th →
  `{50, 55, 0, 5, 10}`.
- day-of-week `fri-tue/2` → wrapped order `fri,sat,sun,mon,tue`; every 2nd →
  `{fri, sun, tue}` = `{5, 0, 2}`.

**Rule 6 — `L` = the last calendar day of the month (day-of-month field only).**
The day-of-month field may be exactly `L` (uppercase, the WHOLE field — never in
a list, range, step, or any other field). `L` matches the last day of whatever
month is being considered: 31, 30, 29, or 28 as appropriate, including a correct
leap-year February. `L` is a restricted day-of-month, so Deviation 1 applies: if
the day-of-week field is also restricted, the day must satisfy BOTH.

- `0 0 L * *` → midnight on the last day of every month (Jan 31, Feb 28/29, …).
- `0 0 L * fri` → fires only when the month's last day IS a Friday.

**Rule 7 — month lengths and leap years are respected when searching forward.**
A numeric day-of-month that does not exist in a given month means that month
produces NO match — the day is NOT clamped onto the last day. `next_fire` must
search forward across months and years to the next real matching date.

- `0 0 31 * *` → skips 30-day months and February entirely; fires only on the
  31st of 31-day months.
- `0 0 29 feb *` → fires only on February 29th of a leap year.

### Worked examples that must hold

(UTC, tz-naive. Import `datetime` from the standard library.)

```
# original deviations
next_fire("*/15 * * * *",       datetime(2026, 1, 1, 0, 0))   == datetime(2026, 1, 1, 0, 15)
next_fire("5-30/10 12 1 6 *",   datetime(2026, 6, 1, 0, 0))   == datetime(2026, 6, 1, 12, 5)    # Dev 2: 5, not 10
next_fire("5-30/10 12 1 6 *",   datetime(2026, 6, 1, 12, 5))  == datetime(2026, 6, 1, 12, 15)
next_fire("0 0 13 * fri",       datetime(2026, 1, 1, 0, 0))   == datetime(2026, 2, 13, 0, 0)    # Dev 1: Fri the 13th
next_fire("0 0 1 * mon",        datetime(2026, 6, 2, 0, 0))   == datetime(2027, 2, 1, 0, 0)     # Dev 1: 1st AND Monday
next_fire("30 9 * * mon",       datetime(2026, 7, 22, 0, 0))  == datetime(2026, 7, 27, 9, 30)
next_fire("0 12 1 jan,jul *",   datetime(2026, 3, 1, 0, 0))   == datetime(2026, 7, 1, 12, 0)

# Rule 4 — wrap-around ranges
next_fire("50-10 * * * *",      datetime(2026, 1, 1, 0, 30))  == datetime(2026, 1, 1, 0, 50)    # wraps: {50..59,0..10}
next_fire("50-10 * * * *",      datetime(2026, 1, 1, 0, 55))  == datetime(2026, 1, 1, 0, 56)
next_fire("0 0 1 nov-feb *",    datetime(2026, 3, 1, 0, 0))   == datetime(2026, 11, 1, 0, 0)    # nov-feb = {11,12,1,2}
next_fire("0 0 * * fri-mon",    datetime(2026, 7, 21, 0, 0))  == datetime(2026, 7, 24, 0, 0)    # Tue -> Fri (dow wrap)

# Rule 5 — steps that count through the wrap
next_fire("50-10/5 0 1 1 *",    datetime(2026, 1, 1, 0, 0))   == datetime(2026, 1, 1, 0, 5)     # {50,55,0,5,10}
next_fire("0 0 * * fri-tue/2",  datetime(2026, 7, 21, 0, 0))  == datetime(2026, 7, 24, 0, 0)    # {fri,sun,tue}: Tue->Fri

# Rule 6 — L = last day of month
next_fire("0 0 L * *",          datetime(2026, 1, 15, 0, 0))  == datetime(2026, 1, 31, 0, 0)
next_fire("0 0 L * *",          datetime(2026, 2, 1, 0, 0))   == datetime(2026, 2, 28, 0, 0)    # 2026 Feb non-leap
next_fire("0 0 L 2 *",          datetime(2027, 3, 1, 0, 0))   == datetime(2028, 2, 29, 0, 0)    # next leap Feb
next_fire("0 0 L * fri",        datetime(2026, 1, 1, 0, 0))   == datetime(2026, 7, 31, 0, 0)    # last day that is a Friday

# Rule 7 — month-length / leap rollover
next_fire("0 0 31 * *",         datetime(2026, 4, 1, 0, 0))   == datetime(2026, 5, 31, 0, 0)    # skip Apr(30)
next_fire("0 0 30 * *",         datetime(2026, 2, 1, 0, 0))   == datetime(2026, 3, 30, 0, 0)    # skip Feb
next_fire("0 0 29 feb *",       datetime(2027, 1, 1, 0, 0))   == datetime(2028, 2, 29, 0, 0)    # leap only
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
next_fire("*/0 * * * *",<any datetime>)   # step <= 0
next_fire("5/10 * * * *",<any datetime>)  # step on a single value (only * or a range may be stepped)
next_fire("1,,3 * * * *",<any datetime>)  # empty list element
next_fire("1x * * * *", <any datetime>)   # unknown token
next_fire("70-10 * * * *",<any datetime>) # wrap endpoints must still be in-domain (70 invalid)
next_fire("0 0 L,15 * *",<any datetime>)  # L may not be combined (list/range/step)
next_fire("0 0 * * L",  <any datetime>)   # L only in the day-of-month field
next_fire("0 0 l * *",  <any datetime>)   # lowercase l is not L
```

Note: `30-5`, `nov-feb`, and `fri-mon` are VALID wrap-around ranges (Rule 4), not
errors. `A > B` never raises.

Then create `test_cron.py` using Python's standard-library `unittest` (no
pytest) that covers every rule above, every worked example, each deviation, each
hardening rule, and each error case.

Create only `cron.py` and `test_cron.py`. Do not create any other files. When
finished, `python3 -m unittest test_cron` must pass with zero failures.
