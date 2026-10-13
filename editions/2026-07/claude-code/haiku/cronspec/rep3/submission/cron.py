from datetime import datetime, timedelta
import calendar


def parse_value(val_str, field_name):
    """Parse a value (number or name)."""
    try:
        return int(val_str)
    except ValueError:
        names = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
            "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
        }
        if val_str != val_str.lower():
            raise ValueError(f"Names must be lowercase: {val_str}")
        if val_str in names:
            return names[val_str]
        raise ValueError(f"Unknown name or value: {val_str}")


def parse_range(range_str, min_val, max_val, field_name):
    """Parse a range and return (a, b)."""
    parts = range_str.split("-")
    if len(parts) != 2:
        raise ValueError(f"Invalid range syntax: {range_str}")

    a = parse_value(parts[0], field_name)
    b = parse_value(parts[1], field_name)

    if not (min_val <= a <= max_val):
        raise ValueError(f"Range start {a} out of domain [{min_val}, {max_val}]")
    if not (min_val <= b <= max_val):
        raise ValueError(f"Range end {b} out of domain [{min_val}, {max_val}]")

    return a, b


def generate_stepped_range(a, b, s, min_val, max_val):
    """Generate values for A-B/s, handling wraps (Rule 5)."""
    values = set()
    if a <= b:
        # No wrap: step from a to b
        current = a
        while current <= b:
            values.add(current)
            current += s
    else:
        # Wrap: a, a+1, ..., max_val, min_val, ..., b (Rule 4)
        wrapped = list(range(a, max_val + 1)) + list(range(min_val, b + 1))
        for i in range(0, len(wrapped), s):
            values.add(wrapped[i])

    return values


def parse_field(spec, min_val, max_val, field_name):
    """Parse a single cron field and return a set of valid values."""

    if spec == "*":
        return set(range(min_val, max_val + 1))

    values = set()

    for item in spec.split(","):
        if not item:
            raise ValueError("Empty element in list")

        if "/" in item:
            # Step expression: */s or A-B/s
            if item.startswith("*/"):
                try:
                    s = int(item[2:])
                except ValueError:
                    raise ValueError(f"Invalid step value: {item[2:]}")
                if s <= 0:
                    raise ValueError("Step must be positive")
                current = min_val
                while current <= max_val:
                    values.add(current)
                    current += s
            elif "-" in item:
                # A-B/s format
                try:
                    range_part, s_str = item.rsplit("/", 1)
                    s = int(s_str)
                except ValueError:
                    raise ValueError(f"Invalid step value: {item}")
                if s <= 0:
                    raise ValueError("Step must be positive")
                a, b = parse_range(range_part, min_val, max_val, field_name)
                values.update(generate_stepped_range(a, b, s, min_val, max_val))
            else:
                # single/s - error
                raise ValueError("Step not allowed on single value")
        elif "-" in item:
            # Range: A-B (Rule 4: handles wraps like 50-10)
            a, b = parse_range(item, min_val, max_val, field_name)
            if a <= b:
                values.update(range(a, b + 1))
            else:
                # Wrap-around: {a, a+1, ..., max_val, min_val, ..., b}
                values.update(range(a, max_val + 1))
                values.update(range(min_val, b + 1))
        else:
            # Single value
            val = parse_value(item, field_name)
            if not (min_val <= val <= max_val):
                raise ValueError(f"Value {val} out of domain [{min_val}, {max_val}]")
            values.add(val)

    return values


def next_fire(expr: str, after: datetime) -> datetime:
    """
    Return the earliest datetime STRICTLY AFTER `after` that matches the cron expression.
    All times are UTC, tz-naive. Result has second == 0 and microsecond == 0.
    """

    expr = expr.strip()
    if not expr:
        raise ValueError("Empty expression")

    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"Expected 5 fields, got {len(fields)}")

    minute_spec, hour_spec, dom_spec, month_spec, dow_spec = fields

    # Parse fields
    try:
        minutes = parse_field(minute_spec, 0, 59, "minute")
        hours = parse_field(hour_spec, 0, 23, "hour")
        months = parse_field(month_spec, 1, 12, "month")
        dows = parse_field(dow_spec, 0, 6, "day-of-week")
    except ValueError as e:
        raise ValueError(str(e))

    # Handle day-of-month (can be L or *)
    if dom_spec == "L":
        doms = "L"
        dom_restricted = True
    elif dom_spec == "*":
        doms = "*"
        dom_restricted = False
    else:
        # Check for invalid L usage
        if "L" in dom_spec:
            raise ValueError("L can only be used alone in day-of-month field")
        try:
            doms = parse_field(dom_spec, 1, 31, "day-of-month")
        except ValueError as e:
            raise ValueError(str(e))
        dom_restricted = True

    dow_restricted = dow_spec != "*"

    # Start from the next minute strictly after `after`
    current = after.replace(second=0, microsecond=0)
    if current <= after:
        current += timedelta(minutes=1)
    else:
        # current is already after `after` (had seconds/microseconds), no need to add
        pass

    # Search forward (limit to prevent infinite loops)
    for _ in range(4 * 365 * 24 * 60):
        year, month, day = current.year, current.month, current.day
        hour, minute = current.hour, current.minute

        # Quick checks: minute, hour, month
        if minute not in minutes or hour not in hours or month not in months:
            current += timedelta(minutes=1)
            continue

        # Day matching (Deviation 1: dom AND dow is intersection, not union)
        dow = current.weekday()  # 0=Mon, 6=Sun
        cron_dow = (dow + 1) % 7  # Convert to cron format (0=Sun, 6=Sat)

        # Check if day-of-month matches
        if doms == "L":
            # Rule 6: L = last day of month
            last_day = calendar.monthrange(year, month)[1]
            matches_dom = (day == last_day)
        else:
            matches_dom = (day in doms) if dom_restricted else True

        # Check if day-of-week matches
        matches_dow = (cron_dow in dows) if dow_restricted else True

        # Apply intersection rule (Deviation 1)
        if dom_restricted and dow_restricted:
            # Both must match
            day_ok = matches_dom and matches_dow
        elif dom_restricted:
            # Only dom matters
            day_ok = matches_dom
        elif dow_restricted:
            # Only dow matters
            day_ok = matches_dow
        else:
            # Both unrestricted: every day matches
            day_ok = True

        if day_ok:
            return current

        current += timedelta(minutes=1)

    raise ValueError("Could not find matching datetime within search limit")
