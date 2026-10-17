from datetime import datetime, timedelta


def next_fire(expr: str, after: datetime) -> datetime:
    """
    Return the earliest datetime STRICTLY AFTER `after` that matches the cron expression.
    All times are UTC, tz-naive. Result has second==0 and microsecond==0.
    """
    minute_set, hour_set, dom_set, month_set, dow_set, dom_restricted, dow_restricted = parse_expression(expr)

    # Start from the next whole minute after `after`
    if after.second != 0 or after.microsecond != 0:
        candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    else:
        candidate = after + timedelta(minutes=1)

    # Search for the next matching time (limit iterations as safety measure)
    max_iterations = 10_000_000  # ~19 years worth of minutes
    for _ in range(max_iterations):
        if matches(candidate, minute_set, hour_set, dom_set, month_set, dow_set,
                   dom_restricted, dow_restricted):
            return candidate
        candidate += timedelta(minutes=1)

    raise RuntimeError("No matching time found within reasonable bounds")


def parse_expression(expr: str) -> tuple:
    """
    Parse a cron expression and return a tuple of sets and restriction flags.
    Returns: (minute_set, hour_set, dom_set, month_set, dow_set, dom_restricted, dow_restricted)
    """
    expr = expr.strip()
    fields = expr.split()

    if len(fields) != 5:
        raise ValueError(f"Expected exactly 5 fields, got {len(fields)}")

    minute_set, _ = parse_field(fields[0], 0, 59)
    hour_set, _ = parse_field(fields[1], 0, 23)
    dom_set, dom_restricted = parse_field(fields[2], 1, 31)
    month_set, _ = parse_field(fields[3], 1, 12, month_names=True)
    dow_set, dow_restricted = parse_field(fields[4], 0, 6, dow_names=True)

    return minute_set, hour_set, dom_set, month_set, dow_set, dom_restricted, dow_restricted


def parse_field(field: str, min_val: int, max_val: int, month_names=False, dow_names=False) -> tuple:
    """
    Parse a single cron field and return (set of matching values, is_restricted).
    is_restricted is False only for a bare `*`.
    """
    if not field:
        raise ValueError("Empty field")

    if field == "*":
        return set(range(min_val, max_val + 1)), False

    # Handle comma-separated list
    parts = field.split(",")
    result = set()

    for part in parts:
        if not part:
            raise ValueError("Empty list element")

        if "/" in part:
            # Step pattern: `*/s` or `A-B/s`
            range_part, step_part = part.rsplit("/", 1)

            try:
                step = int(step_part)
            except ValueError:
                raise ValueError(f"Invalid step: {step_part}")

            if step <= 0:
                raise ValueError(f"Step must be positive, got {step}")

            if range_part == "*":
                # `*/s` - anchor to domain floor
                start = min_val
                end = max_val
            elif "-" in range_part:
                # `A-B/s`
                range_parts = range_part.split("-", 1)
                if len(range_parts) != 2:
                    raise ValueError(f"Invalid range: {range_part}")
                start_str, end_str = range_parts
                start = parse_value(start_str, min_val, max_val, month_names, dow_names)
                end = parse_value(end_str, min_val, max_val, month_names, dow_names)
                if start > end:
                    raise ValueError(f"Range start > end: {range_part}")
            else:
                # Step on a single value is not allowed
                raise ValueError(f"Step cannot be applied to single value: {part}")

            # Add values: start, start+step, start+2*step, ...
            value = start
            while value <= end:
                result.add(value)
                value += step
        elif "-" in part:
            # Range pattern: `A-B`
            range_parts = part.split("-", 1)
            if len(range_parts) != 2:
                raise ValueError(f"Invalid range: {part}")
            start_str, end_str = range_parts
            start = parse_value(start_str, min_val, max_val, month_names, dow_names)
            end = parse_value(end_str, min_val, max_val, month_names, dow_names)
            if start > end:
                raise ValueError(f"Range start > end: {part}")
            result.update(range(start, end + 1))
        else:
            # Single value
            value = parse_value(part, min_val, max_val, month_names, dow_names)
            result.add(value)

    return result, True


def parse_value(value_str: str, min_val: int, max_val: int, month_names=False, dow_names=False) -> int:
    """
    Parse a single value (number or name) and return its integer representation.
    """
    if not value_str:
        raise ValueError("Empty value")

    # Try to parse as integer
    if value_str.isdigit():
        value = int(value_str)
        if not (min_val <= value <= max_val):
            raise ValueError(f"Value {value} out of range [{min_val}, {max_val}]")
        return value

    # If it's alphabetic (could be a name), check case
    if value_str.isalpha():
        if value_str != value_str.lower():
            raise ValueError(f"Names must be lowercase: {value_str}")

    # Try to parse as name
    if month_names:
        month_map = {
            'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
            'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
        }
        if value_str in month_map:
            return month_map[value_str]

    if dow_names:
        dow_map = {
            'sun': 0, 'mon': 1, 'tue': 2, 'wed': 3, 'thu': 4, 'fri': 5, 'sat': 6
        }
        if value_str in dow_map:
            return dow_map[value_str]

    # If it's not a number and not a recognized name, it's invalid
    raise ValueError(f"Invalid value: {value_str}")


def matches(dt: datetime, minute_set: set, hour_set: set, dom_set: set, month_set: set,
            dow_set: set, dom_restricted: bool, dow_restricted: bool) -> bool:
    """
    Check if a datetime matches the cron expression.
    Implements Deviation 1: day-of-month AND day-of-week is intersection.
    """
    # Check minute, hour, month
    if dt.minute not in minute_set:
        return False
    if dt.hour not in hour_set:
        return False
    if dt.month not in month_set:
        return False

    # Check day of month and day of week with intersection logic
    day_of_month = dt.day
    # Convert from Python's weekday (0=Monday, 6=Sunday) to cron format (0=Sunday, 6=Saturday)
    day_of_week = (dt.weekday() + 1) % 7

    if dom_restricted and dow_restricted:
        # Both restricted: day must match BOTH (intersection)
        return day_of_month in dom_set and day_of_week in dow_set
    elif dom_restricted:
        # Only day-of-month restricted
        return day_of_month in dom_set
    elif dow_restricted:
        # Only day-of-week restricted
        return day_of_week in dow_set
    else:
        # Both unrestricted: every day matches
        return True
