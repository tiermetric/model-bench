"""Parse and format compact, human-readable durations."""

__all__ = ["parse_duration", "format_duration"]

_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_UNIT_ORDER = {"d": 0, "h": 1, "m": 2, "s": 3}


def parse_duration(text: str) -> int:
    """Return the number of whole seconds represented by *text*.

    The accepted form is one or more integer/unit segments, in descending
    unit order, with surrounding (but not internal) whitespace permitted.
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

        if number_start == position or position == len(value):
            raise ValueError("each duration component requires a number and unit")

        unit = value[position]
        if unit not in _UNIT_SECONDS:
            raise ValueError(f"invalid duration unit: {unit!r}")

        order = _UNIT_ORDER[unit]
        if order <= previous_order:
            raise ValueError("duration units must be unique and in descending order")

        total += int(value[number_start:position]) * _UNIT_SECONDS[unit]
        previous_order = order
        position += 1

    return total


def format_duration(seconds: int) -> str:
    """Return the canonical compact representation of non-negative *seconds*."""
    if seconds < 0:
        raise ValueError("seconds must be non-negative")
    if seconds == 0:
        return "0s"

    parts = []
    remainder = seconds
    for unit, unit_seconds in _UNIT_SECONDS.items():
        count, remainder = divmod(remainder, unit_seconds)
        if count:
            parts.append(f"{count}{unit}")
    return "".join(parts)
