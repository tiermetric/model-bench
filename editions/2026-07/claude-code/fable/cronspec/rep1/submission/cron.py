"""Cron-like schedule computation.

Public API: next_fire(expr, after) -> datetime

All datetimes are tz-naive UTC wall-clock. Semantics deviate from standard
vixie cron in three ways (intersection of day-of-month/day-of-week, steps
anchored at the range start, strict domains with dow 7 invalid) and add four
hardening rules (wrap-around ranges, steps that count through the wrap,
`L` = last day of month, real month lengths / leap years).
"""

import calendar
from datetime import datetime, timedelta

__all__ = ["next_fire"]

_LAST_DAY = object()  # sentinel for a day-of-month field of exactly "L"

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DOW_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}

# (lo, hi, names) per field: minute, hour, day-of-month, month, day-of-week
_FIELDS = (
    (0, 59, None),
    (0, 23, None),
    (1, 31, None),
    (1, 12, _MONTH_NAMES),
    (0, 6, _DOW_NAMES),
)


def _parse_value(token, lo, hi, names):
    """Parse a single value token (number or lowercase name) into an int."""
    if names and token in names:
        return names[token]
    if token.isdigit():
        value = int(token)
        if lo <= value <= hi:
            return value
        raise ValueError("value %d out of domain %d-%d" % (value, lo, hi))
    raise ValueError("unknown token %r" % token)


def _expand_range(start, end, lo, hi):
    """Values of start-end in forward order, wrapping through hi back to lo."""
    if start <= end:
        return list(range(start, end + 1))
    return list(range(start, hi + 1)) + list(range(lo, end + 1))


def _parse_element(token, lo, hi, names):
    """Parse one comma-list element into a list of matching values."""
    if not token:
        raise ValueError("empty list element")

    step = 1
    if "/" in token:
        base, sep, step_str = token.partition("/")
        if "/" in step_str:
            raise ValueError("malformed step %r" % token)
        if not step_str.isdigit() or int(step_str) <= 0:
            raise ValueError("step must be a positive integer: %r" % token)
        step = int(step_str)
        if base != "*" and "-" not in base:
            raise ValueError("only * or a range may be stepped: %r" % token)
        token = base

    if token == "*":
        ordered = list(range(lo, hi + 1))
    elif "-" in token:
        parts = token.split("-")
        if len(parts) != 2:
            raise ValueError("malformed range %r" % token)
        start = _parse_value(parts[0], lo, hi, names)
        end = _parse_value(parts[1], lo, hi, names)
        ordered = _expand_range(start, end, lo, hi)
    else:
        ordered = [_parse_value(token, lo, hi, names)]

    return ordered[::step]


def _parse_field(field, lo, hi, names):
    """Parse a whole field into a set of matching values."""
    values = set()
    for element in field.split(","):
        values.update(_parse_element(element, lo, hi, names))
    return values


def _parse(expr):
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError("expected 5 fields, got %d" % len(fields))

    parsed = []
    for i, (field, (lo, hi, names)) in enumerate(zip(fields, _FIELDS)):
        if i == 2 and field == "L":
            parsed.append(_LAST_DAY)
        else:
            parsed.append(_parse_field(field, lo, hi, names))
    return parsed


def _day_matches(date, dom, dows):
    if dom is _LAST_DAY:
        dom_ok = date.day == calendar.monthrange(date.year, date.month)[1]
    else:
        dom_ok = date.day in dom
    # cron dow: 0 = Sunday; datetime.weekday(): 0 = Monday
    dow = (date.weekday() + 1) % 7
    return dom_ok and dow in dows


def next_fire(expr: str, after: datetime) -> datetime:
    """Return the earliest minute-aligned datetime strictly after `after`
    matching the 5-field cron expression `expr`. Raises ValueError on any
    malformed expression."""
    minutes, hours, dom, months, dows = _parse(expr)
    minutes = sorted(minutes)
    hours = sorted(hours)

    start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = start.date()

    # ~400 years covers every reachable schedule (e.g. Feb 29 on a given
    # weekday); anything still unmatched is genuinely impossible.
    limit = day + timedelta(days=366 * 400)
    while day < limit:
        if day.month not in months:
            # jump to the 1st of the next month
            if day.month == 12:
                day = day.replace(year=day.year + 1, month=1, day=1)
            else:
                day = day.replace(month=day.month + 1, day=1)
            continue
        if _day_matches(day, dom, dows):
            if day == start.date():
                for h in hours:
                    if h < start.hour:
                        continue
                    for m in minutes:
                        if h == start.hour and m < start.minute:
                            continue
                        return datetime(day.year, day.month, day.day, h, m)
            else:
                return datetime(day.year, day.month, day.day, hours[0], minutes[0])
        day += timedelta(days=1)
    raise ValueError("expression %r never matches" % expr)
