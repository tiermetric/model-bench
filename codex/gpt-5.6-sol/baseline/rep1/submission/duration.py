"""Parse and format compact, human-readable durations."""

__all__ = ["parse_duration", "format_duration"]

_UNIT_SECONDS = {"d": 86_400, "h": 3_600, "m": 60, "s": 1}
_UNIT_ORDER = {"d": 0, "h": 1, "m": 2, "s": 3}


def parse_duration(text: str) -> int:
    """Return the number of whole seconds represented by *text*.

    The accepted syntax is one or more integer/unit segments, with units in
    descending order: days, hours, minutes, then seconds.
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
            raise ValueError("each duration component must be an integer and a unit")

        unit = value[position]
        position += 1
        if unit not in _UNIT_SECONDS:
            raise ValueError(f"unknown duration unit: {unit!r}")

        order = _UNIT_ORDER[unit]
        if order <= previous_order:
            raise ValueError("duration units must be unique and in descending order")
        previous_order = order

        amount = int(value[number_start : position - 1])
        total += amount * _UNIT_SECONDS[unit]

    return total


def format_duration(seconds: int) -> str:
    """Return the canonical compact representation of non-negative *seconds*."""
    if not isinstance(seconds, int):
        raise TypeError("seconds must be an int")
    if seconds < 0:
        raise ValueError("seconds must not be negative")
    if seconds == 0:
        return "0s"

    components = []
    remainder = seconds
    for unit, unit_seconds in _UNIT_SECONDS.items():
        amount, remainder = divmod(remainder, unit_seconds)
        if amount:
            components.append(f"{amount}{unit}")
    return "".join(components)
