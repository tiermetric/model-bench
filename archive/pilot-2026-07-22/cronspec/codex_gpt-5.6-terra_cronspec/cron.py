"""A small, strict five-field cron scheduler."""

from datetime import datetime, timedelta

__all__ = ["next_fire"]


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4,
         "fri": 5, "sat": 6}


def _value(text, lo, hi, names):
    if text in names:
        value = names[text]
    elif text.isdigit():
        value = int(text)
    else:
        raise ValueError("invalid cron value")
    if not lo <= value <= hi:
        raise ValueError("cron value outside its domain")
    return value


def _range(start, end, lo, hi):
    """Return the inclusive, forward (and possibly wrapped) cyclic range."""
    if start <= end:
        return range(start, end + 1)
    return list(range(start, hi + 1)) + list(range(lo, end + 1))


def _parse_field(text, lo, hi, names=None):
    names = names or {}
    if text == "*":
        return set(range(lo, hi + 1)), False
    if not text:
        raise ValueError("empty cron field")

    values = set()
    for item in text.split(","):
        if not item:
            raise ValueError("empty list element")
        if item == "*":
            values.update(range(lo, hi + 1))
            continue
        if "/" in item:
            if item.count("/") != 1:
                raise ValueError("invalid step")
            base, stride_text = item.split("/")
            if not stride_text.isdigit() or int(stride_text) <= 0:
                raise ValueError("step must be positive")
            stride = int(stride_text)
            if base == "*":
                sequence = range(lo, hi + 1)
            else:
                if base.count("-") != 1:
                    raise ValueError("a step requires a range or *")
                left, right = base.split("-")
                start = _value(left, lo, hi, names)
                end = _value(right, lo, hi, names)
                sequence = _range(start, end, lo, hi)
            values.update(list(sequence)[::stride])
            continue
        if "-" in item:
            if item.count("-") != 1:
                raise ValueError("invalid range")
            left, right = item.split("-")
            start = _value(left, lo, hi, names)
            end = _value(right, lo, hi, names)
            values.update(_range(start, end, lo, hi))
        else:
            values.add(_value(item, lo, hi, names))
    return values, True


def _parse(expr):
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError("cron expression must have exactly five fields")
    minute = _parse_field(fields[0], 0, 59)
    hour = _parse_field(fields[1], 0, 23)
    dom_text = fields[2]
    if dom_text == "L":
        day_of_month = (None, True, True)  # values, restricted, last-day flag
    else:
        # L is deliberately not a normal name, so every other use is rejected.
        values, restricted = _parse_field(dom_text, 1, 31)
        day_of_month = (values, restricted, False)
    month = _parse_field(fields[3], 1, 12, _MONTHS)
    dow = _parse_field(fields[4], 0, 6, _DAYS)
    return minute, hour, day_of_month, month, dow


def next_fire(expr: str, after: datetime) -> datetime:
    """Return the first minute strictly after *after* matched by ``expr``.

    Datetimes are intentionally treated as naive UTC wall-clock values.
    """
    (minutes, _), (hours, _), (dom_values, _, last_day), \
        (months, _), (dows, _) = _parse(expr)

    try:
        candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    except OverflowError as exc:
        raise ValueError("no later datetime is available") from exc

    date = candidate.date()
    first_date = date
    while True:
        weekday = (date.weekday() + 1) % 7  # Python Monday=0; cron Sunday=0.
        if date.month in months and weekday in dows:
            if last_day:
                tomorrow = date + timedelta(days=1)
                dom_match = tomorrow.month != date.month
            else:
                dom_match = date.day in dom_values
            # Both constraints must hold.  Bare '*' fields have already made
            # their respective condition universally true.
            if dom_match:
                floor_hour = candidate.hour if date == first_date else 0
                floor_minute = candidate.minute if date == first_date else 0
                for hour in sorted(hours):
                    if hour < floor_hour:
                        continue
                    for minute in sorted(minutes):
                        if hour == floor_hour and minute < floor_minute:
                            continue
                        return datetime(date.year, date.month, date.day, hour, minute)
        try:
            date += timedelta(days=1)
        except OverflowError as exc:
            raise ValueError("no matching later datetime is available") from exc
