"""A small five-field cron scheduler with the rules described by this project."""

import calendar as _calendar
import datetime as _datetime
import re as _re

__all__ = ["next_fire"]


_NUMBER = _re.compile(r"[0-9]+")
_MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"), 1
)}
_WEEKDAYS = {name: number for number, name in enumerate(
    ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
)}


def _value(token, low, high, names):
    if token in names:
        return names[token]
    if not _NUMBER.fullmatch(token):
        raise ValueError("invalid cron value")
    value = int(token)
    if not low <= value <= high:
        raise ValueError("cron value outside field domain")
    return value


def _range_values(start, end, low, high):
    if start <= end:
        return list(range(start, end + 1))
    return list(range(start, high + 1)) + list(range(low, end + 1))


def _parse_field(text, low, high, names=None):
    names = names or {}
    if not text:
        raise ValueError("empty cron field")
    result = set()
    for item in text.split(","):
        if not item:
            raise ValueError("empty cron list element")

        pieces = item.split("/")
        if len(pieces) > 2:
            raise ValueError("invalid step")
        base = pieces[0]
        step = None
        if len(pieces) == 2:
            if not _NUMBER.fullmatch(pieces[1]) or int(pieces[1]) <= 0:
                raise ValueError("step must be a positive integer")
            step = int(pieces[1])

        if base == "*":
            ordered = list(range(low, high + 1))
        else:
            endpoints = base.split("-")
            if len(endpoints) == 1:
                if step is not None:
                    raise ValueError("a single value cannot have a step")
                ordered = [_value(base, low, high, names)]
            elif len(endpoints) == 2 and all(endpoints):
                start = _value(endpoints[0], low, high, names)
                end = _value(endpoints[1], low, high, names)
                ordered = _range_values(start, end, low, high)
            else:
                raise ValueError("invalid range")

        result.update(ordered[::step] if step is not None else ordered)
    return frozenset(result)


def _parse(expr):
    if not isinstance(expr, str):
        raise ValueError("cron expression must be a string")
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError("cron expression must contain five fields")

    minute = _parse_field(fields[0], 0, 59)
    hour = _parse_field(fields[1], 0, 23)
    if fields[2] == "L":
        day_of_month = None
        last_day = True
    else:
        if "L" in fields[2]:
            raise ValueError("L must be the whole day-of-month field")
        day_of_month = _parse_field(fields[2], 1, 31)
        last_day = False
    month = _parse_field(fields[3], 1, 12, _MONTHS)
    day_of_week = _parse_field(fields[4], 0, 6, _WEEKDAYS)
    return (minute, hour, day_of_month, month, day_of_week, last_day,
            fields[2] == "*", fields[4] == "*")


def _day_matches(day, dom, dow, last_day, dom_unrestricted, dow_unrestricted):
    final_day = _calendar.monthrange(day.year, day.month)[1]
    dom_match = day.day == final_day if last_day else day.day in dom
    # datetime.weekday() uses Monday=0; this expression uses Sunday=0.
    dow_match = (day.weekday() + 1) % 7 in dow
    if dom_unrestricted and dow_unrestricted:
        return True
    if dom_unrestricted:
        return dow_match
    if dow_unrestricted:
        return dom_match
    return dom_match and dow_match


def next_fire(expr: str, after: _datetime.datetime) -> _datetime.datetime:
    """Return the first minute boundary strictly after *after* matching *expr*."""
    minute, hour, dom, months, dow, last_day, dom_star, dow_star = _parse(expr)
    if not isinstance(after, _datetime.datetime) or after.tzinfo is not None:
        raise ValueError("after must be a timezone-naive datetime")

    try:
        candidate = after.replace(second=0, microsecond=0) + _datetime.timedelta(minutes=1)
    except OverflowError as exc:
        raise ValueError("no matching datetime in the supported range") from exc

    hours = sorted(hour)
    minutes = sorted(minute)
    day = candidate.date()
    while True:
        if day.month in months and _day_matches(
                day, dom, dow, last_day, dom_star, dow_star):
            for selected_hour in hours:
                for selected_minute in minutes:
                    result = _datetime.datetime(
                        day.year, day.month, day.day,
                        selected_hour, selected_minute)
                    if result >= candidate:
                        return result
        try:
            day += _datetime.timedelta(days=1)
        except OverflowError as exc:
            raise ValueError("no matching datetime in the supported range") from exc
