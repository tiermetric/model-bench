#!/usr/bin/env python3
"""Control arm (FAIL side): a vixie-FAITHFUL `next_fire` for the cronspec task.

This deliberately implements standard/vixie cron semantics, which get ALL THREE
of the cronspec deviations WRONG:

  * Deviation 1 — day-of-month AND day-of-week use UNION (vixie), not intersection.
  * Deviation 2 — a stepped range is "every multiple of step inside [A,B]"
    (anchored at 0), not anchored at the range start.
  * Deviation 3 — day-of-week 7 is accepted as an alias for Sunday.

check-cronspec-selftest.py copies this in as `cron.py` and asserts it FAILS
acceptance_cronspec.py, specifically on the deviated cases. If this ever PASSES
the judge, the judge is not actually testing the deviations and must be fixed.

On NON-deviation expressions this agrees with cronspec_reference.py — that is the
point: the only reason it fails the judge is the three deviations.
"""
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
        if start > end:
            raise ValueError(f"range start > end: {base!r}")
    else:
        if had_step:
            raise ValueError(f"step requires '*' or a range: {elem!r}")
        start = end = _parse_value(base, lo, hi, names)
    # WRONG vs Deviation 2: anchor the step at 0 (every multiple of step in [A,B]).
    for v in range(start, end + 1):
        if v % step == 0:
            out.add(v)


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


def _day_matches(dt, dom_r, dom_set, dow_r, dow_set):
    dom_ok = dt.day in dom_set
    dow_ok = _cron_dow(dt) in dow_set
    # WRONG vs Deviation 1: vixie takes the UNION when both are restricted.
    if dom_r and dow_r:
        return dom_ok or dow_ok
    if dom_r:
        return dom_ok
    if dow_r:
        return dow_ok
    return True


def next_fire(expr, after):
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"expected exactly 5 fields, got {len(fields)}")
    _, minute_set = _parse_field(fields[0], 0, 59, {})
    _, hour_set = _parse_field(fields[1], 0, 23, {})
    dom_r, dom_set = _parse_field(fields[2], 1, 31, {})
    _, month_set = _parse_field(fields[3], 1, 12, MONTHS)
    # WRONG vs Deviation 3: dow domain 0-7, 7 aliases Sunday.
    dow_r, dow_set = _parse_field(fields[4], 0, 7, DOW)
    if 7 in dow_set:
        dow_set.add(0)

    minutes = sorted(minute_set)
    hours = sorted(hour_set)
    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = after + timedelta(days=366 * 8)
    cur = t
    while cur <= limit:
        if cur.month in month_set and _day_matches(cur, dom_r, dom_set, dow_r, dow_set):
            for hh in hours:
                for mm in minutes:
                    cand = cur.replace(hour=hh, minute=mm)
                    if cand >= cur:
                        return cand
        cur = (cur + timedelta(days=1)).replace(hour=0, minute=0)
    raise ValueError("no matching time within 8-year horizon")
