"""Cron-like schedule evaluation.

Implements next_fire(expr, after): the earliest tz-naive UTC datetime strictly
after `after` matching the five-field cron expression `expr`.

Deviations from standard cron (per spec):
 1. day-of-month AND day-of-week are intersected when both are restricted.
 2. steps are anchored at the range start (5-30/10 -> 5,15,25), not at 0.
 3. strict domains: day-of-week is 0-6 only (7 is an error), names lowercase.

Hardening rules: ranges wrap when start > end (steps count through the wrap),
`L` matches the last day of the month, and nonexistent dates (Feb 30, Apr 31)
are skipped, never clamped.
"""

import calendar
from datetime import date, datetime, timedelta

_MONTH_NAMES = {name: i + 1 for i, name in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split())}
_DOW_NAMES = {name: i for i, name in enumerate(
    "sun mon tue wed thu fri sat".split())}

# How far forward to search before declaring the expression unsatisfiable
# (e.g. "0 0 31 feb *"). Feb-29-on-a-given-weekday needs at most 40 years;
# 500 leaves a huge margin.
_MAX_SEARCH_YEARS = 500


def _is_int_token(tok):
    return tok.isascii() and tok.isdigit()


def _parse_value(tok, lo, hi, names, what):
    if _is_int_token(tok):
        val = int(tok)
    elif names is not None and tok in names:
        val = names[tok]
    else:
        raise ValueError("invalid %s value: %r" % (what, tok))
    if not lo <= val <= hi:
        raise ValueError("%s value %d out of domain %d-%d" % (what, val, lo, hi))
    return val


def _parse_part(part, lo, hi, names, what):
    base, sep, step_s = part.partition("/")
    if sep:
        if not _is_int_token(step_s) or int(step_s) <= 0:
            raise ValueError("invalid step %r in %s field" % (step_s, what))
        step = int(step_s)
    else:
        step = 1

    if base == "*":
        seq = list(range(lo, hi + 1))
    elif "-" in base:
        ends = base.split("-")
        if len(ends) != 2:
            raise ValueError("invalid range %r in %s field" % (base, what))
        a = _parse_value(ends[0], lo, hi, names, what)
        b = _parse_value(ends[1], lo, hi, names, what)
        if a <= b:
            seq = list(range(a, b + 1))
        else:
            # Wrap through the domain maximum back down to b.
            seq = list(range(a, hi + 1)) + list(range(lo, b + 1))
    else:
        if sep:
            raise ValueError(
                "step is only valid on '*' or a range, got %r" % part)
        return {_parse_value(base, lo, hi, names, what)}

    # Anchored at the range start, counting through any wrap.
    return set(seq[::step])


def _parse_field(field, lo, hi, names, what, allow_last=False):
    """Return (values, restricted, is_last)."""
    if field == "*":
        return set(range(lo, hi + 1)), False, False
    if field == "L":
        if not allow_last:
            raise ValueError("'L' is only valid in the day-of-month field")
        return set(), True, True
    values = set()
    for part in field.split(","):
        if not part:
            raise ValueError("empty list element in %s field" % what)
        values |= _parse_part(part, lo, hi, names, what)
    return values, True, False


def _parse(expr):
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError("expected 5 fields, got %d: %r" % (len(fields), expr))
    minutes, _, _ = _parse_field(fields[0], 0, 59, None, "minute")
    hours, _, _ = _parse_field(fields[1], 0, 23, None, "hour")
    dom, dom_r, dom_last = _parse_field(
        fields[2], 1, 31, None, "day-of-month", allow_last=True)
    months, _, _ = _parse_field(fields[3], 1, 12, _MONTH_NAMES, "month")
    dow, dow_r, _ = _parse_field(fields[4], 0, 6, _DOW_NAMES, "day-of-week")
    return minutes, hours, dom, dom_r, dom_last, months, dow, dow_r


def _day_matches(day, dom, dom_r, dom_last, dow, dow_r):
    if dom_last:
        dom_ok = day.day == calendar.monthrange(day.year, day.month)[1]
    else:
        dom_ok = day.day in dom
    dow_ok = (day.weekday() + 1) % 7 in dow  # cron: 0 = Sunday
    if dom_r and dow_r:
        return dom_ok and dow_ok  # Deviation 1: intersection, not union
    if dom_r:
        return dom_ok
    if dow_r:
        return dow_ok
    return True


def _first_time(hours, minutes, start_h, start_m):
    for h in hours:
        if h < start_h:
            continue
        if h == start_h:
            for m in minutes:
                if m >= start_m:
                    return h, m
        else:
            return h, minutes[0]
    return None


def next_fire(expr, after):
    """Return the earliest datetime strictly after `after` matching `expr`."""
    minutes, hours, dom, dom_r, dom_last, months, dow, dow_r = _parse(expr)
    minutes = sorted(minutes)
    hours = sorted(hours)

    start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    cur = date(start.year, start.month, start.day)
    first_day = True
    limit_year = start.year + _MAX_SEARCH_YEARS

    while cur.year <= limit_year:
        if cur.month not in months:
            if cur.month == 12:
                cur = date(cur.year + 1, 1, 1)
            else:
                cur = date(cur.year, cur.month + 1, 1)
            first_day = False
            continue
        if _day_matches(cur, dom, dom_r, dom_last, dow, dow_r):
            start_h, start_m = (start.hour, start.minute) if first_day else (0, 0)
            found = _first_time(hours, minutes, start_h, start_m)
            if found is not None:
                return datetime(cur.year, cur.month, cur.day, *found)
        cur += timedelta(days=1)
        first_day = False

    raise ValueError("expression %r never matches" % expr)
