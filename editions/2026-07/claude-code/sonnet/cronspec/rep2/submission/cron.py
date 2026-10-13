"""A cron-like expression scheduler.

Five whitespace-separated fields: minute, hour, day-of-month, month,
day-of-week. See the module-level docstring in the project spec for the
full grammar and the deviations from standard vixie-cron semantics:

  - day-of-month and day-of-week are INTERSECTED when both are restricted
    (not unioned, as standard cron does).
  - a step "A-B/s" is anchored at A, not at 0.
  - domains are strict (e.g. day-of-week is 0-6; 7 is invalid).
  - ranges "A-B" wrap when A > B, and a stepped wrapped range counts
    through the wrap starting at A.
  - day-of-month may be the literal "L", meaning the last day of the month.
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

_MAX_SEARCH_DAYS = 20000


def _parse_value(token, low, high, names):
    if names is not None and token in names:
        return names[token]
    if token.isdigit():
        value = int(token)
        if not (low <= value <= high):
            raise ValueError(
                f"value {token!r} out of domain [{low}, {high}]"
            )
        return value
    raise ValueError(f"unknown token {token!r}")


def _parse_item(item, low, high, names):
    if item == "":
        raise ValueError("empty list element")

    step = None
    base = item
    if "/" in item:
        base, step_str = item.split("/", 1)
        if "/" in step_str:
            raise ValueError(f"malformed step in {item!r}")
        if not step_str.isdigit() or int(step_str) == 0:
            raise ValueError(f"invalid step in {item!r}")
        step = int(step_str)

    if base == "*":
        ordered = list(range(low, high + 1))
    elif "-" in base:
        a_tok, _, b_tok = base.partition("-")
        if "-" in b_tok:
            raise ValueError(f"malformed range {base!r}")
        a_val = _parse_value(a_tok, low, high, names)
        b_val = _parse_value(b_tok, low, high, names)
        if a_val <= b_val:
            ordered = list(range(a_val, b_val + 1))
        else:
            ordered = list(range(a_val, high + 1)) + list(range(low, b_val + 1))
    else:
        if step is not None:
            raise ValueError(f"step not allowed on single value {item!r}")
        return {_parse_value(base, low, high, names)}

    if step is not None:
        return set(ordered[0::step])
    return set(ordered)


def _parse_field(field_str, low, high, names=None):
    s = field_str.strip()
    if s == "":
        raise ValueError("empty field")
    if s == "*":
        return set(range(low, high + 1)), False
    items = s.split(",")
    if any(it == "" for it in items):
        raise ValueError(f"empty list element in {field_str!r}")
    values = set()
    for it in items:
        values |= _parse_item(it, low, high, names)
    return values, True


def _parse_dom_field(field_str):
    s = field_str.strip()
    if s == "L":
        return "L", True
    return _parse_field(s, 1, 31, None)


def _parse_expr(expr):
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(
            f"expected exactly 5 fields, got {len(fields)} in {expr!r}"
        )
    minute_str, hour_str, dom_str, month_str, dow_str = fields

    minute_set, _ = _parse_field(minute_str, 0, 59, None)
    hour_set, _ = _parse_field(hour_str, 0, 23, None)
    dom_val, dom_restricted = _parse_dom_field(dom_str)
    month_set, _ = _parse_field(month_str, 1, 12, MONTH_NAMES)
    dow_set, dow_restricted = _parse_field(dow_str, 0, 6, DOW_NAMES)

    return {
        "minute": minute_set,
        "hour": hour_set,
        "dom": dom_val,
        "dom_restricted": dom_restricted,
        "month": month_set,
        "dow": dow_set,
        "dow_restricted": dow_restricted,
    }


def _cron_dow(d):
    # Python's date.weekday(): Monday=0 ... Sunday=6.
    # cron dow here: Sunday=0 ... Saturday=6.
    return (d.weekday() + 1) % 7


def next_fire(expr: str, after: datetime) -> datetime:
    fields = _parse_expr(expr)

    minute_set = fields["minute"]
    hour_set = fields["hour"]
    dom_val = fields["dom"]
    dom_restricted = fields["dom_restricted"]
    month_set = fields["month"]
    dow_set = fields["dow"]
    dow_restricted = fields["dow_restricted"]

    sorted_hours = sorted(hour_set)
    sorted_minutes = sorted(minute_set)

    start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    start_date = start.date()

    candidate_date = start_date
    days_checked = 0

    while True:
        if days_checked > _MAX_SEARCH_DAYS:
            raise ValueError(
                f"no matching fire time found for {expr!r} within search bound"
            )

        if candidate_date.month in month_set:
            if dom_val == "L":
                last_day = calendar.monthrange(
                    candidate_date.year, candidate_date.month
                )[1]
                dom_ok = candidate_date.day == last_day
            else:
                dom_ok = candidate_date.day in dom_val
            dow_ok = _cron_dow(candidate_date) in dow_set

            if dom_restricted and dow_restricted:
                day_match = dom_ok and dow_ok
            elif dom_restricted:
                day_match = dom_ok
            elif dow_restricted:
                day_match = dow_ok
            else:
                day_match = True

            if day_match:
                threshold = (
                    start.hour * 60 + start.minute
                    if candidate_date == start_date
                    else 0
                )
                for h in sorted_hours:
                    for m in sorted_minutes:
                        minute_of_day = h * 60 + m
                        if minute_of_day >= threshold:
                            return datetime(
                                candidate_date.year,
                                candidate_date.month,
                                candidate_date.day,
                                h,
                                m,
                            )

        candidate_date += timedelta(days=1)
        days_checked += 1
