"""A small, strict five-field cron scheduler."""

import calendar as _calendar
import datetime as _datetime
import re as _re

__all__ = ["next_fire"]

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def _value(text, low, high, names):
    """Parse one numeric or named field value, rejecting all other syntax."""
    if text in names:
        return names[text]
    if not _re.fullmatch(r"\d+", text):
        raise ValueError("invalid cron value")
    value = int(text)
    if not low <= value <= high:
        raise ValueError("cron value outside its domain")
    return value


def _range_values(start, end, low, high):
    if start <= end:
        return list(range(start, end + 1))
    return list(range(start, high + 1)) + list(range(low, end + 1))


def _field(text, low, high, names=None):
    """Return a field's allowed values and whether it is a bare wildcard."""
    names = {} if names is None else names
    unrestricted = text == "*"
    values = set()
    # split deliberately retains empty list elements so they can be rejected.
    for part in text.split(","):
        if not part:
            raise ValueError("empty cron list element")
        if part.count("/") > 1:
            raise ValueError("invalid cron step")
        if "/" in part:
            base, stride_text = part.split("/")
            if not _re.fullmatch(r"\d+", stride_text) or int(stride_text) <= 0:
                raise ValueError("invalid cron step")
            stride = int(stride_text)
            if base == "*":
                ordered = list(range(low, high + 1))
            else:
                match = _re.fullmatch(r"(.+)-(.+)", base)
                if not match:
                    raise ValueError("a step requires * or a range")
                start = _value(match.group(1), low, high, names)
                end = _value(match.group(2), low, high, names)
                ordered = _range_values(start, end, low, high)
            values.update(ordered[::stride])
            continue
        if part == "*":
            values.update(range(low, high + 1))
            continue
        match = _re.fullmatch(r"(.+)-(.+)", part)
        if match:
            start = _value(match.group(1), low, high, names)
            end = _value(match.group(2), low, high, names)
            values.update(_range_values(start, end, low, high))
            continue
        values.add(_value(part, low, high, names))
    return values, unrestricted


def _matches_day(day, dom_values, dom_last, dom_unrestricted, dow_values, dow_unrestricted):
    last_day = _calendar.monthrange(day.year, day.month)[1]
    dom_match = day.day == last_day if dom_last else day.day in dom_values
    # Python uses Monday=0; cron uses Sunday=0.
    dow_match = ((day.weekday() + 1) % 7) in dow_values
    if dom_unrestricted:
        return True if dow_unrestricted else dow_match
    if dow_unrestricted:
        return dom_match
    return dom_match and dow_match


def next_fire(expr: str, after: _datetime.datetime) -> _datetime.datetime:
    """Return the first minute strictly after *after* that matches *expr*."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError("cron expression must contain exactly five fields")

    minute_values, _ = _field(fields[0], 0, 59)
    hour_values, _ = _field(fields[1], 0, 23)
    if fields[2] == "L":
        dom_values, dom_last, dom_unrestricted = set(), True, False
    else:
        # L is intentionally not understood by _field, rejecting all combinations.
        dom_values, dom_unrestricted = _field(fields[2], 1, 31)
        dom_last = False
    month_values, _ = _field(fields[3], 1, 12, _MONTHS)
    dow_values, dow_unrestricted = _field(fields[4], 0, 6, _DAYS)

    candidate = after.replace(second=0, microsecond=0) + _datetime.timedelta(minutes=1)
    minutes = sorted(minute_values)
    hours = sorted(hour_values)
    day = candidate.date()
    while True:
        if day.month in month_values and _matches_day(
            day, dom_values, dom_last, dom_unrestricted, dow_values, dow_unrestricted
        ):
            for hour in hours:
                for minute in minutes:
                    result = _datetime.datetime(day.year, day.month, day.day, hour, minute)
                    if result >= candidate:
                        return result
        day += _datetime.timedelta(days=1)
