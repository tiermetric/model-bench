from datetime import datetime, timedelta
import calendar

MONTH_NAMES = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4,
    'may': 5, 'jun': 6, 'jul': 7, 'aug': 8,
    'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

DOW_NAMES = {
    'sun': 0, 'mon': 1, 'tue': 2, 'wed': 3,
    'thu': 4, 'fri': 5, 'sat': 6,
}


def next_fire(expr: str, after: datetime) -> datetime:
    """Find the next datetime that matches the cron expression after the given time.

    Returns the earliest datetime strictly after `after` that matches `expr`.
    All times are UTC, tz-naive. Result has second=0 and microsecond=0.
    """
    fields = _parse_expression(expr)

    # Start from next minute boundary
    current = after.replace(second=0, microsecond=0) + timedelta(minutes=1)

    # Search forward (limit to ~4 years to prevent infinite loops)
    end_limit = after + timedelta(days=365*4)

    while current <= end_limit:
        if _matches(current, fields):
            return current
        current += timedelta(minutes=1)

    raise ValueError("No matching datetime found within search limit")


def _parse_expression(expr: str):
    """Parse a 5-field cron expression."""
    expr = expr.strip()

    if not expr:
        raise ValueError("Empty expression")

    fields = expr.split()

    if len(fields) != 5:
        raise ValueError(f"Expected 5 fields, got {len(fields)}")

    return {
        'minute': _parse_field(fields[0], 0, 59, None, 'minute'),
        'hour': _parse_field(fields[1], 0, 23, None, 'hour'),
        'dom': _parse_field(fields[2], 1, 31, None, 'dom'),
        'month': _parse_field(fields[3], 1, 12, MONTH_NAMES, 'month'),
        'dow': _parse_field(fields[4], 0, 6, DOW_NAMES, 'dow'),
    }


def _parse_field(field_str, min_val, max_val, names, field_type):
    """Parse a single field of a cron expression."""

    # Special case: L only for day-of-month
    if field_str == 'L':
        if field_type != 'dom':
            raise ValueError(f"L is only valid in day-of-month field")
        return {'type': 'L'}

    if field_str == '*':
        return {'type': '*'}

    # If we get here, L should not appear anywhere else
    if 'L' in field_str:
        raise ValueError(f"L cannot be combined with other values or used outside day-of-month field")

    # Check for invalid characters (names must be lowercase if present)
    valid_chars = set('-,/*0123456789')
    if names:
        valid_chars |= set('abcdefghijklmnopqrstuvwxyz')

    if not all(c in valid_chars for c in field_str):
        raise ValueError(f"Invalid character in field")

    # Parse comma-separated list
    parts = field_str.split(',')
    values = set()

    for part in parts:
        if part == '':
            raise ValueError("Empty list element")

        part_values = _parse_list_element(part, min_val, max_val, names)
        values.update(part_values)

    return {'type': 'values', 'values': values}


def _parse_list_element(element, min_val, max_val, names):
    """Parse a single element of a comma-separated list (may include ranges/steps)."""

    if '/' in element:
        # Step notation
        parts = element.split('/')
        if len(parts) != 2:
            raise ValueError(f"Invalid step: {element}")

        base, step_str = parts

        try:
            step = int(step_str)
        except ValueError:
            raise ValueError(f"Invalid step value: {step_str}")

        if step <= 0:
            raise ValueError(f"Step must be positive, got {step}")

        if base == '*':
            # */s notation: start from min_val, stride s
            range_values = list(range(min_val, max_val + 1, step))
        elif '-' in base:
            # A-B/s notation
            full_range = _parse_range(base, min_val, max_val, names)
            range_values = [full_range[i] for i in range(0, len(full_range), step)]
        else:
            # single/s is an error
            raise ValueError(f"Step can only apply to * or a range")

        return set(range_values)

    elif '-' in element:
        # Range notation
        return set(_parse_range(element, min_val, max_val, names))

    else:
        # Single value
        val = _parse_value(element, min_val, max_val, names)
        return {val}


def _parse_value(val_str, min_val, max_val, names):
    """Parse a single value (number or name)."""

    try:
        val = int(val_str)
    except ValueError:
        # Try as name
        if names and val_str in names:
            return names[val_str]
        elif names:
            raise ValueError(f"Invalid name or value: {val_str}")
        else:
            raise ValueError(f"Invalid value: {val_str}")

    if val < min_val or val > max_val:
        raise ValueError(f"Value {val} out of range [{min_val}, {max_val}]")

    return val


def _parse_range(range_str, min_val, max_val, names):
    """Parse a range A-B, handling wrap-around (Rule 4)."""

    parts = range_str.split('-')

    if len(parts) != 2:
        raise ValueError(f"Invalid range: {range_str}")

    start_str, end_str = parts

    if not start_str or not end_str:
        raise ValueError(f"Invalid range: {range_str}")

    start = _parse_value(start_str, min_val, max_val, names)
    end = _parse_value(end_str, min_val, max_val, names)

    if start <= end:
        return list(range(start, end + 1))
    else:
        # Wrap-around range (Rule 4)
        return list(range(start, max_val + 1)) + list(range(min_val, end + 1))


def _matches(dt, fields):
    """Check if a datetime matches the cron fields."""

    # Check minute, hour, month
    if dt.minute not in _get_field_values(fields['minute'], 0, 59):
        return False

    if dt.hour not in _get_field_values(fields['hour'], 0, 23):
        return False

    if dt.month not in _get_field_values(fields['month'], 1, 12):
        return False

    # Check day (Deviation 1: special handling for dom and dow)
    dom_spec = fields['dom']
    dow_spec = fields['dow']

    dom_unrestricted = dom_spec['type'] == '*'
    dow_unrestricted = dow_spec['type'] == '*'

    if dom_unrestricted and dow_unrestricted:
        # Both unrestricted, day matches
        return True

    if dom_unrestricted:
        # Only dow restricted
        return _check_dow(dt, dow_spec)

    if dow_unrestricted:
        # Only dom restricted
        return _check_dom(dt, dom_spec)

    # Both restricted: INTERSECTION (Deviation 1)
    return _check_dom(dt, dom_spec) and _check_dow(dt, dow_spec)


def _get_field_values(spec, min_val, max_val):
    """Get the set of values for a field spec."""

    if spec['type'] == '*':
        return set(range(min_val, max_val + 1))

    return spec['values']


def _check_dom(dt, dom_spec):
    """Check if a datetime matches the day-of-month spec."""

    if dom_spec['type'] == 'L':
        # Last day of month (Rule 6)
        last_day = calendar.monthrange(dt.year, dt.month)[1]
        return dt.day == last_day

    return dt.day in dom_spec['values']


def _check_dow(dt, dow_spec):
    """Check if a datetime matches the day-of-week spec."""

    # Python's weekday: Monday=0, Sunday=6
    # Cron's dow: Sunday=0, Saturday=6
    # Convert: cron_dow = (python_weekday + 1) % 7
    python_dow = dt.weekday()
    cron_dow = (python_dow + 1) % 7

    return cron_dow in dow_spec['values']
