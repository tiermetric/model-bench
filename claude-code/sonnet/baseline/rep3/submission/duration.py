"""Parse and format human-readable durations like "1h30m" or "2d4h"."""

import re

_UNIT_SECONDS = {'d': 86400, 'h': 3600, 'm': 60, 's': 1}
_UNIT_ORDER = ['d', 'h', 'm', 's']
_COMPONENT_RE = re.compile(r'(\d+)([a-z])')


def parse_duration(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        raise ValueError("duration string is empty")
    if any(ch.isspace() for ch in stripped):
        raise ValueError("duration string contains internal whitespace")

    total = 0
    pos = 0
    last_unit_index = -1
    while pos < len(stripped):
        match = _COMPONENT_RE.match(stripped, pos)
        if not match:
            raise ValueError(f"invalid duration component at position {pos}: {stripped[pos:]!r}")
        number_str, unit = match.group(1), match.group(2)
        if unit not in _UNIT_SECONDS:
            raise ValueError(f"unknown unit {unit!r}")
        unit_index = _UNIT_ORDER.index(unit)
        if unit_index <= last_unit_index:
            raise ValueError(f"unit {unit!r} is out of order or duplicated")
        last_unit_index = unit_index
        total += int(number_str) * _UNIT_SECONDS[unit]
        pos = match.end()
    return total


def format_duration(seconds: int) -> str:
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
