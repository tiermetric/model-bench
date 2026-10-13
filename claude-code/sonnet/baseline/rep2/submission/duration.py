import re

_UNIT_SECONDS = {'d': 86400, 'h': 3600, 'm': 60, 's': 1}
_UNIT_ORDER = 'dhms'
_SEGMENT_RE = re.compile(r'(\d+)([a-z])')


def parse_duration(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        raise ValueError(f"invalid duration: {text!r}")
    if any(ch.isspace() for ch in stripped):
        raise ValueError(f"invalid duration: whitespace inside {text!r}")

    total = 0
    pos = 0
    last_index = -1
    for match in _SEGMENT_RE.finditer(stripped):
        if match.start() != pos:
            raise ValueError(f"invalid duration: {text!r}")
        num_str, unit = match.groups()
        if unit not in _UNIT_SECONDS:
            raise ValueError(f"unknown unit {unit!r} in {text!r}")
        index = _UNIT_ORDER.index(unit)
        if index <= last_index:
            raise ValueError(f"units out of order or duplicated in {text!r}")
        last_index = index
        total += int(num_str) * _UNIT_SECONDS[unit]
        pos = match.end()

    if pos != len(stripped):
        raise ValueError(f"invalid duration: {text!r}")

    return total


def format_duration(seconds: int) -> str:
    if seconds < 0:
        raise ValueError("seconds must be non-negative")
    if seconds == 0:
        return "0s"

    parts = []
    remainder = seconds
    for unit in _UNIT_ORDER:
        value = _UNIT_SECONDS[unit]
        quantity, remainder = divmod(remainder, value)
        if quantity:
            parts.append(f"{quantity}{unit}")

    return ''.join(parts)
