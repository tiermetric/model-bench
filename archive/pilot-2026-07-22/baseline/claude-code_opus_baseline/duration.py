"""Human-readable duration parsing and formatting.

A duration string is one or more ``<non-negative-integer><unit>`` segments
concatenated with no spaces, where the unit is one of:

    d = 86400s, h = 3600s, m = 60s, s = 1s

Units must appear in strictly descending order of size and each at most once.
"""

# Unit letter -> (seconds, order rank). Lower rank == larger unit.
_UNITS = {
    "d": (86400, 0),
    "h": (3600, 1),
    "m": (60, 2),
    "s": (1, 3),
}

# Largest-first ordering used by format_duration.
_ORDERED = (("d", 86400), ("h", 3600), ("m", 60), ("s", 1))


def parse_duration(text: str) -> int:
    """Parse a human-readable duration string into total whole seconds.

    Raises ValueError on any malformed input (see module docstring for the
    grammar). Component magnitudes are unbounded; only unit order and
    uniqueness are enforced.
    """
    if not isinstance(text, str):
        raise ValueError("duration must be a string")

    stripped = text.strip()
    if not stripped:
        raise ValueError("empty duration")

    total = 0
    prev_rank = -1  # rank of the last unit seen; enforces strict ascent
    i = 0
    n = len(stripped)

    while i < n:
        # Read one or more digits.
        start = i
        while i < n and stripped[i].isdigit():
            i += 1
        if i == start:
            # No digits where a number was expected (e.g. bare unit, or
            # whitespace/garbage inside the string).
            raise ValueError(f"expected a number at position {start}: {text!r}")

        number = int(stripped[start:i])

        # Next character must be a unit letter.
        if i >= n:
            raise ValueError(f"number without a unit: {text!r}")
        unit = stripped[i]
        if unit not in _UNITS:
            raise ValueError(f"unknown unit {unit!r}: {text!r}")
        i += 1

        seconds, rank = _UNITS[unit]
        if rank <= prev_rank:
            raise ValueError(f"units out of order or duplicated: {text!r}")
        prev_rank = rank

        total += number * seconds

    return total


def format_duration(seconds: int) -> str:
    """Format an int number of seconds as the canonical shortest string.

    Largest units first, zero-valued components omitted. ``0`` yields ``"0s"``.
    Guarantees ``parse_duration(format_duration(n)) == n`` for every n >= 0.
    """
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        raise ValueError("seconds must be an int")
    if seconds < 0:
        raise ValueError("seconds must be non-negative")
    if seconds == 0:
        return "0s"

    parts = []
    remaining = seconds
    for letter, size in _ORDERED:
        value, remaining = divmod(remaining, size)
        if value:
            parts.append(f"{value}{letter}")

    return "".join(parts)
