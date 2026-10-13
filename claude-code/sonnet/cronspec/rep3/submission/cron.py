"""A cron-like schedule expression evaluator.

Grammar, deviations from standard cron, and hardening rules are documented
in the project spec. This module exposes a single public function,
next_fire(expr, after), which computes the next matching minute boundary.
"""

import calendar
from datetime import datetime, timedelta

_FIELD_DOMAINS = {
    "minute": (0, 59),
    "hour": (0, 23),
    "dom": (1, 31),
    "month": (1, 12),
    "dow": (0, 6),
}

_NAME_MAPS = {
    "month": {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    },
    "dow": {
        "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
    },
}

# Safety bound on forward search so a self-contradictory expression (e.g.
# day-of-month 31 in a February-only month field) cannot loop forever.
_SEARCH_LIMIT_DAYS = 40000


def _resolve_value(token, field_type):
    names = _NAME_MAPS.get(field_type)
    if names and token in names:
        return names[token]
    if token.isdigit():
        value = int(token)
        lo, hi = _FIELD_DOMAINS[field_type]
        if value < lo or value > hi:
            raise ValueError(f"value {token!r} out of domain for {field_type}")
        return value
    raise ValueError(f"unknown token {token!r} for {field_type}")


def _wrapped_order(a, b, lo, hi):
    """Values from a to b inclusive, in forward order, wrapping through hi/lo
    if a > b."""
    if a <= b:
        return list(range(a, b + 1))
    return list(range(a, hi + 1)) + list(range(lo, b + 1))


def _parse_item(item, field_type):
    if item == "":
        raise ValueError("empty list element")
    if item == "L":
        raise ValueError("L is only valid as the entire day-of-month field")

    lo, hi = _FIELD_DOMAINS[field_type]

    if "/" in item:
        base, _, step_str = item.partition("/")
        if not step_str.isdigit():
            raise ValueError(f"invalid step {step_str!r}")
        step = int(step_str)
        if step <= 0:
            raise ValueError("step must be positive")
        if base == "*":
            a, b = lo, hi
        elif "-" in base:
            a_tok, b_tok = base.split("-", 1)
            a = _resolve_value(a_tok, field_type)
            b = _resolve_value(b_tok, field_type)
        else:
            raise ValueError("step may only apply to '*' or a range")
        order = _wrapped_order(a, b, lo, hi)
        return set(order[0::step])

    if "-" in item:
        a_tok, b_tok = item.split("-", 1)
        a = _resolve_value(a_tok, field_type)
        b = _resolve_value(b_tok, field_type)
        return set(_wrapped_order(a, b, lo, hi))

    return {_resolve_value(item, field_type)}


def _parse_field(raw, field_type):
    if field_type == "dom" and raw == "L":
        return "L"

    lo, hi = _FIELD_DOMAINS[field_type]
    if raw == "*":
        return set(range(lo, hi + 1))

    parts = raw.split(",")
    result = set()
    for part in parts:
        result |= _parse_item(part, field_type)
    return result


def _parse_expr(expr):
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"expected 5 fields, got {len(fields)}")

    minute_raw, hour_raw, dom_raw, month_raw, dow_raw = fields

    minutes = _parse_field(minute_raw, "minute")
    hours = _parse_field(hour_raw, "hour")
    dom = _parse_field(dom_raw, "dom")
    months = _parse_field(month_raw, "month")
    dow = _parse_field(dow_raw, "dow")

    return minutes, hours, dom, months, dow


def _find_time(sorted_hours, sorted_minutes, floor):
    """Earliest (hour, minute) pair from the given sorted candidates that is
    >= floor (or the overall earliest if floor is None)."""
    if floor is None:
        return sorted_hours[0], sorted_minutes[0]
    start_h, start_m = floor
    for h in sorted_hours:
        if h < start_h:
            continue
        if h == start_h:
            for m in sorted_minutes:
                if m >= start_m:
                    return h, m
            continue
        return h, sorted_minutes[0]
    return None


def next_fire(expr, after):
    """Return the earliest datetime strictly after `after` matching `expr`."""
    minutes, hours, dom, months, dow = _parse_expr(expr.strip())

    dom_is_l = dom == "L"

    sorted_hours = sorted(hours)
    sorted_minutes = sorted(minutes)

    start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)

    d = start.date()
    first_date = d

    for _ in range(_SEARCH_LIMIT_DAYS):
        if d.month in months:
            if dom_is_l:
                dom_ok = d.day == calendar.monthrange(d.year, d.month)[1]
            else:
                dom_ok = d.day in dom
            dow_val = (d.weekday() + 1) % 7
            dow_ok = dow_val in dow
            if dom_ok and dow_ok:
                floor = (start.hour, start.minute) if d == first_date else None
                result = _find_time(sorted_hours, sorted_minutes, floor)
                if result is not None:
                    h, m = result
                    return datetime(d.year, d.month, d.day, h, m)
        d = d + timedelta(days=1)

    raise ValueError("no matching fire time found within search bound")
