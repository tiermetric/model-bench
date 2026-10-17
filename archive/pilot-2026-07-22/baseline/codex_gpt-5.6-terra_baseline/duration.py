"""Parsing and formatting for compact human-readable durations."""

__all__ = ["parse_duration", "format_duration"]


def parse_duration(text: str) -> int:
    """Return the number of seconds represented by *text*.

    Accepted components are ordered, lowercase ``d``, ``h``, ``m``, and ``s``
    segments with non-negative decimal integer values.
    """
    text = text.strip()
    if not text:
        raise ValueError("duration must not be empty")

    unit_seconds = {"d": 86_400, "h": 3_600, "m": 60, "s": 1}
    unit_order = {"d": 0, "h": 1, "m": 2, "s": 3}
    total = 0
    previous_order = -1
    position = 0

    while position < len(text):
        if not ("0" <= text[position] <= "9"):
            raise ValueError("each duration unit must have a non-negative integer")

        value = 0
        while position < len(text) and "0" <= text[position] <= "9":
            value = value * 10 + ord(text[position]) - ord("0")
            position += 1

        if position == len(text) or text[position] not in unit_seconds:
            raise ValueError("unknown or missing duration unit")

        unit = text[position]
        order = unit_order[unit]
        if order <= previous_order:
            raise ValueError("duration units must be unique and descending")

        total += value * unit_seconds[unit]
        previous_order = order
        position += 1

    return total


def format_duration(seconds: int) -> str:
    """Return the canonical compact representation of non-negative *seconds*."""
    if seconds < 0:
        raise ValueError("seconds must not be negative")
    if seconds == 0:
        return "0s"

    parts = []
    for size, unit in ((86_400, "d"), (3_600, "h"), (60, "m"), (1, "s")):
        amount, seconds = divmod(seconds, size)
        if amount:
            parts.append(f"{amount}{unit}")
    return "".join(parts)
