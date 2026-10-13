def parse_duration(text: str) -> int:
    """Parse a human-readable duration string into total seconds.

    Args:
        text: A string like "1h30m", "2d", "1d2h3m4s", "90s"

    Returns:
        Total seconds as a non-negative integer.

    Raises:
        ValueError: For invalid input (empty, unknown units, wrong order, etc.)
    """
    text = text.strip()

    if not text:
        raise ValueError("Empty duration string")

    if any(c.isspace() for c in text):
        raise ValueError("Whitespace inside duration string")

    units = {'d': (86400, 0), 'h': (3600, 1), 'm': (60, 2), 's': (1, 3)}

    total_seconds = 0
    last_order = -1
    seen_units = set()

    i = 0
    while i < len(text):
        if not text[i].isdigit():
            raise ValueError(f"Expected digit at position {i}")

        num_start = i
        while i < len(text) and text[i].isdigit():
            i += 1

        number = int(text[num_start:i])

        if i >= len(text):
            raise ValueError("Number without unit")

        unit = text[i]
        i += 1

        if unit not in units:
            raise ValueError(f"Unknown unit '{unit}'")

        if unit in seen_units:
            raise ValueError(f"Duplicate unit '{unit}'")

        seconds_in_unit, order = units[unit]
        if order < last_order:
            raise ValueError("Units out of order")

        seen_units.add(unit)
        last_order = order
        total_seconds += number * seconds_in_unit

    return total_seconds


def format_duration(seconds: int) -> str:
    """Format seconds into a canonical human-readable duration string.

    Args:
        seconds: A non-negative integer number of seconds.

    Returns:
        A canonical duration string using the largest fitting units,
        with zero-value components omitted (except "0s" when input is 0).

    Raises:
        ValueError: If seconds is negative.
    """
    if seconds < 0:
        raise ValueError("Duration cannot be negative")

    if seconds == 0:
        return "0s"

    units = [('d', 86400), ('h', 3600), ('m', 60), ('s', 1)]

    result = []
    remaining = seconds

    for unit_name, unit_seconds in units:
        if remaining >= unit_seconds:
            count = remaining // unit_seconds
            result.append(f"{count}{unit_name}")
            remaining = remaining % unit_seconds

    return "".join(result)
