"""A cron-like scheduler: next_fire(expr, after) -> datetime.

See module docstring in the task spec for the full grammar and the three
deviations from standard vixie-cron semantics this implementation makes.
"""

import calendar
import re
from datetime import datetime, timedelta

_NUM_RE = re.compile(r'^\d+$')

_MONTH_NAMES = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}
_DOW_NAMES = {
    'sun': 0, 'mon': 1, 'tue': 2, 'wed': 3, 'thu': 4, 'fri': 5, 'sat': 6,
}

_MAX_DAYS = 20000  # generous search horizon (~54 years); cheap since it's just date math


def _resolve_value(token, lo, hi, names):
    if _NUM_RE.match(token):
        value = int(token)
    elif names is not None and token in names:
        value = names[token]
    else:
        raise ValueError(f"unknown token: {token!r}")
    if not (lo <= value <= hi):
        raise ValueError(f"value out of domain: {token!r}")
    return value


def _ordered_range(a, b, lo, hi):
    # Rule 4: A > B wraps through the domain max back down to B.
    if a <= b:
        return list(range(a, b + 1))
    return list(range(a, hi + 1)) + list(range(lo, b + 1))


def _parse_element(elem, lo, hi, names):
    if elem == '':
        raise ValueError("empty list element")

    if '/' in elem:
        slash_parts = elem.split('/')
        if len(slash_parts) != 2:
            raise ValueError(f"invalid step token: {elem!r}")
        base, step_str = slash_parts
        if not _NUM_RE.match(step_str):
            raise ValueError(f"invalid step value: {step_str!r}")
        step = int(step_str)
        if step <= 0:
            raise ValueError(f"step must be positive: {elem!r}")
        if base == '*':
            a, b = lo, hi
        elif '-' in base:
            a_str, b_str = base.split('-', 1)
            a = _resolve_value(a_str, lo, hi, names)
            b = _resolve_value(b_str, lo, hi, names)
        else:
            # Deviation 2 / Rule 5: only * or a range may carry a step.
            raise ValueError(f"step only allowed on * or a range: {elem!r}")
        # Deviation 2 / Rule 5: anchor the stride at the range start, not 0.
        seq = _ordered_range(a, b, lo, hi)
        return set(seq[0::step])

    if elem == '*':
        return set(range(lo, hi + 1))
    if '-' in elem:
        a_str, b_str = elem.split('-', 1)
        a = _resolve_value(a_str, lo, hi, names)
        b = _resolve_value(b_str, lo, hi, names)
        return set(_ordered_range(a, b, lo, hi))
    return {_resolve_value(elem, lo, hi, names)}


def _parse_field(field_str, lo, hi, names, allow_L=False):
    if field_str == 'L':
        if allow_L:
            return {'restricted': True, 'is_L': True, 'values': None}
        raise ValueError("L is only valid in the day-of-month field")

    restricted = field_str != '*'
    values = set()
    for elem in field_str.split(','):
        values |= _parse_element(elem, lo, hi, names)
    return {'restricted': restricted, 'is_L': False, 'values': frozenset(values)}


def _parse_expr(expr):
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"expression must have exactly 5 fields, got {len(parts)}")
    minute_f = _parse_field(parts[0], 0, 59, None)
    hour_f = _parse_field(parts[1], 0, 23, None)
    dom_f = _parse_field(parts[2], 1, 31, None, allow_L=True)
    month_f = _parse_field(parts[3], 1, 12, _MONTH_NAMES)
    dow_f = _parse_field(parts[4], 0, 6, _DOW_NAMES)
    return minute_f, hour_f, dom_f, month_f, dow_f


def _day_matches(d, dom_f, dow_f):
    dom_restricted = dom_f['restricted']
    dow_restricted = dow_f['restricted']

    if dom_f['is_L']:
        last_day = calendar.monthrange(d.year, d.month)[1]
        dom_match = d.day == last_day
    elif dom_restricted:
        dom_match = d.day in dom_f['values']
    else:
        dom_match = True

    if dow_restricted:
        cron_dow = d.isoweekday() % 7  # Sunday(7) -> 0, Monday(1) -> 1, ...
        dow_match = cron_dow in dow_f['values']
    else:
        dow_match = True

    # Deviation 1: when both fields are restricted it's an intersection,
    # not the standard-cron union.
    if dom_restricted and dow_restricted:
        return dom_match and dow_match
    if dom_restricted:
        return dom_match
    if dow_restricted:
        return dow_match
    return True


def _first_time(hour_list, minute_list, bound):
    if bound is None:
        return (hour_list[0], minute_list[0])
    bound_hour, bound_minute = bound
    for h in hour_list:
        if h < bound_hour:
            continue
        if h == bound_hour:
            for m in minute_list:
                if m >= bound_minute:
                    return (h, m)
            continue
        return (h, minute_list[0])
    return None


def next_fire(expr, after):
    minute_f, hour_f, dom_f, month_f, dow_f = _parse_expr(expr)
    minute_list = sorted(minute_f['values'])
    hour_list = sorted(hour_f['values'])
    month_values = month_f['values']

    start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    current_date = start.date()
    bound = (start.hour, start.minute)

    for _ in range(_MAX_DAYS):
        if current_date.month in month_values and _day_matches(current_date, dom_f, dow_f):
            t = _first_time(hour_list, minute_list, bound)
            if t is not None:
                return datetime(current_date.year, current_date.month, current_date.day, t[0], t[1])
        current_date += timedelta(days=1)
        bound = None

    raise ValueError(f"no matching time found for expression: {expr!r}")
