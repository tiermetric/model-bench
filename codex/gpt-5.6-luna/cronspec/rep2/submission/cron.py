"""A small, strict, UTC-naive cron expression scheduler."""

from datetime import datetime as _datetime
from datetime import timedelta as _timedelta
import calendar as _calendar
import re as _re


_MONTHS = {
    name: number for number, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}
_DAYS = {name: number for number, name in enumerate(
    ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
)}
_INTEGER = _re.compile(r"[0-9]+$")


def _value(text, field, lo, hi, names):
    if names and text in names:
        value = names[text]
    elif _INTEGER.fullmatch(text):
        value = int(text)
    else:
        raise ValueError("invalid cron value")
    if not lo <= value <= hi:
        raise ValueError("cron value out of domain")
    return value


def _range_values(start, end, lo, hi):
    if start <= end:
        return list(range(start, end + 1))
    return list(range(start, hi + 1)) + list(range(lo, end + 1))


def _parse_field(text, field, lo, hi, names=None):
    if field == 2 and text == "L":
        return None, False
    if field != 2 and "L" in text:
        raise ValueError("L is only valid as the complete day-of-month field")
    if not text:
        raise ValueError("empty cron field")

    values = set()
    for item in text.split(","):
        if not item:
            raise ValueError("empty cron list element")
        slash = item.split("/")
        if len(slash) > 2:
            raise ValueError("invalid cron step")
        base = slash[0]
        step = 1
        stepped = len(slash) == 2
        if stepped:
            if not _INTEGER.fullmatch(slash[1]) or int(slash[1]) <= 0:
                raise ValueError("invalid cron step")
            step = int(slash[1])

        if base == "*":
            sequence = list(range(lo, hi + 1))
        else:
            pieces = base.split("-")
            if len(pieces) == 1:
                if stepped:
                    raise ValueError("a single value cannot have a step")
                sequence = [_value(pieces[0], field, lo, hi, names)]
            elif len(pieces) == 2 and pieces[0] and pieces[1]:
                start = _value(pieces[0], field, lo, hi, names)
                end = _value(pieces[1], field, lo, hi, names)
                sequence = _range_values(start, end, lo, hi)
            else:
                raise ValueError("invalid cron range")
        values.update(sequence[::step])
    return values, False


def _parse(expr):
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError("cron expression must have five fields")
    minute, _ = _parse_field(fields[0], 0, 0, 59)
    hour, _ = _parse_field(fields[1], 1, 0, 23)
    day, day_unrestricted = _parse_field(fields[2], 2, 1, 31)
    month, _ = _parse_field(fields[3], 3, 1, 12, _MONTHS)
    dow, dow_unrestricted = _parse_field(fields[4], 4, 0, 6, _DAYS)
    return minute, hour, day, day_unrestricted, month, dow, dow_unrestricted


def _day_matches(date, day, day_unrestricted, dow, dow_unrestricted):
    if day_unrestricted:
        dom_ok = True
    elif day is None:
        dom_ok = date.day == _calendar.monthrange(date.year, date.month)[1]
    else:
        dom_ok = date.day in day
    dow_value = (date.weekday() + 1) % 7
    dow_ok = dow_unrestricted or dow_value in dow
    return dom_ok and dow_ok


def next_fire(expr: str, after: _datetime) -> _datetime:
    """Return the first minute strictly after *after* matching *expr*."""
    minute, hour, day, day_unrestricted, month, dow, dow_unrestricted = _parse(expr)
    candidate = after.replace(second=0, microsecond=0) + _timedelta(minutes=1)

    while candidate.year <= 9999:
        if candidate.month in month:
            last_day = _calendar.monthrange(candidate.year, candidate.month)[1]
            for day_number in range(1, last_day + 1):
                date = candidate.replace(day=day_number).date()
                if not _day_matches(date, day, day_unrestricted, dow,
                                     dow_unrestricted):
                    continue
                for h in sorted(hour):
                    for m in sorted(minute):
                        result = candidate.replace(day=day_number, hour=h,
                                                   minute=m, second=0,
                                                   microsecond=0)
                        if result > after:
                            return result
        try:
            candidate = (candidate.replace(day=1, hour=0, minute=0,
                                           second=0, microsecond=0)
                         + _timedelta(days=32)).replace(day=1)
        except OverflowError:
            break

    raise ValueError("cron expression has no future match")


__all__ = ["next_fire"]
