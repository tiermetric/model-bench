"""Utilities for parsing and formatting human-readable durations."""

import re


_SEGMENT = re.compile(r"([0-9]+)([dhms])")
_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_UNIT_ORDER = {"d": 0, "h": 1, "m": 2, "s": 3}


def parse_duration(text: str) -> int:
    """Parse a concatenated duration expression into whole seconds."""
    if not isinstance(text, str):
        raise ValueError("duration must be a string")

    value = text.strip()
    if not value:
        raise ValueError("duration cannot be empty")

    total = 0
    position = 0
    previous_order = -1
    seen = set()
    while position < len(value):
        match = _SEGMENT.match(value, position)
        if match is None:
            raise ValueError("invalid duration")

        number, unit = match.groups()
        order = _UNIT_ORDER[unit]
        if unit in seen or order <= previous_order:
            raise ValueError("duration units must be unique and descending")
        seen.add(unit)
        previous_order = order
        total += int(number) * _UNIT_SECONDS[unit]
        position = match.end()

    return total


def format_duration(seconds: int) -> str:
    """Format a non-negative number of seconds canonically."""
    if not isinstance(seconds, int) or seconds < 0:
        raise ValueError("seconds must be a non-negative integer")
    if seconds == 0:
        return "0s"

    remaining = seconds
    components = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        count, remaining = divmod(remaining, size)
        if count:
            components.append(f"{count}{unit}")
    return "".join(components)


__all__ = ["parse_duration", "format_duration"]
