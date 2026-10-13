"""Utilities for parsing and formatting whole-second durations."""

__all__ = ("parse_duration", "format_duration")

_UNIT_SECONDS = {"d": 86_400, "h": 3_600, "m": 60, "s": 1}
_UNIT_ORDER = {"d": 0, "h": 1, "m": 2, "s": 3}


def parse_duration(text: str) -> int:
    """Convert a compact, descending-unit duration string to seconds.

    The outer whitespace of *text* is ignored; whitespace within it is not.
    """
    value = text.strip()
    if not value:
        raise ValueError("duration must not be empty")

    total = 0
    position = 0
    previous_order = -1

    while position < len(value):
        number_start = position
        while position < len(value) and "0" <= value[position] <= "9":
            position += 1

        if number_start == position:
            raise ValueError("each unit must have a non-negative integer")
        if position == len(value):
            raise ValueError("number is missing a unit")

        unit = value[position]
        if unit not in _UNIT_SECONDS:
            raise ValueError("unknown duration unit")
        order = _UNIT_ORDER[unit]
        if order <= previous_order:
            raise ValueError("units must be unique and in descending order")

        total += int(value[number_start:position]) * _UNIT_SECONDS[unit]
        previous_order = order
        position += 1

    return total


def format_duration(seconds: int) -> str:
    """Return the shortest canonical duration representation of *seconds*."""
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 0:
        raise ValueError("seconds must be a non-negative integer")
    if seconds == 0:
        return "0s"

    parts = []
    remaining = seconds
    for unit, unit_seconds in _UNIT_SECONDS.items():
        amount, remaining = divmod(remaining, unit_seconds)
        if amount:
            parts.append(f"{amount}{unit}")
    return "".join(parts)
