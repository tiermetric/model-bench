"""A small, UTC-naive cron expression scheduler."""

import datetime as _dt
import re as _re


__all__ = ["next_fire"]

_MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"), 1
)}
_DOWS = {name: number for number, name in enumerate(
    ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
)}
_FIELDS = ((0, 59, None), (0, 23, None), (1, 31, None),
           (1, 12, _MONTHS), (0, 6, _DOWS))
_TOKEN = _re.compile(r"^([^/]+)(?:/([0-9]+))?$")
_RANGE = _re.compile(r"^([^\-]+)-([^\-]+)$")


def _value(text, field):
    lo, hi, names = _FIELDS[field]
    if names is not None and text in names:
        return names[text]
    if not text.isdigit():
        raise ValueError("invalid cron value")
    value = int(text)
    if value < lo or value > hi:
        raise ValueError("cron value out of range")
    return value


def _range_values(start, end, lo, hi, step):
    if start <= end:
        sequence = range(start, end + 1)
    else:
        sequence = list(range(start, hi + 1)) + list(range(lo, end + 1))
    return set(list(sequence)[::step])


def _parse_field(text, field):
    lo, hi, _ = _FIELDS[field]
    if text == "L":
        if field != 2:
            raise ValueError("L is only valid for day-of-month")
        return None, False, True
    if field == 2 and "L" in text:
        raise ValueError("L must be the whole day-of-month field")

    unrestricted = text == "*"
    values = set()
    for part in text.split(","):
        if not part:
            raise ValueError("empty cron list item")
        match = _TOKEN.fullmatch(part)
        if match is None:
            raise ValueError("invalid cron field")
        body, step_text = match.groups()
        step = 1 if step_text is None else int(step_text)
        if step <= 0:
            raise ValueError("cron step must be positive")

        if body == "*":
            start, end = lo, hi
        else:
            range_match = _RANGE.fullmatch(body)
            if range_match is None:
                if step_text is not None:
                    raise ValueError("steps require * or a range")
                values.add(_value(body, field))
                continue
            start = _value(range_match.group(1), field)
            end = _value(range_match.group(2), field)
        values.update(_range_values(start, end, lo, hi, step))
    return values, unrestricted, False


def _parse(expr):
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError("cron expression must have five fields")
    parsed = [_parse_field(text, index) for index, text in enumerate(fields)]
    return parsed


def _days_in_month(year, month):
    if year == 9999 and month == 12:
        return 31
    if month == 12:
        next_month = _dt.date(year + 1, 1, 1)
    else:
        next_month = _dt.date(year, month + 1, 1)
    return (next_month - _dt.timedelta(days=1)).day


def next_fire(expr: str, after: _dt.datetime) -> _dt.datetime:
    """Return the first minute strictly later than *after* matching *expr*."""
    parsed = _parse(expr)
    minute_values, _, _ = parsed[0]
    hour_values, _, _ = parsed[1]
    dom_values, dom_unrestricted, dom_last = parsed[2]
    month_values, _, _ = parsed[3]
    dow_values, dow_unrestricted, _ = parsed[4]

    candidate = after.replace(second=0, microsecond=0) + _dt.timedelta(minutes=1)
    year, month = candidate.year, candidate.month
    while year <= 9999:
        if month in month_values:
            last_day = _days_in_month(year, month)
            for day in range(1, last_day + 1):
                dom_match = (day == last_day) if dom_last else (
                    dom_unrestricted or day in dom_values
                )
                dow = (_dt.date(year, month, day).weekday() + 1) % 7
                dow_match = dow_unrestricted or dow in dow_values
                if not (dom_match and dow_match):
                    continue
                for hour in sorted(hour_values):
                    for minute in sorted(minute_values):
                        result = _dt.datetime(year, month, day, hour, minute)
                        if result >= candidate and result > after:
                            return result
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    raise ValueError("no matching datetime in supported range")
