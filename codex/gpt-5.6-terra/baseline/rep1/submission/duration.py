"""Parsing and formatting utilities for compact duration strings."""

__all__ = ["parse_duration", "format_duration"]

_UNITS = (("d", 86_400), ("h", 3_600), ("m", 60), ("s", 1))
_UNIT_SECONDS = dict(_UNITS)
_UNIT_ORDER = {unit: index for index, (unit, _) in enumerate(_UNITS)}


def parse_duration(text: str) -> int:
    """Return the whole seconds represented by a compact duration string.

    The accepted grammar is one or more number-and-unit segments in descending
    unit order, with optional whitespace only around the complete string.
    """
    if not isinstance(text, str):
        raise ValueError("duration must be a string")

    text = text.strip()
    if not text:
        raise ValueError("duration cannot be empty")

    total = 0
    previous_order = -1
    position = 0
    length = len(text)

    while position < length:
        start = position
        while position < length and text[position].isdigit():
            position += 1
        if start == position:
            raise ValueError("each unit must have a non-negative integer")
        if position == length:
            raise ValueError("number is missing a unit")

        unit = text[position]
        position += 1
        if unit not in _UNIT_SECONDS:
            raise ValueError("unknown duration unit")

        order = _UNIT_ORDER[unit]
        if order <= previous_order:
            raise ValueError("duration units must be unique and descending")

        total += int(text[start:position - 1]) * _UNIT_SECONDS[unit]
        previous_order = order

    return total


def format_duration(seconds: int) -> str:
    """Return the canonical compact duration representation of whole seconds."""
    if type(seconds) is not int or seconds < 0:
        raise ValueError("seconds must be a non-negative integer")
    if seconds == 0:
        return "0s"

    components = []
    remainder = seconds
    for unit, unit_seconds in _UNITS:
        value, remainder = divmod(remainder, unit_seconds)
        if value:
            components.append(f"{value}{unit}")
    return "".join(components)
