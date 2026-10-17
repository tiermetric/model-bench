#!/usr/bin/env python3
"""Control arm (PASS side): a spec-FAITHFUL `next_fire` for the cronspec task.

This is the canonical correct implementation of the DEVIATED spec in
prompts/cronspec.md — the three original deviations PLUS the four hardening rules
(wrap-around ranges, steps that count through a wrap, `L` = last day of month,
and correct month-length/leap rollover). It is copied in as `cron.py` by
check-cronspec-selftest.py, which asserts it PASSES acceptance_cronspec.py. Its
logic is kept byte-for-byte identical to the reference embedded in
acceptance_cronspec.py; the self-test's PASS on this file is what proves the two
copies agree.

Not a benchmark target; never run by run.sh. It exists only so the judge's
trustworthiness is demonstrable: a correct impl passes, while cronspec_vixie.py
(vixie-faithful) and cronspec_naive_careful.py (original 3 deviations but none of
the 4 hardening rules) both fail.
"""
import calendar
from datetime import datetime, timedelta  # noqa: F401  (datetime used by callers)

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}
DOW = {d: i for i, d in enumerate(
    ["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}


def _last_day(dt):
    return calendar.monthrange(dt.year, dt.month)[1]


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


def _emit(start, end, step, lo, hi, out):
    # Wrapped ordered sequence from start to end inclusive (wraps hi->lo when
    # start > end). The step is anchored at index 0 (the range start) and counts
    # forward THROUGH the wrap.
    if start <= end:
        seq = list(range(start, end + 1))
    else:
        seq = list(range(start, hi + 1)) + list(range(lo, end + 1))
    for i in range(0, len(seq), step):
        out.add(seq[i])


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
            raise ValueError(f"invalid step (not a positive integer): {elem!r}")
        step = int(stepstr)
        if step <= 0:
            raise ValueError(f"step <= 0: {elem!r}")
    if base == "*":
        _emit(lo, hi, step, lo, hi, out)
    elif "-" in base:
        rparts = base.split("-")
        if len(rparts) != 2 or rparts[0] == "" or rparts[1] == "":
            raise ValueError(f"unknown token: {base!r}")
        start = _parse_value(rparts[0], lo, hi, names)
        end = _parse_value(rparts[1], lo, hi, names)
        _emit(start, end, step, lo, hi, out)   # start > end wraps (not an error)
    else:
        if had_step:
            raise ValueError(f"step requires '*' or a range: {elem!r}")
        out.add(_parse_value(base, lo, hi, names))


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
    # day-of-month: 'L' (the whole field only) = last calendar day of the month.
    dom_f = fields[2]
    if dom_f == "L":
        dom_r, dom_kind, dom_set = True, "L", None
    else:
        dom_r, dom_set = _parse_field(dom_f, 1, 31, {})
        dom_kind = "set"
    _, month_set = _parse_field(fields[3], 1, 12, MONTHS)
    dow_r, dow_set = _parse_field(fields[4], 0, 6, DOW)  # Deviation 3: 0-6 only.

    def dom_matches(dt):
        if dom_kind == "L":
            return dt.day == _last_day(dt)
        return dt.day in dom_set

    def day_matches(dt):
        dom_ok = dom_matches(dt)
        dow_ok = _cron_dow(dt) in dow_set
        if dom_r and dow_r:
            return dom_ok and dow_ok      # Deviation 1: intersection
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
