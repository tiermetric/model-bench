"""A small, strict, UTC-naive cron expression evaluator."""

import calendar as _calendar
import datetime as _datetime
import re as _re


_MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_DAYS = {name: number for number, name in enumerate(
    ("sun", "mon", "tue", "wed", "thu", "fri", "sat"))}


def _field_values(text, lo, hi, names=None, allow_last=False):
    if allow_last and text == "L":
        return None, True
    if not text or "L" in text:
        raise ValueError("invalid cron field")

    values = set()
    for part in text.split(","):
        if not part:
            raise ValueError("empty cron list element")
        pieces = part.split("/")
        if len(pieces) > 2 or (len(pieces) == 2 and not pieces[1].isdigit()):
            raise ValueError("invalid cron step")
        base = pieces[0]
        step = None
        if len(pieces) == 2:
            step = int(pieces[1])
            if step <= 0:
                raise ValueError("step must be positive")
            if base != "*" and "-" not in base:
                raise ValueError("steps require a star or range")

        if base == "*":
            start, end = lo, hi
        else:
            endpoints = base.split("-")
            if len(endpoints) == 1:
                if step is not None:
                    raise ValueError("steps require a range")
                start = end = _parse_value(endpoints[0], lo, hi, names)
            elif len(endpoints) == 2:
                start = _parse_value(endpoints[0], lo, hi, names)
                end = _parse_value(endpoints[1], lo, hi, names)
            else:
                raise ValueError("invalid cron range")

        sequence = _range_sequence(start, end, lo, hi)
        if step is None:
            values.update(sequence)
        else:
            values.update(sequence[::step])
    return values, False


def _parse_value(text, lo, hi, names):
    if names is not None and text in names:
        value = names[text]
    elif text.isdigit():
        value = int(text)
    else:
        raise ValueError("invalid cron value")
    if not lo <= value <= hi:
        raise ValueError("cron value out of range")
    return value


def _range_sequence(start, end, lo, hi):
    if start <= end:
        return list(range(start, end + 1))
    return list(range(start, hi + 1)) + list(range(lo, end + 1))


def _parse(expr):
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError("cron expression must have five fields")
    specs = (
        (0, 59, None, False),
        (0, 23, None, False),
        (1, 31, None, True),
        (1, 12, _MONTHS, False),
        (0, 6, _DAYS, False),
    )
    parsed = []
    for index, (lo, hi, names, allow_last) in enumerate(specs):
        if index == 2 and fields[index] == "L":
            parsed.append((None, False, True))
        else:
            if "L" in fields[index]:
                raise ValueError("L is only a standalone day-of-month field")
            values, _ = _field_values(fields[index], lo, hi, names, allow_last)
            parsed.append((values, fields[index] == "*", False))
    return parsed


def next_fire(expr: str, after: _datetime.datetime) -> _datetime.datetime:
    """Return the first cron minute strictly later than *after*."""
    minute, hour, dom, month, dow = _parse(expr)
    candidate = after.replace(second=0, microsecond=0) + _datetime.timedelta(minutes=1)

    while True:
        if candidate.month not in month[0]:
            candidate = (candidate.replace(day=1) + _datetime.timedelta(days=32)).replace(day=1)
            continue
        last_day = _calendar.monthrange(candidate.year, candidate.month)[1]
        dom_match = (candidate.day == last_day if dom[2] else candidate.day in dom[0])
        dow_number = (candidate.weekday() + 1) % 7
        dow_match = dow_number in dow[0]
        if dom[1] and dow[1]:
            day_match = True
        elif dom[1]:
            day_match = dow_match
        elif dow[1]:
            day_match = dom_match
        else:
            day_match = dom_match and dow_match
        if day_match:
            if candidate.day <= last_day:
                for selected_hour in sorted(hour[0]):
                    for selected_minute in sorted(minute[0]):
                        result = candidate.replace(hour=selected_hour, minute=selected_minute,
                                                   second=0, microsecond=0)
                        if result > after:
                            return result
        candidate = (candidate + _datetime.timedelta(days=1)).replace(hour=0, minute=0,
                                                                       second=0, microsecond=0)
