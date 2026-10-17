"""Parse and format human-readable durations."""


_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_UNIT_ORDER = {"d": 0, "h": 1, "m": 2, "s": 3}


def parse_duration(text: str) -> int:
    """Return the number of seconds represented by *text*."""
    if not isinstance(text, str):
        raise ValueError("duration must be a string")

    text = text.strip()
    if not text:
        raise ValueError("duration cannot be empty")

    total = 0
    position = 0
    previous_order = -1
    while position < len(text):
        start = position
        while position < len(text) and "0" <= text[position] <= "9":
            position += 1
        if start == position or position == len(text):
            raise ValueError("each segment must contain a number and a unit")

        number = 0
        for digit in text[start:position]:
            number = number * 10 + (ord(digit) - ord("0"))

        unit = text[position]
        if unit not in _UNIT_SECONDS:
            raise ValueError("unknown duration unit")
        unit_order = _UNIT_ORDER[unit]
        if unit_order <= previous_order:
            raise ValueError("duration units must be unique and descending")

        total += number * _UNIT_SECONDS[unit]
        previous_order = unit_order
        position += 1

    return total


def format_duration(seconds: int) -> str:
    """Return the canonical shortest representation of *seconds*."""
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 0:
        raise ValueError("seconds must be a non-negative integer")
    if seconds == 0:
        return "0s"

    parts = []
    remaining = seconds
    for unit, unit_seconds in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        amount, remaining = divmod(remaining, unit_seconds)
        if amount:
            parts.append(f"{amount}{unit}")
    return "".join(parts)
