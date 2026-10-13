"""Utilities for parsing and formatting durations."""

import re


_SEGMENTS = re.compile(r"(?:[0-9]+[dhms])+")
_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_UNIT_ORDER = {"d": 0, "h": 1, "m": 2, "s": 3}


def parse_duration(text: str) -> int:
    """Parse a concatenated duration string and return its total seconds."""
    stripped = text.strip()
    if not stripped or _SEGMENTS.fullmatch(stripped) is None:
        raise ValueError("invalid duration")

    total = 0
    previous_order = -1
    for number, unit in re.findall(r"([0-9]+)([dhms])", stripped):
        order = _UNIT_ORDER[unit]
        if order <= previous_order:
            raise ValueError("duration units must be in descending order")
        previous_order = order
        total += int(number) * _UNIT_SECONDS[unit]
    return total


def format_duration(seconds: int) -> str:
    """Return the canonical shortest representation of a duration."""
    if not isinstance(seconds, int) or seconds < 0:
        raise ValueError("seconds must be a non-negative integer")
    if seconds == 0:
        return "0s"

    parts = []
    remainder = seconds
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        value, remainder = divmod(remainder, size)
        if value:
            parts.append(f"{value}{unit}")
    return "".join(parts)


__all__ = ["parse_duration", "format_duration"]
