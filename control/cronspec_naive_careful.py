#!/usr/bin/env python3
"""Control arm (FAIL side #2): the ORIGINAL 3 deviations right, the 4 hardening
rules WRONG — a "careful but incomplete" implementation.

This impl reads the original spec cleanly and implements all three original
deviations correctly (DOM∧DOW intersection, step anchored to the range start for
forward ranges, day-of-week 0-6 with 7 rejected). But it does NOT implement any
of the four hardening rules:

  * Wrap-around ranges (Rule 4)      -> `A-B` with A > B raises ValueError.
  * Steps through a wrap (Rule 5)    -> N/A (wrap already rejected).
  * `L` = last day of month (Rule 6) -> `L` is an unknown token -> ValueError.
  * Month-length / leap rollover (Rule 7) -> a day-of-month value that exceeds a
    month's length is CLAMPED to the last day (a classic careless bug), instead
    of skipping that month. So `0 0 31 * *` fires on the 30th of 30-day months
    and the 28th/29th of February, and `0 0 29 feb *` fires on Feb 28 in a
    non-leap year — both WRONG.

check-cronspec-selftest.py copies this in as `cron.py` and asserts it FAILS
acceptance_cronspec.py, specifically on the four hardening rules while still
AGREEING with the reference on the original-deviation cases. If it ever PASSES,
the judge is not testing the added difficulty and must be fixed.
"""
import calendar
from datetime import datetime, timedelta  # noqa: F401

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}
DOW = {d: i for i, d in enumerate(
    ["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}


def _parse_value(tok, lo, hi, names):
    if tok in names:
        v = names[tok]
    elif tok.isdigit():
        v = int(tok)
    else:
        raise ValueError(f"unknown token: {tok!r}")
    if not (lo <= v <= hi):
        raise ValueError(f"value {tok!r} out of domain [{lo},{hi}]")
    return v


def _parse_element(elem, lo, hi, names, out):
    if elem == "":
        raise ValueError("empty field element")
    had_step = "/" in elem
    step = 1
    base = elem
    if had_step:
        parts = elem.split("/")
        if len(parts) != 2 or parts[0] == "":
            raise ValueError(f"unknown token: {elem!r}")
        base, stepstr = parts
        if not stepstr.isdigit():
            raise ValueError(f"invalid step: {elem!r}")
        step = int(stepstr)
        if step <= 0:
            raise ValueError(f"step <= 0: {elem!r}")
    if base == "*":
        start, end = lo, hi
    elif "-" in base:
        rparts = base.split("-")
        if len(rparts) != 2 or rparts[0] == "" or rparts[1] == "":
            raise ValueError(f"unknown token: {base!r}")
        start = _parse_value(rparts[0], lo, hi, names)
        end = _parse_value(rparts[1], lo, hi, names)
        # WRONG vs Rule 4/5: no wrap — treats A > B as an error.
        if start > end:
            raise ValueError(f"range start > end: {base!r}")
    else:
        if had_step:
            raise ValueError(f"step requires '*' or a range: {elem!r}")
        start = end = _parse_value(base, lo, hi, names)
    # Deviation 2 (correct for forward ranges): anchored to the range start.
    v = start
    while v <= end:
        out.add(v)
        v += step


def _parse_field(field, lo, hi, names):
    if field == "":
        raise ValueError("empty field")
    if field == "*":
        return False, set(range(lo, hi + 1))
    out = set()
    for elem in field.split(","):
        _parse_element(elem, lo, hi, names, out)
    if not out:
        raise ValueError("empty field")
    return True, out


def _cron_dow(dt):
    return (dt.weekday() + 1) % 7


def next_fire(expr, after):
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"expected exactly 5 fields, got {len(fields)}")
    _, minute_set = _parse_field(fields[0], 0, 59, {})
    _, hour_set = _parse_field(fields[1], 0, 23, {})
    # WRONG vs Rule 6: no 'L' support — parsed as an ordinary field, so 'L' is an
    # unknown token and raises ValueError.
    dom_r, dom_set = _parse_field(fields[2], 1, 31, {})
    _, month_set = _parse_field(fields[3], 1, 12, MONTHS)
    dow_r, dow_set = _parse_field(fields[4], 0, 6, DOW)  # Deviation 3 (correct).

    def dom_matches(dt):
        # WRONG vs Rule 7: clamp a day-of-month that exceeds this month's length
        # onto the last day, instead of skipping the month.
        ld = calendar.monthrange(dt.year, dt.month)[1]
        if dt.day in dom_set:
            return True
        return dt.day == ld and any(d > ld for d in dom_set)

    def day_matches(dt):
        dom_ok = dom_matches(dt)
        dow_ok = _cron_dow(dt) in dow_set
        if dom_r and dow_r:
            return dom_ok and dow_ok      # Deviation 1 (correct).
        if dom_r:
            return dom_ok
        if dow_r:
            return dow_ok
        return True

    minutes = sorted(minute_set)
    hours = sorted(hour_set)
    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = after + timedelta(days=366 * 8)
    cur = t
    while cur <= limit:
        if cur.month in month_set and day_matches(cur):
            for hh in hours:
                for mm in minutes:
                    cand = cur.replace(hour=hh, minute=mm)
                    if cand >= cur:
                        return cand
        cur = (cur + timedelta(days=1)).replace(hour=0, minute=0)
    raise ValueError("no matching time within 8-year horizon")
