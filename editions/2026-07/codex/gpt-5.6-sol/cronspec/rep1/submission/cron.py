"""A small five-field cron scheduler using naive UTC datetimes."""

import calendar as _calendar
import datetime as _datetime

__all__ = ["next_fire"]


_MONTH_NAMES = {
    name: number
    for number, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"),
        1,
    )
}
_DOW_NAMES = {
    name: number
    for number, name in enumerate(("sun", "mon", "tue", "wed", "thu", "fri", "sat"))
}


def _value(token, low, high, names):
    if token in names:
        return names[token]
    if not token.isascii() or not token.isdigit():
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
    if not text or text == "L" or "L" in text:
        raise ValueError("invalid use of L")

    result = set()
    for item in text.split(","):
        if not item:
            raise ValueError("empty list element")
        pieces = item.split("/")
        if len(pieces) > 2:
            raise ValueError("invalid step")
        base = pieces[0]
        step = 1
        if len(pieces) == 2:
            if not pieces[1].isascii() or not pieces[1].isdigit():
                raise ValueError("invalid step")
            step = int(pieces[1])
            if step <= 0 or (base != "*" and "-" not in base):
                raise ValueError("invalid step")

        if base == "*":
            values = list(range(low, high + 1))
        else:
            endpoints = base.split("-")
            if len(endpoints) == 1:
                values = [_value(base, low, high, names)]
            elif len(endpoints) == 2 and all(endpoints):
                start = _value(endpoints[0], low, high, names)
                end = _value(endpoints[1], low, high, names)
                values = _range_values(start, end, low, high)
            else:
                raise ValueError("invalid range")
        result.update(values[::step])
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
        day = None
    else:
        day = _parse_field(fields[2], 1, 31)
    month = _parse_field(fields[3], 1, 12, _MONTH_NAMES)
    dow = _parse_field(fields[4], 0, 6, _DOW_NAMES)
    return minute, hour, day, month, dow


def _day_matches(date, day_values, dow_values):
    last = _calendar.monthrange(date.year, date.month)[1]
    dom_match = date.day == last if day_values is None else date.day in day_values
    # datetime.weekday is Monday=0; the expression uses Sunday=0.
    dow_match = (date.weekday() + 1) % 7 in dow_values
    return dom_match and dow_match


def next_fire(expr: str, after: _datetime.datetime) -> _datetime.datetime:
    """Return the earliest minute boundary strictly after *after* matching *expr*."""
    minutes, hours, days, months, dows = _parse(expr)
    if not isinstance(after, _datetime.datetime) or after.tzinfo is not None:
        raise ValueError("after must be a timezone-naive datetime")

    dom_unrestricted = expr.split()[2] == "*"
    dow_unrestricted = expr.split()[4] == "*"
    effective_days = frozenset(range(1, 32)) if dom_unrestricted else days
    effective_dows = frozenset(range(7)) if dow_unrestricted else dows

    try:
        boundary = after.replace(second=0, microsecond=0) + _datetime.timedelta(minutes=1)
    except OverflowError as exc:
        raise ValueError("no representable future firing time") from exc

    date = boundary.date()
    sorted_hours = sorted(hours)
    sorted_minutes = sorted(minutes)
    while True:
        if date.month in months and _day_matches(date, effective_days, effective_dows):
            for hour in sorted_hours:
                for minute in sorted_minutes:
                    candidate = _datetime.datetime(date.year, date.month, date.day, hour, minute)
                    if candidate >= boundary:
                        return candidate
        if date == _datetime.date.max:
            raise ValueError("no representable future firing time")
        date += _datetime.timedelta(days=1)
