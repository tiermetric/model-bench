from datetime import datetime, timedelta
import calendar


def next_fire(expr: str, after: datetime) -> datetime:
    """Return the earliest datetime STRICTLY AFTER `after` matching the cron expression.

    All times are UTC, tz-naive. The result has second=0 and microsecond=0.
    """
    parsed = parse_cron_expression(expr)

    # Start from the next whole minute
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)

    # Search forward (reasonable limit: ~4 years)
    max_iterations = 4 * 366 * 24 * 60
    for _ in range(max_iterations):
        if matches(candidate, parsed):
            return candidate
        candidate += timedelta(minutes=1)

    raise RuntimeError("No matching datetime found within search window")


def parse_cron_expression(expr: str):
    """Parse a 5-field cron expression into a structured dict."""
    fields = expr.strip().split()
    # Filter out empty strings from multiple spaces
    fields = [f for f in fields if f]

    if len(fields) != 5:
        raise ValueError(f"Expected 5 fields, got {len(fields)}")

    minute_field = parse_field(fields[0], 0, 59, 'minute')
    hour_field = parse_field(fields[1], 0, 23, 'hour')
    day_field = parse_day_field(fields[2])
    month_field = parse_field(fields[3], 1, 12, 'month')
    dow_field = parse_field(fields[4], 0, 6, 'dow')

    return {
        'minute': minute_field,
        'hour': hour_field,
        'day': day_field,
        'month': month_field,
        'dow': dow_field,
    }


def parse_field(field_str, min_val, max_val, field_type):
    """Parse a cron field (minute, hour, month, or dow)."""
    if field_str == '*':
        return {'type': 'unrestricted'}

    # Handle comma-separated lists
    if ',' in field_str:
        items = field_str.split(',')
        if any(item == '' for item in items):
            raise ValueError("Empty list element")
        values = set()
        for item in items:
            parsed = parse_field_element(item, min_val, max_val, field_type)
            if isinstance(parsed, set):
                values.update(parsed)
            else:
                values.add(parsed)
        return {'type': 'list', 'values': values}

    # Single element
    parsed = parse_field_element(field_str, min_val, max_val, field_type)
    if isinstance(parsed, set):
        return {'type': 'range', 'values': parsed}
    else:
        return {'type': 'value', 'value': parsed}


def parse_field_element(element, min_val, max_val, field_type):
    """Parse a single element: value, range, or step."""
    if '/' in element:
        return parse_step(element, min_val, max_val, field_type)
    elif '-' in element:
        return parse_range(element, min_val, max_val, field_type)
    else:
        return parse_single_value(element, min_val, max_val, field_type)


def parse_step(element, min_val, max_val, field_type):
    """Parse a step expression: */s or A-B/s."""
    parts = element.split('/')
    if len(parts) != 2:
        raise ValueError("Invalid step syntax")

    base, step_str = parts

    try:
        step = int(step_str)
    except ValueError:
        raise ValueError("Invalid step value")

    if step <= 0:
        raise ValueError("Step must be positive")

    if base == '*':
        # */s → start from domain min
        values = set(range(min_val, max_val + 1, step))
        return values
    elif '-' in base:
        # A-B/s
        return parse_range_with_step(base, step, min_val, max_val, field_type)
    else:
        # step on single value
        raise ValueError("Step can only be applied to * or a range")


