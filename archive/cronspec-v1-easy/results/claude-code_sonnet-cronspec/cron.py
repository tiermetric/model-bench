"""A cron-like scheduler with two deliberate deviations from vixie-cron.

Public API: next_fire(expr, after) -> datetime
"""

import re
from datetime import datetime, timedelta

_MONTHS = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

_DOWS = {
    'sun': 0, 'mon': 1, 'tue': 2, 'wed': 3, 'thu': 4, 'fri': 5, 'sat': 6,
}

# One comma-separated element: "*", "*/N", "A", "A-B", or "A-B/N".
_PART_RE = re.compile(r'^(\*|[A-Za-z0-9]+(?:-[A-Za-z0-9]+)?)(?:/([0-9]+))?$')

_MAX_DAYS_SEARCHED = 20000


def _resolve_token(tok, lo, hi, names):
    if names is not None and re.fullmatch(r'[a-z]+', tok):
        if tok not in names:
            raise ValueError(f"unknown name: {tok!r}")
        val = names[tok]
    elif re.fullmatch(r'[0-9]+', tok):
        val = int(tok)
    else:
        raise ValueError(f"invalid token: {tok!r}")
    if not (lo <= val <= hi):
        raise ValueError(f"value {val} out of domain [{lo}, {hi}]")
    return val


def _parse_field(field_str, lo, hi, names):
    if field_str == '':
        raise ValueError("empty field")

    is_unrestricted = (field_str == '*')
    values = set()

    for part in field_str.split(','):
        if part == '':
            raise ValueError("empty list element")

        m = _PART_RE.fullmatch(part)
        if not m:
            raise ValueError(f"invalid field part: {part!r}")

        base, step_str = m.group(1), m.group(2)
        step = None
        if step_str is not None:
            step = int(step_str)
            if step <= 0:
                raise ValueError("step must be a positive integer")

        if base == '*':
            start, end = lo, hi
            values.update(range(start, end + 1, step if step else 1))
        elif '-' in base:
            start_tok, end_tok = base.split('-', 1)
            start = _resolve_token(start_tok, lo, hi, names)
            end = _resolve_token(end_tok, lo, hi, names)
            if start > end:
                raise ValueError(f"range start > end: {part!r}")
            values.update(range(start, end + 1, step if step else 1))
        else:
            if step is not None:
                raise ValueError("step is only valid after '*' or a range")
            values.add(_resolve_token(base, lo, hi, names))

    return values, is_unrestricted


def next_fire(expr: str, after: datetime) -> datetime:
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"expression must have exactly 5 fields, got {len(fields)}")

    minute_f, hour_f, dom_f, month_f, dow_f = fields

    minutes, _ = _parse_field(minute_f, 0, 59, None)
    hours, _ = _parse_field(hour_f, 0, 23, None)
    doms, dom_unrestricted = _parse_field(dom_f, 1, 31, None)
    months, _ = _parse_field(month_f, 1, 12, _MONTHS)
    dows, dow_unrestricted = _parse_field(dow_f, 0, 6, _DOWS)

    times = sorted((h, m) for h in hours for m in minutes)

    def date_matches(d):
        if d.month not in months:
            return False
        dom_ok = d.day in doms
        cron_dow = (d.weekday() + 1) % 7  # python Mon=0..Sun=6 -> cron Sun=0..Sat=6
        dow_ok = cron_dow in dows
        if dom_unrestricted and dow_unrestricted:
            return True
        if dom_unrestricted:
            return dow_ok
        if dow_unrestricted:
            return dom_ok
        return dom_ok and dow_ok

    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    d = candidate.date()
    first_day_time = (candidate.hour, candidate.minute)

    for i in range(_MAX_DAYS_SEARCHED):
        if date_matches(d):
            todays_times = [t for t in times if t >= first_day_time] if i == 0 else times
            if todays_times:
                h, m = todays_times[0]
                return datetime(d.year, d.month, d.day, h, m)
        d = d + timedelta(days=1)

    raise ValueError("no matching time found within search bound")
