"""A small cron expression parser and next-fire calculator."""

import datetime as _datetime
import re as _re


__all__ = ["next_fire"]


_MONTHS = {
    name: number
    for number, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"),
        1,
    )
}
_DAYS = {
    name: number
    for number, name in enumerate(("sun", "mon", "tue", "wed", "thu", "fri", "sat"))
}


def _parse_value(token, minimum, maximum, names):
    if names is not None and token in names:
        return names[token]
    if not token or not token.isdigit():
        raise ValueError(f"invalid cron value: {token!r}")
    value = int(token)
    if not minimum <= value <= maximum:
        raise ValueError(f"cron value out of range: {token!r}")
    return value


def _parse_field(field, minimum, maximum, names):
    if not field:
        raise ValueError("empty cron field")

    unrestricted = field == "*"
    values = set()
    for item in field.split(","):
        if not item:
            raise ValueError("empty cron list element")

        pieces = item.split("/")
        if len(pieces) > 2:
            raise ValueError("invalid cron step")
        base = pieces[0]
        if len(pieces) == 2:
            step_text = pieces[1]
            if not step_text or not step_text.isdigit() or int(step_text) <= 0:
                raise ValueError("cron step must be positive")
            step = int(step_text)
            if base == "*":
                start, end = minimum, maximum
            else:
                range_parts = base.split("-")
                if len(range_parts) != 2 or not range_parts[0] or not range_parts[1]:
                    raise ValueError("step must follow * or a range")
                start = _parse_value(range_parts[0], minimum, maximum, names)
                end = _parse_value(range_parts[1], minimum, maximum, names)
                if start > end:
                    raise ValueError("cron range start exceeds end")
            values.update(range(start, end + 1, step))
            continue

        if base == "*":
            values.update(range(minimum, maximum + 1))
            continue
        if "/" in item:
            raise ValueError("invalid cron item")
        range_parts = base.split("-")
        if len(range_parts) == 1:
            values.add(_parse_value(base, minimum, maximum, names))
        elif len(range_parts) == 2 and range_parts[0] and range_parts[1]:
            start = _parse_value(range_parts[0], minimum, maximum, names)
            end = _parse_value(range_parts[1], minimum, maximum, names)
            if start > end:
                raise ValueError("cron range start exceeds end")
            values.update(range(start, end + 1))
        else:
            raise ValueError(f"invalid cron item: {item!r}")

    if not values:
        raise ValueError("cron field has no values")
    return frozenset(values), unrestricted


def _parse_expression(expr):
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError("cron expression must contain exactly five fields")
    specs = (
        (0, 59, None),
        (0, 23, None),
        (1, 31, None),
        (1, 12, _MONTHS),
        (0, 6, _DAYS),
    )
    return tuple(
        _parse_field(field, minimum, maximum, names)
        for field, (minimum, maximum, names) in zip(fields, specs)
    )


def _cron_weekday(day):
    # datetime.weekday(): Monday=0; cron: Sunday=0.
    return (day.weekday() + 1) % 7


def next_fire(expr: str, after: _datetime.datetime) -> _datetime.datetime:
    """Return the first cron minute strictly after *after*."""
    (minutes, _), (hours, _), (month_days, dom_unrestricted), \
        (months, _), (weekdays, dow_unrestricted) = _parse_expression(expr)

    candidate = after.replace(second=0, microsecond=0) + _datetime.timedelta(minutes=1)
    day = candidate.date()
    while True:
        if day.month in months:
            dom_matches = day.day in month_days
            dow_matches = _cron_weekday(day) in weekdays
            if (dom_unrestricted or dom_matches) and (dow_unrestricted or dow_matches):
                first_hour = candidate.hour if day == candidate.date() else 0
                for hour in sorted(hours):
                    if hour < first_hour:
                        continue
                    first_minute = candidate.minute if day == candidate.date() and hour == first_hour else 0
                    for minute in sorted(minutes):
                        if minute < first_minute:
                            continue
                        return _datetime.datetime.combine(
                            day, _datetime.time(hour, minute)
                        )
        day += _datetime.timedelta(days=1)

