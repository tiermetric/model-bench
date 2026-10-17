"""A small five-field cron scheduler using the rules specified by this project."""

import bisect as _bisect
import datetime as _datetime
import re as _re

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
_INTEGER = _re.compile(r"[0-9]+")


def _value(token, lower, upper, names):
    if _INTEGER.fullmatch(token):
        value = int(token)
    elif token in names:
        value = names[token]
    else:
        raise ValueError("invalid cron value: {!r}".format(token))
    if not lower <= value <= upper:
        raise ValueError("cron value outside field domain: {!r}".format(token))
    return value


def _field(text, lower, upper, names=None):
    names = names or {}
    if not text:
        raise ValueError("empty cron field")
    values = set()
    for item in text.split(","):
        if not item:
            raise ValueError("empty cron list element")

        pieces = item.split("/")
        if len(pieces) > 2:
            raise ValueError("invalid step expression")
        base = pieces[0]
        step = 1
        if len(pieces) == 2:
            if not _INTEGER.fullmatch(pieces[1]) or int(pieces[1]) <= 0:
                raise ValueError("step must be a positive integer")
            step = int(pieces[1])
            if base != "*" and "-" not in base:
                raise ValueError("a step cannot follow a single value")

        if base == "*":
            start, end = lower, upper
        else:
            endpoints = base.split("-")
            if len(endpoints) == 1:
                start = end = _value(base, lower, upper, names)
            elif len(endpoints) == 2 and endpoints[0] and endpoints[1]:
                start = _value(endpoints[0], lower, upper, names)
                end = _value(endpoints[1], lower, upper, names)
                if start > end:
                    raise ValueError("range start exceeds range end")
            else:
                raise ValueError("invalid cron range")
        values.update(range(start, end + 1, step))
    return frozenset(values)


def _parse(expr):
    if not isinstance(expr, str):
        raise ValueError("cron expression must be a string")
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError("cron expression must contain exactly five fields")
    minute = _field(fields[0], 0, 59)
    hour = _field(fields[1], 0, 23)
    day = _field(fields[2], 1, 31)
    month = _field(fields[3], 1, 12, _MONTH_NAMES)
    weekday = _field(fields[4], 0, 6, _DOW_NAMES)
    return minute, hour, day, month, weekday, fields[2] == "*", fields[4] == "*"


def _cron_weekday(day):
    # datetime.weekday(): Monday=0; cron: Sunday=0.
    return (day.weekday() + 1) % 7


def next_fire(expr: str, after: _datetime.datetime) -> _datetime.datetime:
    """Return the first minute boundary strictly after *after* matching *expr*."""
    minutes, hours, days, months, weekdays, day_any, weekday_any = _parse(expr)
    if not isinstance(after, _datetime.datetime) or after.tzinfo is not None:
        raise ValueError("after must be a timezone-naive datetime")

    candidate = after.replace(second=0, microsecond=0)
    try:
        candidate += _datetime.timedelta(minutes=1)
    except OverflowError as error:
        raise ValueError("no representable future firing time") from error

    times = sorted((hour, minute) for hour in hours for minute in minutes)
    current_date = candidate.date()
    first_day = True

    while True:
        month_ok = current_date.month in months
        dom_ok = current_date.day in days
        dow_ok = _cron_weekday(current_date) in weekdays
        if day_any and weekday_any:
            day_ok = True
        elif day_any:
            day_ok = dow_ok
        elif weekday_any:
            day_ok = dom_ok
        else:
            day_ok = dom_ok and dow_ok

        if month_ok and day_ok:
            threshold = (candidate.hour, candidate.minute) if first_day else (-1, -1)
            index = _bisect.bisect_left(times, threshold)
            if index < len(times):
                hour, minute = times[index]
                return _datetime.datetime(
                    current_date.year, current_date.month, current_date.day, hour, minute
                )

        if current_date == _datetime.date.max:
            raise ValueError("no representable future firing time")
        current_date += _datetime.timedelta(days=1)
        first_day = False
