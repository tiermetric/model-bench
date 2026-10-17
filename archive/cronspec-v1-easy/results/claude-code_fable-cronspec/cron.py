"""A cron-like scheduler with a deliberately non-standard dialect.

Differences from vixie cron:

1. day-of-month and day-of-week are combined with AND (intersection),
   never OR.
2. A step ``A-B/s`` is anchored at ``A`` (values A, A+s, A+2s, ... <= B),
   not at multiples of ``s``.
3. Strict domains: day-of-week is 0-6 only (0 = Sunday, 7 is invalid),
   names are lowercase only, and any malformed field raises ValueError.
"""

from datetime import datetime, timedelta

__all__ = ["next_fire"]

_MONTH_NAMES = {
    name: number
    for number, name in enumerate(
        "jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1
    )
}
_DOW_NAMES = {
    name: number
    for number, name in enumerate("sun mon tue wed thu fri sat".split())
}

# (field name, domain low, domain high, name table)
_FIELD_SPECS = (
    ("minute", 0, 59, None),
    ("hour", 0, 23, None),
    ("day-of-month", 1, 31, None),
    ("month", 1, 12, _MONTH_NAMES),
    ("day-of-week", 0, 6, _DOW_NAMES),
)

# Search horizon for next_fire. Any satisfiable expression (including
# Feb 29 on a specific weekday) recurs well inside 100 years.
_MAX_SEARCH_DAYS = 366 * 100


def _parse_value(token, low, high, names, field):
    """Parse a single value token (number or lowercase name)."""
    if names is not None and token in names:
        return names[token]
    if token.isdigit():
        try:
            value = int(token)
        except ValueError:
            raise ValueError(
                "invalid token %r in %s field" % (token, field)
            ) from None
        if low <= value <= high:
            return value
        raise ValueError(
            "%s value %d out of domain %d-%d" % (field, value, low, high)
        )
    raise ValueError("invalid token %r in %s field" % (token, field))


def _parse_element(element, low, high, names, field):
    """Parse one comma-list element into a set of matching values."""
    if element == "":
        raise ValueError("empty element in %s field" % field)

    body, slash, step_text = element.partition("/")
    if slash:
        if not step_text.isdigit() or int(step_text) == 0:
            raise ValueError(
                "step %r in %s field is not a positive integer"
                % (step_text, field)
            )
        step = int(step_text)
    else:
        step = 1

    if body == "*":
        start, end = low, high
    elif "-" in body:
        start_text, _, end_text = body.partition("-")
        start = _parse_value(start_text, low, high, names, field)
        end = _parse_value(end_text, low, high, names, field)
        if start > end:
            raise ValueError(
                "range %r in %s field has start > end" % (body, field)
            )
    else:
        if slash:
            raise ValueError(
                "step may only follow '*' or a range, got %r in %s field"
                % (element, field)
            )
        return {_parse_value(body, low, high, names, field)}

    return set(range(start, end + 1, step))


def _parse_field(text, spec):
    field, low, high, names = spec
    values = set()
    for element in text.split(","):
        values |= _parse_element(element, low, high, names, field)
    return values


def next_fire(expr: str, after: datetime) -> datetime:
    """Return the earliest datetime strictly after ``after`` matching ``expr``.

    Times are naive wall-clock UTC; the result is on a minute boundary
    (second == 0, microsecond == 0). Raises ValueError for a malformed
    expression, or if no matching time exists within the search horizon.
    """
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(
            "expression must have exactly 5 fields, got %d" % len(fields)
        )

    minutes, hours, doms, months, dows = (
        _parse_field(text, spec) for text, spec in zip(fields, _FIELD_SPECS)
    )
    sorted_hours = sorted(hours)
    sorted_minutes = sorted(minutes)

    # First candidate: the next whole minute strictly after `after`.
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)

    day = candidate.replace(hour=0, minute=0)
    for _ in range(_MAX_SEARCH_DAYS):
        # Deviation 1: dom and dow always intersect ('*' matches all
        # values, so no unrestricted special case is needed).
        if (
            day.month in months
            and day.day in doms
            and (day.weekday() + 1) % 7 in dows  # cron: 0 = Sunday
        ):
            for hour in sorted_hours:
                for minute in sorted_minutes:
                    fire = day.replace(hour=hour, minute=minute)
                    if fire >= candidate:
                        return fire
        day += timedelta(days=1)

    raise ValueError("expression %r never fires" % expr)
