"""A small, strict five-field cron scheduler."""

from datetime import datetime as _datetime, timedelta as _timedelta
import calendar as _calendar
import re as _re

__all__ = ["next_fire"]

_MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
)}
_DAYS = {name: number for number, name in enumerate(
    ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
)}
_INTEGER = _re.compile(r"[0-9]+\Z")


def _value(token, low, high, names):
    if token in names:
        return names[token]
    if not _INTEGER.fullmatch(token):
        raise ValueError("invalid cron value")
    value = int(token)
    if not low <= value <= high:
        raise ValueError("cron value outside field domain")
    return value


def _range_values(start, end, low, high):
    if start <= end:
        return range(start, end + 1)
    return list(range(start, high + 1)) + list(range(low, end + 1))


def _parse_field(field, low, high, names=None):
    """Return (allowed values, is_bare_star)."""
    names = names or {}
    if field == "*":
        return frozenset(range(low, high + 1)), True
    if not field or field.startswith(",") or field.endswith(",") or ",," in field:
        raise ValueError("invalid cron list")

    allowed = set()
    for part in field.split(","):
        if part.count("/") > 1:
            raise ValueError("invalid cron step")
        if "/" in part:
            base, stride_text = part.split("/")
            if not _INTEGER.fullmatch(stride_text) or int(stride_text) <= 0:
                raise ValueError("invalid cron step")
            stride = int(stride_text)
            if base == "*":
                values = range(low, high + 1)
            else:
                if base.count("-") != 1:
                    raise ValueError("a step requires a range or *")
                left, right = base.split("-")
                if not left or not right:
                    raise ValueError("invalid cron range")
                start = _value(left, low, high, names)
                end = _value(right, low, high, names)
                values = _range_values(start, end, low, high)
            allowed.update(values[::stride])
            continue

        if part.count("-") == 1:
            left, right = part.split("-")
            if not left or not right:
                raise ValueError("invalid cron range")
            start = _value(left, low, high, names)
            end = _value(right, low, high, names)
            allowed.update(_range_values(start, end, low, high))
        elif "-" in part:
            raise ValueError("invalid cron range")
        else:
            allowed.add(_value(part, low, high, names))
    return frozenset(allowed), False


def _parse(expr):
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError("cron expression must contain exactly five fields")
    minute, hour, dom, month, dow = fields
    minutes, _ = _parse_field(minute, 0, 59)
    hours, _ = _parse_field(hour, 0, 23)
    months, _ = _parse_field(month, 1, 12, _MONTHS)
    dows, dow_star = _parse_field(dow, 0, 6, _DAYS)
    if dom == "L":
        dom_values, dom_star, last_day = frozenset(), False, True
    else:
        # The ordinary parser rejects L in every non-exact use, including lists.
        dom_values, dom_star = _parse_field(dom, 1, 31)
        last_day = False
    return minutes, hours, dom_values, dom_star, last_day, months, dows, dow_star


def _cron_dow(date):
    # datetime.weekday: Monday=0.  Cron's Sunday=0 convention differs.
    return (date.weekday() + 1) % 7


def _day_matches(date, dom_values, dom_star, last_day, dows, dow_star):
    dom_match = date.day == _calendar.monthrange(date.year, date.month)[1] if last_day else date.day in dom_values
    dow_match = _cron_dow(date) in dows
    if dom_star and dow_star:
        return True
    if dom_star:
        return dow_match
    if dow_star:
        return dom_match
    return dom_match and dow_match


def next_fire(expr: str, after: _datetime) -> _datetime:
    """Return the first minute strictly after *after* matching a cron expression."""
    minutes, hours, dom_values, dom_star, last_day, months, dows, dow_star = _parse(expr)
    candidate = after.replace(second=0, microsecond=0) + _timedelta(minutes=1)

    while True:
        if candidate.month in months and _day_matches(
            candidate, dom_values, dom_star, last_day, dows, dow_star
        ):
            for hour in sorted(hours):
                for minute in sorted(minutes):
                    result = candidate.replace(hour=hour, minute=minute)
                    if result >= candidate:
                        return result
        # Move to the next midnight; a whole date has now been considered.
        candidate = (candidate.replace(hour=0, minute=0) + _timedelta(days=1))