def parse_range_with_step(range_str, step, min_val, max_val, field_type):
    """Parse A-B/s, handling wraparound."""
    parts = range_str.split('-', 1)
    if len(parts) != 2:
        raise ValueError("Invalid range")

    start = parse_single_value(parts[0], min_val, max_val, field_type)
    end = parse_single_value(parts[1], min_val, max_val, field_type)

    if start <= end:
        # Non-wrapped range: {start, start+step, start+2*step, ...} up to end
        values = {start + i * step for i in range((end - start) // step + 1) if start + i * step <= end}
    else:
        # Wrapped range: build list and step through
        wrapped = list(range(start, max_val + 1)) + list(range(min_val, end + 1))
        values = {wrapped[i * step] for i in range(len(wrapped)) if i * step < len(wrapped)}

    return values


def parse_range(range_str, min_val, max_val, field_type):
    """Parse A-B, handling wraparound."""
    parts = range_str.split('-', 1)
    if len(parts) != 2:
        raise ValueError("Invalid range")

    start = parse_single_value(parts[0], min_val, max_val, field_type)
    end = parse_single_value(parts[1], min_val, max_val, field_type)

    if start <= end:
        return set(range(start, end + 1))
    else:
        # Wrapped range
        return set(range(start, max_val + 1)) | set(range(min_val, end + 1))


def parse_single_value(value_str, min_val, max_val, field_type):
    """Parse a single numeric or named value."""
    # Try numeric
    try:
        val = int(value_str)
    except ValueError:
        # Try named
        if field_type == 'month':
            months = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
                      'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}
            val = months.get(value_str)
            if val is None:
                raise ValueError(f"Unknown month name: {value_str}")
        elif field_type == 'dow':
            days = {'sun': 0, 'mon': 1, 'tue': 2, 'wed': 3, 'thu': 4, 'fri': 5, 'sat': 6}
            val = days.get(value_str)
            if val is None:
                raise ValueError(f"Unknown day name: {value_str}")
        else:
            raise ValueError(f"Invalid value: {value_str}")

    # Check domain
    if val < min_val or val > max_val:
        raise ValueError(f"Value out of domain")

    return val


def parse_day_field(field_str):
    """Parse the day-of-month field (special handling for L)."""
    if field_str == 'L':
        return {'type': 'last_day'}

    if field_str == '*':
        return {'type': 'unrestricted'}

    # L cannot be combined with anything
    if 'L' in field_str or 'l' in field_str:
        raise ValueError("L cannot be combined")

    # Handle comma-separated lists
    if ',' in field_str:
        items = field_str.split(',')
        if any(item == '' for item in items):
            raise ValueError("Empty list element")
        values = set()
        for item in items:
            parsed = parse_day_element(item)
            if isinstance(parsed, set):
                values.update(parsed)
            else:
                values.add(parsed)
        return {'type': 'list', 'values': values}

    # Single element
    parsed = parse_day_element(field_str)
    if isinstance(parsed, set):
        return {'type': 'range', 'values': parsed}
    else:
        return {'type': 'value', 'value': parsed}


def parse_day_element(element):
    """Parse a single day-of-month element."""
    if '/' in element:
        return parse_day_step(element)
    elif '-' in element:
        return parse_day_range(element)
    else:
        return parse_day_value(element)


def parse_day_step(element):
    """Parse A-B/s or */s for day-of-month."""
    parts = element.split('/')
    if len(parts) != 2:
        raise ValueError("Invalid step syntax")

    base, step_str = parts

    try:
        step = int(step_str)
    except ValueError:
        raise ValueError("Invalid step value")

    if step <= 0:
        raise ValueError("Step must be positive")

    if base == '*':
        # */s on [1, 31]
        values = set(range(1, 32, step))
        return values
    elif '-' in base:
        # A-B/s
        parts = base.split('-', 1)
        start = parse_day_value(parts[0])
        end = parse_day_value(parts[1])

        if start <= end:
            values = {start + i * step for i in range((end - start) // step + 1) if start + i * step <= end}
        else:
            wrapped = list(range(start, 32)) + list(range(1, end + 1))
            values = {wrapped[i * step] for i in range(len(wrapped)) if i * step < len(wrapped)}

        return values
    else:
        raise ValueError("Step can only be applied to * or a range")


def parse_day_range(range_str):
    """Parse A-B for day-of-month, handling wraparound."""
    parts = range_str.split('-', 1)
    if len(parts) != 2:
        raise ValueError("Invalid range")

    start = parse_day_value(parts[0])
    end = parse_day_value(parts[1])

    if start <= end:
        return set(range(start, end + 1))
    else:
        return set(range(start, 32)) | set(range(1, end + 1))


def parse_day_value(value_str):
    """Parse a single day value."""
    try:
        val = int(value_str)
    except ValueError:
        raise ValueError("Invalid day value")

    if val < 1 or val > 31:
        raise ValueError("Day out of domain")

    return val


def matches(dt, parsed):
    """Check if a datetime matches the parsed cron expression."""
    minute = dt.minute
    hour = dt.hour
    day = dt.day
    month = dt.month

    # Convert Python weekday (Monday=0) to cron (Sunday=0)
    dow = (dt.weekday() + 1) % 7

    # Check minute, hour, month (must all match)
    if not field_matches(minute, parsed['minute']):
        return False
    if not field_matches(hour, parsed['hour']):
        return False
    if not field_matches(month, parsed['month']):
        return False

    # Handle day-of-month and day-of-week (Deviation 1: intersection)
    day_spec = parsed['day']
    dow_spec = parsed['dow']

    day_restricted = day_spec['type'] != 'unrestricted'
    dow_restricted = dow_spec['type'] != 'unrestricted'

    if day_restricted and dow_restricted:
        # Both restricted: intersection (must match both)
        return day_matches(day, day_spec, dt) and field_matches(dow, dow_spec)
    elif day_restricted:
        # Only day restricted
        return day_matches(day, day_spec, dt)
    elif dow_restricted:
        # Only dow restricted
        return field_matches(dow, dow_spec)
    else:
        # Neither restricted
        return True


def field_matches(value, spec):
    """Check if a value matches a field spec."""
    if spec['type'] == 'unrestricted':
        return True
    elif spec['type'] == 'value':
        return value == spec['value']
    elif spec['type'] in ('list', 'range'):
        return value in spec['values']
    return False


def day_matches(day, spec, dt):
    """Check if a day matches the day-of-month spec."""
    if spec['type'] == 'unrestricted':
        return True
    elif spec['type'] == 'last_day':
        _, last_day = calendar.monthrange(dt.year, dt.month)
        return day == last_day
    elif spec['type'] == 'value':
        return day == spec['value']
    elif spec['type'] in ('list', 'range'):
        return day in spec['values']
    return False
