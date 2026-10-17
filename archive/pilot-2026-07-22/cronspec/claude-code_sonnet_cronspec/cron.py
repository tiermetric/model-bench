"""A cron-like expression scheduler.

Public API: next_fire(expr, after) -> datetime
"""

import calendar
from datetime import datetime, timedelta

MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

DOW_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}

_MONTH_LO, _MONTH_HI = 1, 12
_DOM_LO, _DOM_HI = 1, 31
_DOW_LO, _DOW_HI = 0, 6
_HOUR_LO, _HOUR_HI = 0, 23
_MINUTE_LO, _MINUTE_HI = 0, 59

_MAX_MONTHS_SEARCHED = 4000


def _parse_value(tok, lo, hi, names):
    if names is not None and tok in names:
        return names[tok]
    if tok.isdigit():
        val = int(tok)
        if val < lo or val > hi:
            raise ValueError(f"value {tok!r} out of domain [{lo},{hi}]")
        return val
    raise ValueError(f"invalid token {tok!r}")


def _range_values(a, b, lo, hi):
    if a <= b:
        return list(range(a, b + 1))
    return list(range(a, hi + 1)) + list(range(lo, b + 1))


def _parse_field(field_str, lo, hi, names=None, allow_l=False):
    if field_str == "*":
        return {"wildcard": True, "l": False, "values": set(range(lo, hi + 1))}

    if field_str == "L":
        if not allow_l:
            raise ValueError("L is only allowed in the day-of-month field")
        return {"wildcard": False, "l": True, "values": None}

    values = set()
    for part in field_str.split(","):
        if part == "":
            raise ValueError("empty list element")
        if "L" in part:
            raise ValueError("L cannot be combined with list/range/step")

        step = None
        base = part
        if "/" in part:
            pieces = part.split("/")
            if len(pieces) != 2:
                raise ValueError(f"invalid token {part!r}")
            base, step_str = pieces
            if step_str == "" or not step_str.isdigit():
                raise ValueError(f"invalid step {step_str!r}")
            step = int(step_str)
            if step <= 0:
                raise ValueError("step must be a positive integer")

        if base == "*":
            seq = list(range(lo, hi + 1))
            values.update(seq[0::step] if step is not None else seq)
        elif "-" in base:
            range_parts = base.split("-", 1)
            if range_parts[0] == "" or range_parts[1] == "":
                raise ValueError(f"invalid range {base!r}")
            a = _parse_value(range_parts[0], lo, hi, names)
            b = _parse_value(range_parts[1], lo, hi, names)
            seq = _range_values(a, b, lo, hi)
            values.update(seq[0::step] if step is not None else seq)
        else:
            if step is not None:
                raise ValueError("step may only apply to '*' or a range")
            values.add(_parse_value(base, lo, hi, names))

    return {"wildcard": False, "l": False, "values": values}


def _parse_expr(expr):
    if not isinstance(expr, str):
        raise ValueError("expr must be a string")

    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"expected 5 fields, got {len(fields)}")

    minute_f = _parse_field(fields[0], _MINUTE_LO, _MINUTE_HI)
    hour_f = _parse_field(fields[1], _HOUR_LO, _HOUR_HI)
    dom_f = _parse_field(fields[2], _DOM_LO, _DOM_HI, allow_l=True)
    month_f = _parse_field(fields[3], _MONTH_LO, _MONTH_HI, names=MONTH_NAMES)
    dow_f = _parse_field(fields[4], _DOW_LO, _DOW_HI, names=DOW_NAMES)

    return minute_f, hour_f, dom_f, month_f, dow_f


def _next_month(year, month):
    if month == 12:
        return year + 1, 1
    return year, month + 1


def _day_matches(year, month, day, dom_wildcard, dom_l, dom_set, dow_wildcard, dow_set, days_in_month):
    if dom_wildcard and dow_wildcard:
        return True

    cron_dow = (datetime(year, month, day).weekday() + 1) % 7
    dow_match = dow_wildcard or (cron_dow in dow_set)

    if dom_wildcard:
        return dow_match

    if dom_l:
        dom_match = day == days_in_month
    else:
        dom_match = day in dom_set

    if dow_wildcard:
        return dom_match

    return dom_match and dow_match


def _earliest_time(hour_list, minute_list, threshold_minutes):
    for h in hour_list:
        base = h * 60
        if base + minute_list[-1] < threshold_minutes:
            continue
        for m in minute_list:
            if base + m >= threshold_minutes:
                return h, m
    return None


def next_fire(expr, after):
    minute_f, hour_f, dom_f, month_f, dow_f = _parse_expr(expr)

    hour_list = sorted(hour_f["values"])
    minute_list = sorted(minute_f["values"])
    month_set = month_f["values"]
    dow_wildcard = dow_f["wildcard"]
    dow_set = dow_f["values"]
    dom_wildcard = dom_f["wildcard"]
    dom_l = dom_f["l"]
    dom_set = dom_f["values"]

    current = after.replace(second=0, microsecond=0) + timedelta(minutes=1)

    year, month = current.year, current.month
    start_day = current.day
    start_minutes = current.hour * 60 + current.minute
    is_first_month = True

    for _ in range(_MAX_MONTHS_SEARCHED):
        if month not in month_set:
            year, month = _next_month(year, month)
            start_day = 1
            start_minutes = 0
            is_first_month = False
            continue

        days_in_month = calendar.monthrange(year, month)[1]
        first_day = start_day if is_first_month else 1

        day = first_day
        while day <= days_in_month:
            threshold = start_minutes if (day == first_day and is_first_month) else 0
            if _day_matches(year, month, day, dom_wildcard, dom_l, dom_set, dow_wildcard, dow_set, days_in_month):
                found = _earliest_time(hour_list, minute_list, threshold)
                if found is not None:
                    h, m = found
                    return datetime(year, month, day, h, m)
            day += 1

        year, month = _next_month(year, month)
        start_day = 1
        start_minutes = 0
        is_first_month = False

    raise RuntimeError("no matching time found within search bound")
