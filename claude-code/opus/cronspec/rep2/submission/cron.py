"""A cron-like scheduler with three deviations from standard cron.

Public API::

    next_fire(expr: str, after: datetime) -> datetime

All datetimes are naive wall-clock UTC.  See the module docstring of
``test_cron.py`` (and the project spec) for the full grammar; the short
version is five whitespace-separated fields::

    minute  hour  day-of-month  month  day-of-week
    0-59    0-23  1-31 or L     1-12   0-6 (0 = Sunday)

Deviations from vixie cron:

1. day-of-month AND day-of-week intersect (vixie unions them) whenever both
   fields are restricted (i.e. neither is a bare ``*``).
2. A step is anchored at the range start: ``5-30/10`` is ``{5, 15, 25}``.
3. Domains are strict: day-of-week ``7`` is an error, names are lowercase only.

Hardening rules: ranges wrap when start > end, steps count through the wrap,
``L`` means the last calendar day of the month, and month lengths / leap years
are respected when searching forward (no clamping).
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta

__all__ = ["next_fire"]


MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

DOW_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}

# name, low, high, names-map
_FIELDS = (
    ("minute", 0, 59, None),
    ("hour", 0, 23, None),
    ("day-of-month", 1, 31, None),
    ("month", 1, 12, MONTH_NAMES),
    ("day-of-week", 0, 6, DOW_NAMES),
)

# How far forward next_fire() is willing to search before declaring the
# expression unsatisfiable (e.g. "0 0 30 feb *").
_SEARCH_LIMIT_YEARS = 500


class _Field:
    """A parsed cron field: a value set plus whether it is restricted."""

    __slots__ = ("values", "restricted", "last_day")

    def __init__(self, values, restricted, last_day=False):
        self.values = values
        self.restricted = restricted
        self.last_day = last_day


def _parse_value(token, field_name, lo, hi, names):
    """Parse a single number or lowercase name into an in-domain int."""
    if not token:
        raise ValueError("empty value in %s field" % field_name)
    if token.isdigit():
        value = int(token)
    elif names is not None and token in names:
        value = names[token]
    else:
        raise ValueError("invalid value %r in %s field" % (token, field_name))
    if not lo <= value <= hi:
        raise ValueError(
            "value %r out of range %d-%d in %s field" % (token, lo, hi, field_name)
        )
    return value


def _forward_range(start, end, lo, hi):
    """Values from start to end inclusive, wrapping through hi back to lo."""
    if start <= end:
        return list(range(start, end + 1))
    return list(range(start, hi + 1)) + list(range(lo, end + 1))


def _parse_element(element, field_name, lo, hi, names):
    """Parse one comma-list element into a set of ints."""
    if not element:
        raise ValueError("empty element in %s field" % field_name)

    base = element
    step = None
    if "/" in element:
        parts = element.split("/")
        if len(parts) != 2:
            raise ValueError("malformed step %r in %s field" % (element, field_name))
        base, step_token = parts
        if not step_token.isdigit():
            raise ValueError("invalid step %r in %s field" % (element, field_name))
        step = int(step_token)
        if step <= 0:
            raise ValueError("step must be positive in %s field" % field_name)
        if not base:
            raise ValueError("malformed step %r in %s field" % (element, field_name))

    if base == "*":
        ordered = list(range(lo, hi + 1))
    elif "-" in base:
        endpoints = base.split("-")
        if len(endpoints) != 2:
            raise ValueError("malformed range %r in %s field" % (element, field_name))
        start = _parse_value(endpoints[0], field_name, lo, hi, names)
        end = _parse_value(endpoints[1], field_name, lo, hi, names)
        ordered = _forward_range(start, end, lo, hi)
    else:
        if step is not None:
            raise ValueError(
                "step not allowed on single value %r in %s field" % (element, field_name)
            )
        ordered = [_parse_value(base, field_name, lo, hi, names)]

    if step is not None:
        ordered = ordered[::step]
    return set(ordered)


def _parse_field(raw, index):
    field_name, lo, hi, names = _FIELDS[index]

    if index == 2:  # day-of-month: the only field where L is legal
        if raw == "L":
            return _Field(set(), restricted=True, last_day=True)
        if "L" in raw:
            raise ValueError("L must be the entire day-of-month field")
    elif "L" in raw:
        raise ValueError("L is only valid in the day-of-month field")

    restricted = raw != "*"
    values = set()
    for element in raw.split(","):
        values |= _parse_element(element, field_name, lo, hi, names)
    return _Field(values, restricted)


def _parse(expr):
    if not isinstance(expr, str):
        raise ValueError("cron expression must be a string")
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(
            "cron expression must have exactly 5 fields, got %d" % len(fields)
        )
    return [_parse_field(raw, i) for i, raw in enumerate(fields)]


def _cron_dow(year, month, day):
    """Day of week with 0 = Sunday (Python's weekday() has 0 = Monday)."""
    return (calendar.weekday(year, month, day) + 1) % 7


def _day_matches(dom, dow, year, month, day, last_day):
    dom_ok = day == last_day if dom.last_day else day in dom.values
    dow_ok = _cron_dow(year, month, day) in dow.values

    if dom.restricted and dow.restricted:
        return dom_ok and dow_ok  # Deviation 1: intersection, not union
    if dom.restricted:
        return dom_ok
    if dow.restricted:
        return dow_ok
    return True


def next_fire(expr, after):
    """Return the earliest datetime strictly after ``after`` matching ``expr``.

    ``after`` may carry seconds/microseconds; the first candidate considered is
    the next whole minute strictly after it.  The result always has
    ``second == 0`` and ``microsecond == 0``.
    """
    minute, hour, dom, month, dow = _parse(expr)

    if not isinstance(after, datetime):
        raise ValueError("after must be a datetime")

    floor = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    minutes = sorted(minute.values)
    hours = sorted(hour.values)

    year, mon = floor.year, floor.month
    end_year = floor.year + _SEARCH_LIMIT_YEARS
    while year <= end_year:
        if mon in month.values:
            last_day = calendar.monthrange(year, mon)[1]
            first_day = floor.day if (year, mon) == (floor.year, floor.month) else 1
            for day in range(first_day, last_day + 1):
                if not _day_matches(dom, dow, year, mon, day, last_day):
                    continue
                for h in hours:
                    for m in minutes:
                        candidate = datetime(year, mon, day, h, m)
                        if candidate >= floor:
                            return candidate
        mon += 1
        if mon > 12:
            mon = 1
            year += 1

    raise ValueError(
        "no matching time for %r within %d years of %s"
        % (expr, _SEARCH_LIMIT_YEARS, after)
    )
