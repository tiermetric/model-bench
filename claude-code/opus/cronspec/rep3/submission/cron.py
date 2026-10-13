"""A cron-like scheduler.

Public API::

    next_fire(expr: str, after: datetime) -> datetime

Returns the earliest datetime strictly after ``after`` that matches the
five-field cron expression ``expr``.  All datetimes are tz-naive UTC wall
clock; there is no DST and no timezone math.  Results always have
``second == 0`` and ``microsecond == 0``.

This dialect deviates from vixie cron in three ways:

1. day-of-month and day-of-week INTERSECT when both are restricted
   (vixie cron takes the union),
2. a step is anchored at the range start (``5-30/10`` -> 5, 15, 25),
3. domains are strict -- day-of-week ``7`` is an error, names are lowercase.

and adds four hardening rules: wrapping ranges (``50-10``), steps counted
through the wrap (``50-10/5``), ``L`` for the last day of the month, and
month-length/leap-year-aware forward search.
"""

from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta

__all__ = ["next_fire"]


_MONTH_NAMES = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_DOW_NAMES = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}

_DIGITS = re.compile(r"\A[0-9]+\Z")

# How far forward next_fire is willing to search before declaring an
# expression unsatisfiable (e.g. "0 0 30 feb *").
_MAX_SEARCH_YEARS = 200


class _FieldDef:
    """Static description of one cron field."""

    __slots__ = ("label", "lo", "hi", "names")

    def __init__(self, label, lo, hi, names=None):
        self.label = label
        self.lo = lo
        self.hi = hi
        self.names = names or {}


_MINUTE = _FieldDef("minute", 0, 59)
_HOUR = _FieldDef("hour", 0, 23)
_DOM = _FieldDef("day-of-month", 1, 31)
_MONTH = _FieldDef("month", 1, 12, _MONTH_NAMES)
_DOW = _FieldDef("day-of-week", 0, 6, _DOW_NAMES)


class _Spec:
    """A parsed cron expression."""

    __slots__ = (
        "minutes",
        "hours",
        "doms",
        "months",
        "dows",
        "dom_restricted",
        "dow_restricted",
        "dom_last",
    )

    def __init__(self):
        self.minutes = ()
        self.hours = ()
        self.doms = frozenset()
        self.months = frozenset()
        self.dows = frozenset()
        self.dom_restricted = False
        self.dow_restricted = False
        self.dom_last = False


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------


def _parse_value(token, fdef):
    """Parse a single number or lowercase name for ``fdef``."""
    if not token:
        raise ValueError("empty value in %s field" % fdef.label)
    if _DIGITS.match(token):
        value = int(token)
    elif token in fdef.names:
        value = fdef.names[token]
    else:
        raise ValueError(
            "invalid value %r in %s field" % (token, fdef.label)
        )
    if value < fdef.lo or value > fdef.hi:
        raise ValueError(
            "value %r out of range %d-%d in %s field"
            % (token, fdef.lo, fdef.hi, fdef.label)
        )
    return value


def _range_order(start, end, fdef):
    """Values of ``start-end`` in forward order, wrapping if start > end."""
    if start <= end:
        return list(range(start, end + 1))
    # Rule 4: wrap through the field maximum back down to ``end``.
    return list(range(start, fdef.hi + 1)) + list(range(fdef.lo, end + 1))


def _parse_element(element, fdef):
    """Parse one comma-list element into a set of values."""
    if not element:
        raise ValueError("empty element in %s field" % fdef.label)

    base = element
    step = 1
    if "/" in element:
        parts = element.split("/")
        if len(parts) != 2:
            raise ValueError(
                "malformed step %r in %s field" % (element, fdef.label)
            )
        base, step_text = parts
        if not _DIGITS.match(step_text or ""):
            raise ValueError(
                "invalid step %r in %s field" % (element, fdef.label)
            )
        step = int(step_text)
        if step <= 0:
            raise ValueError(
                "step must be positive in %s field: %r" % (fdef.label, element)
            )
        if base != "*" and "-" not in base:
            # Deviation: only "*" or an explicit range may carry a step.
            raise ValueError(
                "step requires '*' or a range in %s field: %r"
                % (fdef.label, element)
            )

    if base == "*":
        ordered = list(range(fdef.lo, fdef.hi + 1))
    elif "-" in base:
        bounds = base.split("-")
        if len(bounds) != 2:
            raise ValueError(
                "malformed range %r in %s field" % (element, fdef.label)
            )
        start = _parse_value(bounds[0], fdef)
        end = _parse_value(bounds[1], fdef)
        ordered = _range_order(start, end, fdef)
    else:
        ordered = [_parse_value(base, fdef)]

    # Deviation 2 / Rule 5: anchor the stride at the range start and count
    # through the wrap.
    return set(ordered[::step])


