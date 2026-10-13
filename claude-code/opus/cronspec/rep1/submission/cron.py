"""A cron-like scheduler with three deviations from standard cron.

Public API::

    next_fire(expr: str, after: datetime) -> datetime

All datetimes are tz-naive wall-clock UTC; there is no DST and no timezone
math.  The returned datetime is the earliest one strictly after ``after`` that
matches ``expr``, with ``second == 0`` and ``microsecond == 0``.

Deviations from standard (vixie) cron:

1. day-of-month and day-of-week are INTERSECTED when both are restricted
   (standard cron unions them).  Only a bare ``*`` counts as unrestricted.
2. A step is anchored at the RANGE START: ``A-B/s`` yields ``A, A+s, A+2s, …``
   rather than the multiples of ``s`` inside ``[A, B]``.
3. Domains are strict: day-of-week is ``0-6`` only (``7`` is an error), names
   are lowercase only, and any malformed token is a ``ValueError``.

Hardening rules:

4. ``A-B`` with ``A > B`` wraps through the field maximum back down to ``B``.
5. A step over a wrapped range is anchored at ``A`` and counts through the wrap.
6. The day-of-month field may be exactly ``L`` — the last day of the month.
7. Month lengths and leap years are respected; a day-of-month that does not
   exist in a month is never clamped, the month simply produces no match.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta

__all__ = ["next_fire"]

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_DOW_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}

# How far forward next_fire will search before declaring an expression
# unsatisfiable (e.g. "0 0 30 feb *").  Leap-year interactions can push a real
# match decades out, so the horizon is deliberately generous.
_MAX_MONTHS = 200 * 12


class _FieldSpec:
    """Domain description for one cron field."""

    __slots__ = ("name", "lo", "hi", "names", "allow_last")

    def __init__(self, name, lo, hi, names=None, allow_last=False):
        self.name = name
        self.lo = lo
        self.hi = hi
        self.names = names or {}
        self.allow_last = allow_last


_MINUTE = _FieldSpec("minute", 0, 59)
_HOUR = _FieldSpec("hour", 0, 23)
_DOM = _FieldSpec("day-of-month", 1, 31, allow_last=True)
_MONTH = _FieldSpec("month", 1, 12, _MONTH_NAMES)
_DOW = _FieldSpec("day-of-week", 0, 6, _DOW_NAMES)


def _is_number(token: str) -> bool:
    """True for a plain run of ASCII digits (rejects '', '²', '+1', ' 1')."""
    return token.isascii() and token.isdigit()


def _parse_value(token: str, spec: _FieldSpec) -> int:
    if not token:
        raise ValueError(f"empty value in {spec.name} field")
    if token in spec.names:
        return spec.names[token]
    if not _is_number(token):
        raise ValueError(f"unknown token {token!r} in {spec.name} field")
    value = int(token)
    if value < spec.lo or value > spec.hi:
        raise ValueError(
            f"{spec.name} value {value} out of domain {spec.lo}-{spec.hi}"
        )
    return value


def _range_sequence(start: int, end: int, spec: _FieldSpec) -> list[int]:
    """Values of ``start-end`` in forward order, wrapping when start > end."""
    if start <= end:
        return list(range(start, end + 1))
    # Rule 4: wrap through the field maximum back down to ``end``.
    return list(range(start, spec.hi + 1)) + list(range(spec.lo, end + 1))


def _parse_step(text: str, spec: _FieldSpec) -> int:
    if not _is_number(text):
        raise ValueError(f"invalid step {text!r} in {spec.name} field")
    step = int(text)
    if step <= 0:
        raise ValueError(f"step must be positive in {spec.name} field")
    return step


def _parse_element(element: str, spec: _FieldSpec) -> list[int]:
    if not element:
        raise ValueError(f"empty list element in {spec.name} field")

    base = element
    step = 1
    if "/" in element:
        parts = element.split("/")
        if len(parts) != 2:
            raise ValueError(f"malformed step in {spec.name} field: {element!r}")
        base, step_text = parts
        step = _parse_step(step_text, spec)

    if base == "*":
        sequence = list(range(spec.lo, spec.hi + 1))
    elif "-" in base:
        bounds = base.split("-")
        if len(bounds) != 2:
            raise ValueError(f"malformed range in {spec.name} field: {base!r}")
        start = _parse_value(bounds[0], spec)
        end = _parse_value(bounds[1], spec)
        sequence = _range_sequence(start, end, spec)
    else:
        if step != 1:
            raise ValueError(
                f"step not allowed on a single value in {spec.name} field: "
                f"{element!r}"
            )
        sequence = [_parse_value(base, spec)]

    # Deviation 2 / Rule 5: anchor the stride at the start of the sequence.
    return sequence[::step]


def _parse_field(text: str, spec: _FieldSpec) -> set[int]:
    values: set[int] = set()
    for element in text.split(","):
        values.update(_parse_element(element, spec))
    return values


class _Schedule:
    """A parsed cron expression."""

    __slots__ = ("minutes", "hours", "days", "day_last", "months",
                 "weekdays", "dom_restricted", "dow_restricted")

    def __init__(self, expr: str):
        if not isinstance(expr, str):
            raise ValueError("cron expression must be a string")
        fields = expr.split()
        if len(fields) != 5:
            raise ValueError(
                f"cron expression must have exactly 5 fields, got {len(fields)}"
            )
        minute, hour, dom, month, dow = fields

        self.minutes = sorted(_parse_field(minute, _MINUTE))
        self.hours = sorted(_parse_field(hour, _HOUR))

        # Rule 6: 'L' is legal only as the entire day-of-month field.
        if dom == "L":
            self.day_last = True
            self.days: set[int] = set()
        else:
            self.day_last = False
            self.days = _parse_field(dom, _DOM)

        self.months = _parse_field(month, _MONTH)
        self.weekdays = _parse_field(dow, _DOW)

        # Deviation 1: only a bare '*' is unrestricted.
        self.dom_restricted = dom != "*"
        self.dow_restricted = dow != "*"

    def _day_matches(self, year: int, month: int, day: int) -> bool:
        dom_ok = True
        if self.dom_restricted:
            if self.day_last:
                dom_ok = day == calendar.monthrange(year, month)[1]
            else:
                dom_ok = day in self.days

        dow_ok = True
        if self.dow_restricted:
            # Python: Monday == 0 … Sunday == 6.  Cron: Sunday == 0.
            cron_dow = (calendar.weekday(year, month, day) + 1) % 7
            dow_ok = cron_dow in self.weekdays

        if self.dom_restricted and self.dow_restricted:
            return dom_ok and dow_ok  # intersection, not union
        return dom_ok and dow_ok

    def next_after(self, after: datetime) -> datetime:
        # The first candidate is the next whole minute strictly after `after`.
        start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)

        year, month = start.year, start.month
        for _ in range(_MAX_MONTHS + 1):
            if month in self.months:
                days_in_month = calendar.monthrange(year, month)[1]
                first_day = 1
                if (year, month) == (start.year, start.month):
                    first_day = start.day
                for day in range(first_day, days_in_month + 1):
                    if not self._day_matches(year, month, day):
                        continue
                    same_day = (year, month, day) == (
                        start.year, start.month, start.day
                    )
                    for hour in self.hours:
                        if same_day and hour < start.hour:
                            continue
                        for minute in self.minutes:
                            if (same_day and hour == start.hour
                                    and minute < start.minute):
                                continue
                            return datetime(year, month, day, hour, minute)
            month += 1
            if month > 12:
                month = 1
                year += 1
            if year > datetime.max.year:
                break

        raise ValueError(
            "no matching time found within the search horizon; the expression "
            "may be unsatisfiable"
        )


def next_fire(expr: str, after: datetime) -> datetime:
    """Return the earliest datetime strictly after ``after`` matching ``expr``.

    Args:
        expr: a five-field cron expression (minute hour day-of-month month
            day-of-week).
        after: a tz-naive UTC datetime; seconds and microseconds are ignored
            beyond advancing to the next whole minute.

    Raises:
        ValueError: if ``expr`` is malformed, out of domain, or has no match
            within the search horizon.
    """
    if not isinstance(after, datetime):
        raise ValueError("after must be a datetime")
    return _Schedule(expr).next_after(after)
