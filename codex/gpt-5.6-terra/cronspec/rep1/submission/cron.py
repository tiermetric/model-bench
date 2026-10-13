"""A small, deliberately strict, cron-like scheduler."""

from datetime import datetime, timedelta
import calendar
import re

__all__ = ["next_fire"]


_MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
)}
_DAYS = {name: number for number, name in enumerate(
    ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
)}


def _value(token, lo, hi, names):
    if token in names:
        return names[token]
    if not re.fullmatch(r"[0-9]+", token):
        raise ValueError("invalid cron value")
    value = int(token)
    if not lo <= value <= hi:
        raise ValueError("cron value out of range")
    return value


def _range_values(start, end, lo, hi, step):
    """Return a possibly wrapped, start-anchored range."""
    if start <= end:
        ordered = range(start, end + 1)
    else:
        ordered = list(range(start, hi + 1)) + list(range(lo, end + 1))
    return set(ordered[::step])


def _parse_field(text, lo, hi, names=None, allow_last=False):
    names = names or {}
    if allow_last and text == "L":
        return None, True, "last"
    if text == "*":
        return set(range(lo, hi + 1)), False, None
    if not text or text.startswith(",") or text.endswith(",") or ",," in text:
        raise ValueError("empty cron list element")

    values = set()
    for item in text.split(","):
        if "/" in item:
            if item.count("/") != 1:
                raise ValueError("invalid cron step")
            base, step_text = item.split("/")
            if not re.fullmatch(r"[0-9]+", step_text) or int(step_text) <= 0:
                raise ValueError("invalid cron step")
            step = int(step_text)
            if base == "*":
                values.update(_range_values(lo, hi, lo, hi, step))
                continue
            match = re.fullmatch(r"(.+)-(.+)", base)
            if not match:
                raise ValueError("a step requires * or a range")
            start = _value(match.group(1), lo, hi, names)
            end = _value(match.group(2), lo, hi, names)
            values.update(_range_values(start, end, lo, hi, step))
            continue

        match = re.fullmatch(r"(.+)-(.+)", item)
        if match:
            start = _value(match.group(1), lo, hi, names)
            end = _value(match.group(2), lo, hi, names)
            values.update(_range_values(start, end, lo, hi, 1))
        else:
            values.add(_value(item, lo, hi, names))
    return values, True, None


def _parse(expr):
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError("cron expression must have exactly five fields")
    minute = _parse_field(fields[0], 0, 59)
    hour = _parse_field(fields[1], 0, 23)
    dom = _parse_field(fields[2], 1, 31, allow_last=True)
    month = _parse_field(fields[3], 1, 12, _MONTHS)
    dow = _parse_field(fields[4], 0, 6, _DAYS)
    return minute, hour, dom, month, dow


def _day_matches(day, dom, dow):
    dom_values, dom_restricted, dom_kind = dom
    dow_values, dow_restricted, _ = dow
    if dom_kind == "last":
        dom_ok = day.day == calendar.monthrange(day.year, day.month)[1]
    else:
        dom_ok = day.day in dom_values
    cron_dow = (day.weekday() + 1) % 7
    dow_ok = cron_dow in dow_values
    if dom_restricted and dow_restricted:
        return dom_ok and dow_ok
    if dom_restricted:
        return dom_ok
    if dow_restricted:
        return dow_ok
    return True


def next_fire(expr: str, after: datetime) -> datetime:
    """Return the first minute strictly after *after* matching ``expr``."""
    minute, hour, dom, month, dow = _parse(expr)
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = candidate.date()

    while True:
        if day.month in month[0] and _day_matches(day, dom, dow):
            start_hour = candidate.hour if day == candidate.date() else 0
            for selected_hour in sorted(hour[0]):
                if selected_hour < start_hour:
                    continue
                start_minute = candidate.minute if day == candidate.date() and selected_hour == start_hour else 0
                for selected_minute in sorted(minute[0]):
                    if selected_minute >= start_minute:
                        return datetime(day.year, day.month, day.day, selected_hour, selected_minute)
        day += timedelta(days=1)
