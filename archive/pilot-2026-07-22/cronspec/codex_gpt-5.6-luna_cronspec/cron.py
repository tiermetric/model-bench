"""A small, strict, UTC-naive cron scheduler."""

from calendar import monthrange as _monthrange
from datetime import datetime, timedelta


__all__ = ["next_fire"]

_MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"), 1
)}
_DAYS = {name: number for number, name in enumerate(
    ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
)}


def _value(text, field, lo, hi, names):
    if text.isdigit():
        value = int(text)
    elif names and text in names:
        value = names[text]
    else:
        raise ValueError("invalid cron value")
    if not lo <= value <= hi:
        raise ValueError("cron value out of domain")
    return value


def _range_values(start, end, step, lo, hi):
    if start <= end:
        sequence = range(start, end + 1)
    else:
        sequence = list(range(start, hi + 1)) + list(range(lo, end + 1))
    return {value for index, value in enumerate(sequence) if index % step == 0}


def _parse_field(text, lo, hi, names=None, allow_last=False):
    if allow_last and text == "L":
        return None, False, True
    if not text or "L" in text:
        raise ValueError("invalid cron field")

    values = set()
    for item in text.split(","):
        if not item:
            raise ValueError("empty cron list element")
        slash = item.count("/")
        if slash > 1:
            raise ValueError("invalid cron step")
        if slash:
            base, step_text = item.split("/")
            if not step_text.isdigit() or int(step_text) <= 0:
                raise ValueError("invalid cron step")
            step = int(step_text)
            if base == "*":
                values.update(range(lo, hi + 1)[::step])
            else:
                if base.count("-") != 1:
                    raise ValueError("steps require a range")
                left, right = base.split("-")
                start = _value(left, text, lo, hi, names)
                end = _value(right, text, lo, hi, names)
                values.update(_range_values(start, end, step, lo, hi))
        elif item == "*":
            values.update(range(lo, hi + 1))
        elif item.count("-") == 1:
            left, right = item.split("-")
            start = _value(left, text, lo, hi, names)
            end = _value(right, text, lo, hi, names)
            values.update(_range_values(start, end, 1, lo, hi))
        else:
            values.add(_value(item, text, lo, hi, names))

    return values, text == "*", False


def _parse(expr):
    if not isinstance(expr, str):
        raise ValueError("expression must be a string")
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError("cron expression must have five fields")

    minute, minute_star, _ = _parse_field(fields[0], 0, 59)
    hour, hour_star, _ = _parse_field(fields[1], 0, 23)
    dom, dom_star, dom_last = _parse_field(fields[2], 1, 31, allow_last=True)
    month, month_star, _ = _parse_field(fields[3], 1, 12, _MONTHS)
    dow, dow_star, _ = _parse_field(fields[4], 0, 6, _DAYS)
    return minute, hour, dom, month, dow, dom_star, dow_star, dom_last


def next_fire(expr: str, after: datetime) -> datetime:
    """Return the first minute strictly later than *after* matching *expr*."""
    minute, hour, dom, month, dow, dom_star, dow_star, dom_last = _parse(expr)
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    year, month_number = candidate.year, candidate.month

    while True:
        if month_number in month:
            last_day = _monthrange(year, month_number)[1]
            for day in range(1, last_day + 1):
                if dom_last:
                    day_match = day == last_day
                else:
                    day_match = dom_star or day in dom
                cron_dow = (datetime(year, month_number, day).weekday() + 1) % 7
                dow_match = dow_star or cron_dow in dow
                if dom_star and dow_star:
                    date_match = True
                elif dom_star:
                    date_match = dow_match
                elif dow_star:
                    date_match = day_match
                else:
                    date_match = day_match and dow_match
                if not date_match:
                    continue
                for h in sorted(hour):
                    for m in sorted(minute):
                        result = datetime(year, month_number, day, h, m)
                        if result >= candidate:
                            return result

        if month_number == 12:
            year += 1
            month_number = 1
        else:
            month_number += 1
