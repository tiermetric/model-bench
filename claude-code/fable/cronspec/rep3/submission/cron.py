"""Cron-expression scheduler: compute the next fire time after a given moment.

All datetimes are tz-naive UTC wall-clock values. The one public function is
``next_fire(expr, after)``.

Semantics (deliberate deviations from vixie cron, per spec):
- day-of-month and day-of-week combine as an INTERSECTION, never a union.
- steps ``A-B/s`` are anchored at ``A`` (A, A+s, ...), not at multiples of s.
- domains are strict: day-of-week is 0-6 only (7 is an error), names are
  lowercase only.
- ranges with start > end wrap through the domain maximum, and steps over a
  wrapped range count through the wrap.
- ``L`` (day-of-month field only, as the whole field) means the last calendar
  day of the month under consideration.
"""

from calendar import monthrange
from datetime import datetime, timedelta

_MONTH_NAMES = {name: i for i, name in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1)}
_DOW_NAMES = {name: i for i, name in enumerate(
    "sun mon tue wed thu fri sat".split())}

# One full Gregorian calendar cycle: any satisfiable expression fires within
# this many days, so exhausting it proves the expression can never match.
_GREGORIAN_CYCLE_DAYS = 146097


def _parse_value(token, lo, hi, names, field):
    if names and token in names:
        return names[token]
    if token.isascii() and token.isdigit():
        value = int(token)
        if lo <= value <= hi:
            return value
        raise ValueError(f"{field}: value {value} out of range {lo}-{hi}")
    raise ValueError(f"{field}: invalid token {token!r}")


def _expand_element(element, lo, hi, names, field):
    """Expand one comma-list element into its ordered list of values."""
    base, step = element, 1
    if "/" in element:
        parts = element.split("/")
        if len(parts) != 2:
            raise ValueError(f"{field}: invalid step syntax {element!r}")
        base, step_text = parts
        if not (step_text.isascii() and step_text.isdigit()) or int(step_text) == 0:
            raise ValueError(f"{field}: step must be a positive integer in {element!r}")
        step = int(step_text)
        if base != "*" and "-" not in base:
            raise ValueError(f"{field}: only '*' or a range may be stepped: {element!r}")
    if base == "*":
        seq = list(range(lo, hi + 1))
    elif "-" in base:
        ends = base.split("-")
        if len(ends) != 2:
            raise ValueError(f"{field}: invalid range {base!r}")
        a = _parse_value(ends[0], lo, hi, names, field)
        b = _parse_value(ends[1], lo, hi, names, field)
        if a <= b:
            seq = list(range(a, b + 1))
        else:
            # Wrap-around range: A..hi then lo..B, kept in wrapped order so a
            # step keeps counting through the wrap.
            seq = list(range(a, hi + 1)) + list(range(lo, b + 1))
    else:
        seq = [_parse_value(base, lo, hi, names, field)]
    # Steps are anchored at the range start: seq[0], seq[0+s], ...
    return seq[::step]


def _parse_field(text, lo, hi, names, field):
    values = set()
    for element in text.split(","):
        if not element:
            raise ValueError(f"{field}: empty list element in {text!r}")
        values.update(_expand_element(element, lo, hi, names, field))
    return values


def next_fire(expr: str, after: datetime) -> datetime:
    """Return the earliest datetime strictly after ``after`` matching ``expr``."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"expression must have exactly 5 fields, got {len(fields)}")
    minute_f, hour_f, dom_f, month_f, dow_f = fields

    minutes = sorted(_parse_field(minute_f, 0, 59, None, "minute"))
    hours = sorted(_parse_field(hour_f, 0, 23, None, "hour"))
    dom_last = dom_f == "L"
    dom = None if dom_last else _parse_field(dom_f, 1, 31, None, "day-of-month")
    months = _parse_field(month_f, 1, 12, _MONTH_NAMES, "month")
    dow = _parse_field(dow_f, 0, 6, _DOW_NAMES, "day-of-week")

    start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = start.date()
    one_day = timedelta(days=1)
    for offset in range(_GREGORIAN_CYCLE_DAYS):
        if day.month in months:
            if dom_last:
                dom_ok = day.day == monthrange(day.year, day.month)[1]
            else:
                dom_ok = day.day in dom
            # Sunday=0 cron numbering; Python's weekday() has Monday=0.
            dow_ok = (day.weekday() + 1) % 7 in dow
            # A bare '*' expands to the full domain, so plain AND yields the
            # spec's intersection semantics for every restricted/unrestricted
            # combination.
            if dom_ok and dow_ok:
                if offset == 0:
                    for h in hours:
                        if h < start.hour:
                            continue
                        for m in minutes:
                            if h == start.hour and m < start.minute:
                                continue
                            return datetime(day.year, day.month, day.day, h, m)
                else:
                    return datetime(day.year, day.month, day.day, hours[0], minutes[0])
        day += one_day
    raise ValueError("expression never matches any datetime")
