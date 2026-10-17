"""Cron-like schedule computation (standard library only).

Provides one public function, ``next_fire(expr, after)``, implementing a
five-field cron dialect with three deviations from standard vixie cron:

1. day-of-month AND day-of-week are intersected (never unioned);
2. steps are anchored at the range start (``5-30/10`` -> 5, 15, 25);
3. strict domains (day-of-week is 0-6 only; 7 is an error),

plus wrap-around ranges (``50-10``), steps that count through the wrap,
``L`` (last day of month) in the day-of-month field, and real month
lengths / leap years when searching forward.

All datetimes are tz-naive UTC wall-clock values.
"""

import calendar
from datetime import datetime, timedelta

__all__ = ["next_fire"]

_MONTH_NAMES = {name: i + 1 for i, name in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split())}
_DOW_NAMES = {name: i for i, name in enumerate(
    "sun mon tue wed thu fri sat".split())}

# (label, lo, hi, names) for each of the five fields, in order.
_FIELDS = (
    ("minute", 0, 59, None),
    ("hour", 0, 23, None),
    ("day-of-month", 1, 31, None),
    ("month", 1, 12, _MONTH_NAMES),
    ("day-of-week", 0, 6, _DOW_NAMES),
)

# Longest possible gap between fires of a valid expression is the Feb 29
# drought around non-leap century years (8 years); 100 years of daily
# candidates is a safe hard stop for any satisfiable expression.
_MAX_SEARCH_DAYS = 366 * 100


def _parse_value(tok, label, lo, hi, names):
    if names is not None and tok in names:
        value = names[tok]
    elif tok.isdigit():
        value = int(tok)
    else:
        raise ValueError(f"invalid {label} value: {tok!r}")
    if not lo <= value <= hi:
        raise ValueError(f"{label} value {value} out of domain {lo}-{hi}")
    return value


def _expand_range(a, b, lo, hi):
    """a-b in forward order, wrapping through hi back to lo when a > b."""
    if a <= b:
        return list(range(a, b + 1))
    return list(range(a, hi + 1)) + list(range(lo, b + 1))


def _parse_element(elem, label, lo, hi, names):
    step = 1
    if "/" in elem:
        base, _, step_str = elem.partition("/")
        if "/" in step_str or not step_str.isdigit() or int(step_str) == 0:
            raise ValueError(f"invalid step in {label} element {elem!r}")
        if base != "*" and "-" not in base:
            raise ValueError(
                f"step requires '*' or a range in {label} element {elem!r}")
        step = int(step_str)
        elem = base
    if elem == "*":
        order = list(range(lo, hi + 1))
    elif "-" in elem:
        a_str, _, b_str = elem.partition("-")
        if not a_str or not b_str or "-" in b_str:
            raise ValueError(f"invalid {label} range {elem!r}")
        a = _parse_value(a_str, label, lo, hi, names)
        b = _parse_value(b_str, label, lo, hi, names)
        order = _expand_range(a, b, lo, hi)
    else:
        order = [_parse_value(elem, label, lo, hi, names)]
    # Deviation 2 / Rule 5: stride from the range start, in wrapped order.
    return order[::step]


def _parse_field(field, index):
    label, lo, hi, names = _FIELDS[index]
    values = set()
    for elem in field.split(","):
        if not elem:
            raise ValueError(f"empty element in {label} field {field!r}")
        values.update(_parse_element(elem, label, lo, hi, names))
    return values


def next_fire(expr: str, after: datetime) -> datetime:
    """Return the earliest datetime strictly after ``after`` matching ``expr``."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(
            f"expression must have exactly 5 fields, got {len(fields)}")

    minutes = sorted(_parse_field(fields[0], 0))
    hours = sorted(_parse_field(fields[1], 1))
    dom_is_last = fields[2] == "L"  # Rule 6: whole field, uppercase only
    doms = None if dom_is_last else _parse_field(fields[2], 2)
    months = _parse_field(fields[3], 3)
    dows = _parse_field(fields[4], 4)

    # First candidate: the next whole minute strictly after `after`.
    start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = start.date()
    for _ in range(_MAX_SEARCH_DAYS):
        if day.month in months:
            if dom_is_last:
                dom_ok = day.day == calendar.monthrange(day.year, day.month)[1]
            else:
                dom_ok = day.day in doms
            # Deviation 1: intersection. Bare '*' parses to the full domain,
            # so an unrestricted field is always satisfied and the other
            # field alone decides; ANDing is correct in every case.
            dow_ok = (day.weekday() + 1) % 7 in dows  # cron: 0 = Sunday
            if dom_ok and dow_ok:
                if day == start.date():
                    for h in hours:
                        if h < start.hour:
                            continue
                        for m in minutes:
                            if h == start.hour and m < start.minute:
                                continue
                            return datetime(day.year, day.month, day.day, h, m)
                else:
                    return datetime(day.year, day.month, day.day,
                                    hours[0], minutes[0])
        day += timedelta(days=1)
    raise ValueError(f"no matching time within 100 years for {expr!r}")
