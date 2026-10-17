"""A cron-like scheduler with three deliberate deviations from vixie cron.

Public API: ``next_fire(expr, after)``.

All datetimes are treated as naive wall-clock UTC. There is no DST and no
timezone math.

The three deviations (see the task spec) are:

  1. day-of-month AND day-of-week is an INTERSECTION when both are restricted
     (standard cron takes the union).
  2. a step ``A-B/s`` is anchored to the range START ``A`` (values A, A+s, ...),
     not to 0.
  3. strict domains and strict rejection -- day-of-week is 0-6 only (``7`` is a
     hard error), uppercase names are invalid, etc.
"""

from datetime import datetime, timedelta

__all__ = ["next_fire"]

# Field domains: (low, high, names). ``names`` maps a lowercase name to its
# numeric value; empty for fields that have no names.
_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DOW_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}

# One spec per field, in expression order.
_FIELDS = [
    ("minute", 0, 59, {}),
    ("hour", 0, 23, {}),
    ("day-of-month", 1, 31, {}),
    ("month", 1, 12, _MONTH_NAMES),
    ("day-of-week", 0, 6, _DOW_NAMES),
]

# A safety bound so an unsatisfiable-but-valid expression (e.g. Feb 30) does
# not loop forever. Ten years is comfortably beyond any real schedule.
_SEARCH_LIMIT = timedelta(days=366 * 10)


def _parse_value(tok, lo, hi, names):
    """Parse a single value: an in-domain number or a lowercase name."""
    if tok in names:
        return names[tok]
    if tok.isdigit():
        v = int(tok)
        if lo <= v <= hi:
            return v
        raise ValueError("value %r out of range [%d,%d]" % (tok, lo, hi))
    raise ValueError("invalid value %r" % (tok,))


def _parse_range(part, lo, hi, names):
    """Parse ``A-B`` into an inclusive (start, end) with start <= end."""
    a_str, sep, b_str = part.partition("-")
    if not sep or a_str == "" or b_str == "":
        raise ValueError("invalid range %r" % (part,))
    start = _parse_value(a_str, lo, hi, names)
    end = _parse_value(b_str, lo, hi, names)
    if start > end:
        raise ValueError("range start > end in %r" % (part,))
    return start, end


def _parse_part(part, lo, hi, names):
    """Parse one comma-separated element into a set of matching values."""
    if part == "":
        raise ValueError("empty field element")
    if part == "*":
        return set(range(lo, hi + 1))

    if "/" in part:
        base, _, step_str = part.partition("/")
        if not step_str.isdigit():
            raise ValueError("invalid step %r" % (step_str,))
        step = int(step_str)
        if step <= 0:
            raise ValueError("step must be positive: %r" % (step_str,))
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            start, end = _parse_range(base, lo, hi, names)
        else:
            # A step may only follow '*' or a range, never a single value.
            raise ValueError("step attached to single value: %r" % (part,))
        # Deviation 2: anchor at ``start``, stride ``step``, up to ``end``.
        return set(range(start, end + 1, step))

    if "-" in part:
        start, end = _parse_range(part, lo, hi, names)
        return set(range(start, end + 1))

    return {_parse_value(part, lo, hi, names)}


def _parse_field(field, lo, hi, names):
    """Parse a whole field into (values_set, restricted_flag).

    Only a bare ``*`` is UNRESTRICTED (Deviation 1).
    """
    if field == "":
        raise ValueError("empty field")
    if field == "*":
        return set(range(lo, hi + 1)), False

    values = set()
    for part in field.split(","):
        values |= _parse_part(part, lo, hi, names)
    return values, True


def _parse(expr):
    """Parse the five-field expression into a match spec."""
    tokens = expr.split()
    if len(tokens) != 5:
        raise ValueError("expression must have exactly 5 fields, got %d"
                         % (len(tokens),))

    parsed = []
    for token, (_, lo, hi, names) in zip(tokens, _FIELDS):
        parsed.append(_parse_field(token, lo, hi, names))

    (minutes, _), (hours, _), (doms, dom_restricted), \
        (months, _), (dows, dow_restricted) = parsed

    return {
        "minutes": minutes,
        "hours": hours,
        "doms": doms,
        "months": months,
        "dows": dows,
        "dom_restricted": dom_restricted,
        "dow_restricted": dow_restricted,
    }


def _cron_dow(dt):
    """Cron day-of-week: 0 = Sunday .. 6 = Saturday."""
    return (dt.weekday() + 1) % 7


def _day_matches(dt, spec):
    """Apply the day-of-month / day-of-week intersection rule (Deviation 1)."""
    dom_ok = dt.day in spec["doms"]
    dow_ok = _cron_dow(dt) in spec["dows"]
    if spec["dom_restricted"] and spec["dow_restricted"]:
        return dom_ok and dow_ok
    if spec["dom_restricted"]:
        return dom_ok
    if spec["dow_restricted"]:
        return dow_ok
    return True


def _first_of_next_month(dt):
    if dt.month == 12:
        return datetime(dt.year + 1, 1, 1)
    return datetime(dt.year, dt.month + 1, 1)


def next_fire(expr, after):
    """Return the earliest datetime strictly after ``after`` matching ``expr``.

    ``expr`` is a five-field cron expression (minute hour day-of-month month
    day-of-week). ``after`` is a naive UTC datetime. The result is a naive UTC
    datetime with second == 0 and microsecond == 0.
    """
    spec = _parse(expr)

    # First candidate: the next whole minute strictly after ``after``.
    cand = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = cand + _SEARCH_LIMIT

    while cand <= limit:
        if cand.month not in spec["months"]:
            cand = _first_of_next_month(cand)
            continue
        if not _day_matches(cand, spec):
            cand = cand.replace(hour=0, minute=0) + timedelta(days=1)
            continue
        if cand.hour not in spec["hours"]:
            cand = cand.replace(minute=0) + timedelta(hours=1)
            continue
        if cand.minute not in spec["minutes"]:
            cand = cand + timedelta(minutes=1)
            continue
        return cand

    raise ValueError("no matching time found within search limit for %r"
                     % (expr,))
