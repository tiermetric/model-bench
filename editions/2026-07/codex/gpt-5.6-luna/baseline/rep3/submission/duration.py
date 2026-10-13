"""Utilities for parsing and formatting durations."""

import re as _re


_UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_ORDER = {"d": 0, "h": 1, "m": 2, "s": 3}
__all__ = ["parse_duration", "format_duration"]


def parse_duration(text: str) -> int:
    """Parse a compact duration expression into whole seconds."""
    if not isinstance(text, str):
        raise ValueError("duration must be a string")

    expression = text.strip()
    if not expression or not _re.fullmatch(r"(?:[0-9]+[dhms])+", expression):
        raise ValueError("invalid duration")

    total = 0
    previous_order = -1
    for match in _re.finditer(r"([0-9]+)([dhms])", expression):
        number, unit = match.groups()
        order = _ORDER[unit]
        if order <= previous_order:
            raise ValueError("duration units must be unique and descending")
        total += int(number) * _UNITS[unit]
        previous_order = order
    return total


def format_duration(seconds: int) -> str:
    """Format a non-negative integer number of seconds canonically."""
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 0:
        raise ValueError("seconds must be a non-negative integer")
    if seconds == 0:
        return "0s"

    components = []
    remainder = seconds
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        value, remainder = divmod(remainder, size)
        if value:
            components.append(f"{value}{unit}")
    return "".join(components)
