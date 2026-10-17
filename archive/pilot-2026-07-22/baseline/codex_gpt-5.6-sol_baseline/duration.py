"""Parse and format compact, human-readable durations."""

import re


__all__ = ["parse_duration", "format_duration"]

_UNIT_SECONDS = {"d": 86_400, "h": 3_600, "m": 60, "s": 1}
_UNIT_ORDER = {unit: position for position, unit in enumerate("dhms")}
_DURATION_PATTERN = re.compile(r"(?:[0-9]+[dhms])+")
_SEGMENT_PATTERN = re.compile(r"([0-9]+)([dhms])")


def parse_duration(text: str) -> int:
    """Return the number of whole seconds represented by *text*.

    The accepted syntax is one or more integer/unit segments in descending
    unit order, with surrounding whitespace permitted.
    """
    if not isinstance(text, str):
        raise ValueError("duration must be a string")

    duration = text.strip()
    if not duration or _DURATION_PATTERN.fullmatch(duration) is None:
        raise ValueError("invalid duration")

    total = 0
    previous_position = -1
    for match in _SEGMENT_PATTERN.finditer(duration):
        number, unit = match.groups()
        position = _UNIT_ORDER[unit]
        if position <= previous_position:
            raise ValueError("units must be unique and in descending order")
        previous_position = position
        total += int(number) * _UNIT_SECONDS[unit]

    return total


def format_duration(seconds: int) -> str:
    """Return the canonical compact representation of *seconds*."""
    if not isinstance(seconds, int):
        raise TypeError("seconds must be an int")
    if seconds < 0:
        raise ValueError("seconds cannot be negative")
    if seconds == 0:
        return "0s"

    parts = []
    remainder = seconds
    for unit, unit_seconds in _UNIT_SECONDS.items():
        value, remainder = divmod(remainder, unit_seconds)
        if value:
            parts.append(f"{value}{unit}")
    return "".join(parts)
