"""A small five-field cron scheduler using naive UTC datetimes."""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta

__all__ = ["next_fire"]


_NAMES = {
    "month": {name: number for number, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"), 1)},
    "dow": {name: number for number, name in enumerate(
        ("sun", "mon", "tue", "wed", "thu", "fri", "sat"))},
}
_INTEGER = re.compile(r"[0-9]+")


def _value(token: str, low: int, high: int, names: dict[str, int]) -> int:
    if token in names:
        result = names[token]
    elif _INTEGER.fullmatch(token):
        result = int(token)
    else:
        raise ValueError("invalid cron value")
    if not low <= result <= high:
        raise ValueError("cron value outside field domain")
    return result


def _range_values(start: int, end: int, low: int, high: int) -> list[int]:
    if start <= end:
        return list(range(start, end + 1))
    return list(range(start, high + 1)) + list(range(low, end + 1))


def _field(text: str, low: int, high: int,
           names: dict[str, int] | None = None) -> tuple[frozenset[int], bool]:
    names = names or {}
    unrestricted = text == "*"
    if not text or any(part == "" for part in text.split(",")):
        raise ValueError("empty cron list element")

    selected: set[int] = set()
    for part in text.split(","):
        pieces = part.split("/")
        if len(pieces) > 2:
            raise ValueError("invalid step")
        base = pieces[0]
        step = 1
        if len(pieces) == 2:
            if not _INTEGER.fullmatch(pieces[1]) or int(pieces[1]) <= 0:
                raise ValueError("step must be a positive integer")
            step = int(pieces[1])
            if base != "*" and base.count("-") != 1:
                raise ValueError("only a wildcard or range may be stepped")

        if base == "*":
            ordered = list(range(low, high + 1))
        elif base.count("-") == 1:
            start_text, end_text = base.split("-")
            start = _value(start_text, low, high, names)
            end = _value(end_text, low, high, names)
            ordered = _range_values(start, end, low, high)
        elif "-" in base:
            raise ValueError("invalid range")
        else:
            ordered = [_value(base, low, high, names)]
        selected.update(ordered[::step])
    return frozenset(selected), unrestricted


def _parse(expr: str):
    if not isinstance(expr, str):
        raise ValueError("expression must be a string")
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError("cron expression must have exactly five fields")

    minutes, _ = _field(fields[0], 0, 59)
    hours, _ = _field(fields[1], 0, 23)

    dom_text = fields[2]
    last_day = dom_text == "L"
    if "L" in dom_text:
        if not last_day:
            raise ValueError("L must be the whole day-of-month field")
        dom, dom_unrestricted = frozenset(), False
    else:
        dom, dom_unrestricted = _field(dom_text, 1, 31)

    months, _ = _field(fields[3], 1, 12, _NAMES["month"])
    dows, dow_unrestricted = _field(fields[4], 0, 6, _NAMES["dow"])
    return (minutes, hours, dom, months, dows, dom_unrestricted,
            dow_unrestricted, last_day)


def next_fire(expr: str, after: datetime) -> datetime:
    """Return the earliest minute strictly after *after* matching *expr*."""
    (minutes, hours, dom, months, dows, dom_free,
     dow_free, last_day) = _parse(expr)

    # A non-boundary time advances to its containing minute's next boundary;
    # a boundary also advances because the result must be strictly later.
    start = after.replace(second=0, microsecond=0)
    try:
        start += timedelta(minutes=1)
    except OverflowError as exc:
        raise ValueError("no later datetime is representable") from exc

    times = [(hour, minute) for hour in sorted(hours) for minute in sorted(minutes)]
    current = start.date()
    while True:
        if current.month in months:
            month_last = calendar.monthrange(current.year, current.month)[1]
            dom_match = current.day == month_last if last_day else current.day in dom
            cron_dow = (current.weekday() + 1) % 7
            dow_match = cron_dow in dows
            if ((dom_free and dow_free) or
                    (dom_free and dow_match) or
                    (dow_free and dom_match) or
                    (not dom_free and not dow_free and dom_match and dow_match)):
                for hour, minute in times:
                    candidate = datetime(current.year, current.month, current.day,
                                         hour, minute)
                    if candidate >= start:
                        return candidate
        try:
            current += timedelta(days=1)
        except OverflowError as exc:
            raise ValueError("no matching datetime is representable") from exc
