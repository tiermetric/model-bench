"""A small, strict five-field cron scheduler."""

from datetime import datetime as _datetime, timedelta as _timedelta

__all__ = ["next_fire"]


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_WEEKDAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4,
             "fri": 5, "sat": 6}


def _value(token, low, high, names):
    """Parse one numeric or named field value, enforcing its domain."""
    if token in names:
        value = names[token]
    elif token.isascii() and token.isdecimal():
        value = int(token)
    else:
        raise ValueError("invalid cron value: {!r}".format(token))
    if not low <= value <= high:
        raise ValueError("cron value outside its field domain: {!r}".format(token))
    return value


def _part_values(part, low, high, names):
    if part.count("/"):
        if part.count("/") != 1:
            raise ValueError("invalid cron step")
        base, stride_text = part.split("/")
        if not (stride_text.isascii() and stride_text.isdecimal() and int(stride_text) > 0):
            raise ValueError("cron step must be a positive integer")
        stride = int(stride_text)
        if base == "*":
            start, end = low, high
        else:
            if base.count("-") != 1:
                raise ValueError("a cron step requires * or a range")
            left, right = base.split("-")
            start = _value(left, low, high, names)
            end = _value(right, low, high, names)
            if start > end:
                raise ValueError("cron range starts after it ends")
        return set(range(start, end + 1, stride))

    if part == "*":
        return set(range(low, high + 1))
    if part.count("-"):
        if part.count("-") != 1:
            raise ValueError("invalid cron range")
        left, right = part.split("-")
        start = _value(left, low, high, names)
        end = _value(right, low, high, names)
        if start > end:
            raise ValueError("cron range starts after it ends")
        return set(range(start, end + 1))
    return {_value(part, low, high, names)}


def _field(text, low, high, names):
    """Return allowed values and whether this field is a bare wildcard."""
    if text == "*":
        return set(range(low, high + 1)), True
    parts = text.split(",")
    if any(part == "" for part in parts):
        raise ValueError("empty cron list element")
    values = set()
    for part in parts:
        values.update(_part_values(part, low, high, names))
    return values, False


def next_fire(expr: str, after: _datetime) -> _datetime:
    """Return the first minute after *after* matching a strict cron expression."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError("cron expression must have exactly five fields")

    minutes, _ = _field(fields[0], 0, 59, {})
    hours, _ = _field(fields[1], 0, 23, {})
    days, days_unrestricted = _field(fields[2], 1, 31, {})
    months, _ = _field(fields[3], 1, 12, _MONTHS)
    weekdays, weekdays_unrestricted = _field(fields[4], 0, 6, _WEEKDAYS)

    # The first possible firing time is always the following minute, even when
    # ``after`` itself falls precisely on a minute boundary.
    candidate = after.replace(second=0, microsecond=0) + _timedelta(minutes=1)
    date = candidate.date()
    ordered_hours = sorted(hours)
    ordered_minutes = sorted(minutes)

    while True:
        # datetime.weekday() is Monday=0; cron's Sunday=0 convention differs.
        cron_weekday = (date.weekday() + 1) % 7
        dom_matches = date.day in days
        dow_matches = cron_weekday in weekdays
        if date.month in months and (
            (days_unrestricted and weekdays_unrestricted)
            or (days_unrestricted and dow_matches)
            or (weekdays_unrestricted and dom_matches)
            or (dom_matches and dow_matches)
        ):
            for hour in ordered_hours:
                for minute in ordered_minutes:
                    result = _datetime(date.year, date.month, date.day, hour, minute)
                    if result >= candidate:
                        return result
        date += _timedelta(days=1)
