import re


def parse_duration(text: str) -> int:
    """Parse a human-readable duration string into total seconds.

    Format: concatenated segments like "1h30m", "2d4h", "90s", "1d2h3m4s"
    Units: d=86400s, h=3600s, m=60s, s=1s (lowercase only)
    Rules:
    - Numbers are non-negative integers with no upper bound
    - Leading zeros allowed: "01h" == "1h"
    - Units must be in strictly descending order (d > h > m > s)
    - Each unit may appear at most once
    - Leading/trailing whitespace ignored; internal whitespace invalid

    Raises ValueError for: empty input, unknown/uppercase units, bare numbers,
    non-integer/negative numbers, internal whitespace, out-of-order or duplicate units.
    """
    text = text.strip()

    if not text:
        raise ValueError("Empty or whitespace-only input")

    if any(c.isspace() for c in text):
        raise ValueError("Whitespace inside the string is invalid")

    # Parse segments using regex: digits followed by lowercase unit letter
    pattern = r'(\d+)([dhms])'
    matches = re.findall(pattern, text)

    # Verify the regex matched the entire input
    matched_text = ''.join(f"{num}{unit}" for num, unit in matches)
    if matched_text != text:
        raise ValueError("Invalid duration string")

    if not matches:
        raise ValueError("No valid duration segments found")

    # Verify unit order and check for duplicates
    unit_order = {'d': 0, 'h': 1, 'm': 2, 's': 3}
    seen_units = set()
    last_order = -1

    total_seconds = 0
    unit_to_seconds = {'d': 86400, 'h': 3600, 'm': 60, 's': 1}

    for num_str, unit in matches:
        if unit in seen_units:
            raise ValueError(f"Duplicate unit: {unit}")
        seen_units.add(unit)

        current_order = unit_order[unit]
        if current_order <= last_order:
            raise ValueError("Units must be in strictly descending order")
        last_order = current_order

        num = int(num_str)
        total_seconds += num * unit_to_seconds[unit]

    return total_seconds


def format_duration(seconds: int) -> str:
    """Convert seconds to a canonical human-readable duration string.

    Returns the SHORTEST string with largest units first, omitting zero components.
    Special case: format_duration(0) returns "0s".

    Raises ValueError for negative input.
    Satisfies: parse_duration(format_duration(n)) == n for all n >= 0.
    """
    if seconds < 0:
        raise ValueError("Negative duration is not allowed")

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

    for unit_char, unit_seconds in units:
        if remaining >= unit_seconds:
            value = remaining // unit_seconds
            remaining = remaining % unit_seconds
            result.append(f"{value}{unit_char}")

    return ''.join(result)
