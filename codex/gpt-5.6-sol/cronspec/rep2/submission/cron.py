"""A small, strict five-field cron scheduler."""

import calendar
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


def _field(text, low, high, names=None):
    names = names or {}
    if not text:
        raise ValueError("empty cron field")
    result = set()
    for item in text.split(","):
        if not item:
            raise ValueError("empty cron list element")
        if item.count("/") > 1:
            raise ValueError("invalid cron step")
        if "/" in item:
            base, step_text = item.split("/")
            if (not step_text.isascii() or not step_text.isdigit()
                    or int(step_text) <= 0):
                raise ValueError("invalid cron step")
            step = int(step_text)
            if base == "*":
                ordered = list(range(low, high + 1))
            elif base.count("-") == 1:
                first, last = base.split("-")
                ordered = _range_values(
                    _value(first, low, high, names),
                    _value(last, low, high, names),
                    low,
                    high,
                )
            else:
                raise ValueError("only a wildcard or range may have a step")
            result.update(ordered[::step])
        elif item == "*":
            result.update(range(low, high + 1))
        elif item.count("-") == 1:
            first, last = item.split("-")
            result.update(_range_values(
                _value(first, low, high, names),
                _value(last, low, high, names),
                low,
                high,
            ))
        else:
            result.add(_value(item, low, high, names))
    return frozenset(result)


def _parse(expr):
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError("cron expression must have exactly five fields")
    minute = _field(fields[0], 0, 59)
    hour = _field(fields[1], 0, 23)
    if fields[2] == "L":
        day = None
    else:
        if "L" in fields[2]:
            raise ValueError("L must be the whole day-of-month field")
        day = _field(fields[2], 1, 31)
    month = _field(fields[3], 1, 12, _MONTH_NAMES)
    dow = _field(fields[4], 0, 6, _DOW_NAMES)
    return minute, hour, day, month, dow, fields[2] == "*", fields[4] == "*"


def next_fire(expr: str, after: _datetime.datetime) -> _datetime.datetime:
    """Return the earliest minute boundary after *after* matching *expr*."""
    minute, hour, dom, month, dow, dom_any, dow_any = _parse(expr)
    try:
        candidate = after.replace(second=0, microsecond=0) + _datetime.timedelta(minutes=1)
        while True:
            if candidate.month in month and candidate.hour in hour and candidate.minute in minute:
                last = calendar.monthrange(candidate.year, candidate.month)[1]
                dom_match = candidate.day == last if dom is None else candidate.day in dom
                # datetime.weekday(): Monday=0; cron: Sunday=0.
                dow_match = ((candidate.weekday() + 1) % 7) in dow
                if ((dom_any and dow_any)
                        or (dom_any and dow_match)
                        or (dow_any and dom_match)
                        or (not dom_any and not dow_any and dom_match and dow_match)):
                    return candidate
            candidate += _datetime.timedelta(minutes=1)
    except (OverflowError, ValueError) as exc:
        raise ValueError("no matching datetime exists after the supplied value") from exc
