"""Parse and format human-readable duration strings like "1h30m"."""

import re

_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_UNIT_ORDER = "dhms"
_SEGMENT_RE = re.compile(r"([0-9]+)([a-z])")


def parse_duration(text: str) -> int:
    """Parse a duration string such as "1h30m" into total whole seconds.

    Units are d, h, m, s (lowercase), must appear in strictly descending
    order, each at most once. Component values are unbounded non-negative
    integers. Raises ValueError on any malformed input.
    """
    if not isinstance(text, str):
        raise ValueError("duration must be a string")
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty duration string")

    total = 0
    last_index = -1
    pos = 0
    while pos < len(stripped):
        match = _SEGMENT_RE.match(stripped, pos)
        if not match:
            raise ValueError(f"invalid duration: {text!r}")
        value, unit = match.groups()
        if unit not in _UNIT_SECONDS:
            raise ValueError(f"unknown unit {unit!r} in duration: {text!r}")
        index = _UNIT_ORDER.index(unit)
        if index <= last_index:
            raise ValueError(f"units out of order or duplicated in: {text!r}")
        last_index = index
        total += int(value) * _UNIT_SECONDS[unit]
        pos = match.end()
    return total


def format_duration(seconds: int) -> str:
    """Format whole seconds as the canonical shortest duration string.

    Largest units first, zero-valued components omitted; 0 -> "0s".
    Raises ValueError for negative input.
    """
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        raise ValueError("seconds must be an int")
    if seconds < 0:
        raise ValueError("seconds must be non-negative")
    if seconds == 0:
        return "0s"

    parts = []
    remaining = seconds
    for unit in _UNIT_ORDER:
        value, remaining = divmod(remaining, _UNIT_SECONDS[unit])
        if value:
            parts.append(f"{value}{unit}")
    return "".join(parts)
