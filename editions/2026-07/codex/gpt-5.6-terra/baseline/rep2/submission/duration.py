"""Utilities for parsing and formatting durations expressed in whole seconds."""

import re as _re


__all__ = ["parse_duration", "format_duration"]


_SEGMENT_RE = _re.compile(r"([0-9]+)([dhms])")
_VALID_DURATION_RE = _re.compile(r"(?:[0-9]+[dhms])+")
_UNIT_SECONDS = {"d": 86_400, "h": 3_600, "m": 60, "s": 1}
_UNIT_ORDER = {"d": 0, "h": 1, "m": 2, "s": 3}


def parse_duration(text: str) -> int:
    """Convert a valid human-readable duration string to whole seconds.

    The input may have surrounding whitespace, but its duration segments must
    be adjacent and use distinct units in descending order.
    """
    value = text.strip()
    if not value or not _VALID_DURATION_RE.fullmatch(value):
        raise ValueError("invalid duration")

    total = 0
    previous_order = -1
    for match in _SEGMENT_RE.finditer(value):
        number, unit = match.groups()
        order = _UNIT_ORDER[unit]
        if order <= previous_order:
            raise ValueError("duration units must be in descending order")
        total += int(number) * _UNIT_SECONDS[unit]
        previous_order = order
    return total


def format_duration(seconds: int) -> str:
    """Return the shortest canonical duration string for non-negative seconds."""
    if seconds < 0:
        raise ValueError("seconds cannot be negative")
    if seconds == 0:
        return "0s"

    remaining = seconds
    parts = []
    for unit, unit_seconds in _UNIT_SECONDS.items():
        amount, remaining = divmod(remaining, unit_seconds)
        if amount:
            parts.append(f"{amount}{unit}")
    return "".join(parts)