def _parse_field(field, fdef):
    """Parse a whole field into a sorted tuple of values."""
    values = set()
    for element in field.split(","):
        values |= _parse_element(element, fdef)
    if not values:  # pragma: no cover - unreachable, kept as a guard
        raise ValueError("no values in %s field" % fdef.label)
    return tuple(sorted(values))


def _parse(expr):
    """Parse a five-field cron expression into a :class:`_Spec`."""
    if not isinstance(expr, str):
        raise ValueError("cron expression must be a string")

    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(
            "cron expression must have exactly 5 fields, got %d" % len(fields)
        )

    minute_f, hour_f, dom_f, month_f, dow_f = fields

    spec = _Spec()
    spec.minutes = _parse_field(minute_f, _MINUTE)
    spec.hours = _parse_field(hour_f, _HOUR)

    # Rule 6: "L" is the whole day-of-month field or nothing at all.
    for raw, fdef in ((dom_f, _DOM), (month_f, _MONTH), (dow_f, _DOW)):
        if "L" in raw and not (fdef is _DOM and raw == "L"):
            raise ValueError(
                "'L' is only valid as the entire day-of-month field: %r" % raw
            )

    if dom_f == "L":
        spec.dom_last = True
        spec.doms = frozenset()
    else:
        spec.doms = frozenset(_parse_field(dom_f, _DOM))

    spec.months = frozenset(_parse_field(month_f, _MONTH))
    spec.dows = frozenset(_parse_field(dow_f, _DOW))

    # Deviation 1: only a bare "*" is unrestricted.
    spec.dom_restricted = dom_f != "*"
    spec.dow_restricted = dow_f != "*"
    return spec


# --------------------------------------------------------------------------
# matching / search
# --------------------------------------------------------------------------


def _day_matches(spec, moment):
    """Deviation 1: intersect day-of-month and day-of-week when both bind."""
    if spec.dom_restricted:
        if spec.dom_last:
            last = calendar.monthrange(moment.year, moment.month)[1]
            if moment.day != last:
                return False
        elif moment.day not in spec.doms:
            return False
    if spec.dow_restricted:
        # Python: Monday==0..Sunday==6.  Cron: Sunday==0..Saturday==6.
        if moment.isoweekday() % 7 not in spec.dows:
            return False
    return True


def _next_ge(values, current):
    """Smallest value in sorted ``values`` that is >= ``current``, or None."""
    for value in values:
        if value >= current:
            return value
    return None


def _start_of_next_day(moment):
    return datetime(moment.year, moment.month, moment.day) + timedelta(days=1)


def _start_of_next_month(moment):
    if moment.month == 12:
        return datetime(moment.year + 1, 1, 1)
    return datetime(moment.year, moment.month + 1, 1)


def _start_of_next_hour(moment):
    return moment.replace(minute=0) + timedelta(hours=1)


def next_fire(expr, after):
    """Return the first datetime strictly after ``after`` matching ``expr``.

    ``expr`` is a five-field cron expression; ``after`` is a tz-naive UTC
    :class:`datetime`.  Raises :class:`ValueError` for a malformed expression
    or one that can never fire.
    """
    spec = _parse(expr)

    if not isinstance(after, datetime):
        raise TypeError("after must be a datetime")

    try:
        current = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    except OverflowError:
        raise ValueError("no matching time for %r after %r" % (expr, after))

    limit_year = current.year + _MAX_SEARCH_YEARS

    while current.year <= limit_year:
        try:
            if current.month not in spec.months:
                current = _start_of_next_month(current)
                continue

            if not _day_matches(spec, current):
                current = _start_of_next_day(current)
                continue

            hour = _next_ge(spec.hours, current.hour)
            if hour is None:
                current = _start_of_next_day(current)
                continue
            if hour != current.hour:
                current = current.replace(hour=hour, minute=0)

            minute = _next_ge(spec.minutes, current.minute)
            if minute is None:
                current = _start_of_next_hour(current)
                continue
            return current.replace(minute=minute)
        except OverflowError:
            break

    raise ValueError("no matching time for %r after %r" % (expr, after))
