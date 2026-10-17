"""A cron-like scheduler with a small, deliberately non-standard dialect.

Public API::

    next_fire(expr: str, after: datetime) -> datetime

All datetimes are tz-naive UTC wall-clock values.  See the module tests for the
full behavioural contract; the short version is:

* day-of-month and day-of-week INTERSECT when both are restricted (Deviation 1),
* ``A-B/s`` steps are anchored at ``A``, not at 0 (Deviation 2),
* domains are strict -- day-of-week ``7`` is an error (Deviation 3),
* ``A-B`` with ``A > B`` wraps through the domain maximum (Rule 4),
* a step over a wrapped range counts through the wrap (Rule 5),
* ``L`` in the day-of-month field is the month's last calendar day (Rule 6),
* month lengths and leap years are honoured when searching forward (Rule 7).
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta

__all__ = ["next_fire"]


_NUMBER_RE = re.compile(r"^[0-9]+$")

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_DOW_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}

# How far forward next_fire is willing to search before giving up on an
# expression that can never match (e.g. "0 0 30 feb *").
_SEARCH_HORIZON_YEARS = 200


class _Field:
    """One parsed cron field: the matching value set plus its domain."""

    __slots__ = ("name", "lo", "hi", "names", "values", "restricted", "last_day")

    def __init__(self, name, lo, hi, names):
        self.name = name
        self.lo = lo
        self.hi = hi
        self.names = names
        self.values = frozenset()
        self.restricted = True
        self.last_day = False  # day-of-month "L"


def _err(message):
    raise ValueError(message)


def _parse_value(token, field):
    """Parse a single numeric or named value token into an in-domain int."""
    if _NUMBER_RE.match(token):
        value = int(token)
    elif field.names is not None and token in field.names:
        value = field.names[token]
    else:
        _err("invalid %s value: %r" % (field.name, token))
    if not (field.lo <= value <= field.hi):
        _err(
            "%s value %d out of range %d-%d"
            % (field.name, value, field.lo, field.hi)
        )
    return value


def _expand_range(start, end, field):
    """Inclusive range in forward order; wraps through the domain max (Rule 4)."""
    if start <= end:
        return list(range(start, end + 1))
    return list(range(start, field.hi + 1)) + list(range(field.lo, end + 1))


def _parse_step(text, field):
    if not _NUMBER_RE.match(text):
        _err("invalid step in %s: %r" % (field.name, text))
    step = int(text)
    if step <= 0:
        _err("step must be positive in %s: %r" % (field.name, text))
    return step


def _parse_element(token, field):
    """Parse one comma-separated element into a list of matching values."""
    if token == "":
        _err("empty element in %s field" % field.name)

    body = token
    step = 1
    if "/" in token:
        parts = token.split("/")
        if len(parts) != 2:
            _err("malformed step in %s: %r" % (field.name, token))
        body, step_text = parts
        step = _parse_step(step_text, field)
        if body != "*" and "-" not in body:
            _err(
                "step may only be applied to '*' or a range in %s: %r"
                % (field.name, token)
            )

    if body == "*":
        values = list(range(field.lo, field.hi + 1))
    elif "-" in body:
        bounds = body.split("-")
        if len(bounds) != 2 or bounds[0] == "" or bounds[1] == "":
            _err("malformed range in %s: %r" % (field.name, token))
        start = _parse_value(bounds[0], field)
        end = _parse_value(bounds[1], field)
        values = _expand_range(start, end, field)
    else:
        values = [_parse_value(body, field)]

    # Deviation 2 / Rule 5: anchor the stride at the range start and count
    # forward through the (possibly wrapped) range.
    return values[::step]


def _parse_field(text, field):
    if text == "*":
        field.values = frozenset(range(field.lo, field.hi + 1))
        field.restricted = False
        return field

    if field.name == "day-of-month" and text == "L":
        field.last_day = True
        field.restricted = True
        return field

    values = set()
    for token in text.split(","):
        values.update(_parse_element(token, field))
    field.values = frozenset(values)
    field.restricted = True
    return field


def _parse(expr):
    if not isinstance(expr, str):
        _err("cron expression must be a string")
    fields = expr.split()
    if len(fields) != 5:
        _err("cron expression must have exactly 5 fields, got %d" % len(fields))

    minute = _parse_field(fields[0], _Field("minute", 0, 59, None))
    hour = _parse_field(fields[1], _Field("hour", 0, 23, None))
    dom = _parse_field(fields[2], _Field("day-of-month", 1, 31, None))
    month = _parse_field(fields[3], _Field("month", 1, 12, _MONTH_NAMES))
    dow = _parse_field(fields[4], _Field("day-of-week", 0, 6, _DOW_NAMES))
    return minute, hour, dom, month, dow


def _cron_dow(day):
    """Day-of-week with Sunday == 0, matching the field's domain."""
    return day.isoweekday() % 7


def _day_matches(day, dom, dow):
    if dom.last_day:
        dom_ok = day.day == calendar.monthrange(day.year, day.month)[1]
    else:
        dom_ok = day.day in dom.values
    dow_ok = _cron_dow(day) in dow.values

    # Deviation 1: intersection when both fields are restricted.
    if dom.restricted and dow.restricted:
        return dom_ok and dow_ok
    if dom.restricted:
        return dom_ok
    if dow.restricted:
        return dow_ok
    return True


def _first_of_next_month(day):
    if day.month == 12:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)


def next_fire(expr, after):
    """Return the earliest datetime strictly after ``after`` matching ``expr``.

    ``after`` is a tz-naive UTC datetime; the result always has
    ``second == microsecond == 0``.
    """
    minute, hour, dom, month, dow = _parse(expr)

    if not isinstance(after, datetime):
        _err("after must be a datetime")

    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    minutes = sorted(minute.values)
    hours = sorted(hour.values)
    months = month.values

    start_day = candidate.date()
    day = start_day
    horizon = min(start_day.year + _SEARCH_HORIZON_YEARS, datetime.max.year)

    while day.year <= horizon:
        if day.month not in months:
            day = _first_of_next_month(day)
            continue
        if _day_matches(day, dom, dow):
            if day == start_day:
                floor_hour, floor_minute = candidate.hour, candidate.minute
            else:
                floor_hour, floor_minute = 0, 0
            for h in hours:
                if h < floor_hour:
                    continue
                lowest = floor_minute if h == floor_hour else 0
                for m in minutes:
                    if m >= lowest:
                        return datetime(day.year, day.month, day.day, h, m)
        try:
            day += timedelta(days=1)
        except OverflowError:  # pragma: no cover - only at date.max
            break

    _err("no matching time for %r within %d years" % (expr, _SEARCH_HORIZON_YEARS))
