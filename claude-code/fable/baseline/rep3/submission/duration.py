"""Parse and format human-readable duration strings like "1h30m"."""

import re

_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_UNIT_ORDER = "dhms"

_SEGMENT_RE = re.compile(r"([0-9]+)([dhms])")


def parse_duration(text: str) -> int:
    """Parse a duration string such as "1h30m" into total whole seconds.

    Raises ValueError for empty input, unknown or uppercase units,
    missing numbers or units, interior whitespace, units out of
    descending order, or duplicate units.
    """
    if not isinstance(text, str):
        raise ValueError(f"duration must be a string, got {type(text).__name__}")
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty duration string")

    total = 0
    last_unit_index = -1
    pos = 0
    while pos < len(stripped):
        match = _SEGMENT_RE.match(stripped, pos)
        if match is None:
            raise ValueError(f"invalid duration: {text!r}")
        value, unit = match.groups()
        unit_index = _UNIT_ORDER.index(unit)
        if unit_index <= last_unit_index:
            raise ValueError(
                f"invalid duration: {text!r} (units must be unique and in d, h, m, s order)"
            )
        last_unit_index = unit_index
        total += int(value) * _UNIT_SECONDS[unit]
        pos = match.end()
    return total


def format_duration(seconds: int) -> str:
    """Format whole seconds as the shortest duration string, e.g. 5400 -> "1h30m".

    Raises ValueError for negative input.
    """
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        raise ValueError(f"seconds must be an int, got {type(seconds).__name__}")
    if seconds < 0:
        raise ValueError(f"seconds must be non-negative, got {seconds}")
    if seconds == 0:
        return "0s"

    parts = []
    remaining = seconds
    for unit in _UNIT_ORDER:
        value, remaining = divmod(remaining, _UNIT_SECONDS[unit])
        if value:
            parts.append(f"{value}{unit}")
    return "".join(parts)
