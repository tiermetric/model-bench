import re


def parse_duration(text: str) -> int:
    text = text.strip()

    if not text:
        raise ValueError("Empty duration string")

    if any(c.isspace() for c in text):
        raise ValueError("Whitespace inside duration string")

    pattern = r'(\d+)([a-zA-Z])'
    matches = re.findall(pattern, text)

    matched_text = ''.join([''.join(m) for m in matches])
    if matched_text != text:
        raise ValueError("Invalid duration format")

    unit_seconds = {'d': 86400, 'h': 3600, 'm': 60, 's': 1}
    unit_order = {'d': 0, 'h': 1, 'm': 2, 's': 3}

    last_order = -1
    total_seconds = 0
    seen_units = set()

    for value_str, unit in matches:
        if unit not in unit_seconds:
            raise ValueError(f"Unknown or uppercase unit: {unit}")

        if unit in seen_units:
            raise ValueError(f"Duplicate unit: {unit}")
        seen_units.add(unit)

        current_order = unit_order[unit]
        if current_order <= last_order:
            raise ValueError("Units out of order")
        last_order = current_order

        value = int(value_str)
        if value < 0:
            raise ValueError("Negative value")

        total_seconds += value * unit_seconds[unit]

    return total_seconds


def format_duration(seconds: int) -> str:
    if seconds < 0:
        raise ValueError("Negative duration")

    if seconds == 0:
        return "0s"

    units = [
        ('d', 86400),
        ('h', 3600),
        ('m', 60),
        ('s', 1)
    ]

    result = []
    remaining = seconds

    for unit, unit_seconds in units:
        if remaining >= unit_seconds:
            count = remaining // unit_seconds
            remaining = remaining % unit_seconds
            result.append(f"{count}{unit}")

    return ''.join(result)
