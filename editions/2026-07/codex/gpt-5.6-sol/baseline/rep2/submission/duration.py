"""Parse and format durations expressed with day, hour, minute, and second units."""

_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_UNIT_ORDER = {"d": 0, "h": 1, "m": 2, "s": 3}


def parse_duration(text: str) -> int:
    """Return the number of whole seconds represented by *text*.

    The accepted syntax is one or more unsigned integer/unit pairs, with units
    occurring at most once and in descending order: d, h, m, s.
    """
    if not isinstance(text, str):
        raise ValueError("duration must be a string")

    text = text.strip()
    if not text:
        raise ValueError("duration must not be empty")

    total = 0
    position = 0
    previous_order = -1

    while position < len(text):
        number_start = position
        while position < len(text) and "0" <= text[position] <= "9":
            position += 1

        if number_start == position:
            raise ValueError("each unit must be preceded by an integer")
        if position == len(text):
            raise ValueError("each integer must be followed by a unit")

        unit = text[position]
        if unit not in _UNIT_SECONDS:
            raise ValueError("unknown duration unit")

        order = _UNIT_ORDER[unit]
        if order <= previous_order:
            raise ValueError("units must be unique and in descending order")

        total += int(text[number_start:position]) * _UNIT_SECONDS[unit]
        previous_order = order
        position += 1

    return total


def format_duration(seconds: int) -> str:
    """Return the canonical shortest duration string for *seconds*."""
    if not isinstance(seconds, int):
        raise ValueError("seconds must be an integer")
    if seconds < 0:
        raise ValueError("seconds must not be negative")
    if seconds == 0:
        return "0s"

    parts = []
    remainder = seconds
    for unit, unit_seconds in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        value, remainder = divmod(remainder, unit_seconds)
        if value:
            parts.append(f"{value}{unit}")
    return "".join(parts)
