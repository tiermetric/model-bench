import re

_UNITS = (("d", 86400), ("h", 3600), ("m", 60), ("s", 1))
_UNIT_SECONDS = dict(_UNITS)
_UNIT_ORDER = {unit: i for i, (unit, _) in enumerate(_UNITS)}
_SEGMENT_RE = re.compile(r"(\d+)([a-zA-Z])")


def parse_duration(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        raise ValueError("duration string is empty")

    pos = 0
    last_unit_index = -1
    total = 0
    matched_any = False

    while pos < len(stripped):
        match = _SEGMENT_RE.match(stripped, pos)
        if not match:
            raise ValueError(f"invalid duration segment at position {pos!r}: {stripped[pos:]!r}")

        number_str, unit = match.groups()
        if unit not in _UNIT_SECONDS:
            raise ValueError(f"unknown unit: {unit!r}")

        unit_index = _UNIT_ORDER[unit]
        if unit_index <= last_unit_index:
            raise ValueError(f"unit {unit!r} is out of order or duplicated")
        last_unit_index = unit_index

        total += int(number_str) * _UNIT_SECONDS[unit]
        matched_any = True
        pos = match.end()

    if not matched_any:
        raise ValueError(f"invalid duration string: {text!r}")

    return total


def format_duration(seconds: int) -> str:
    if seconds < 0:
        raise ValueError("seconds must be non-negative")

    if seconds == 0:
        return "0s"

    remaining = seconds
    parts = []
    for unit, unit_seconds in _UNITS:
        value, remaining = divmod(remaining, unit_seconds)
        if value:
            parts.append(f"{value}{unit}")

    return "".join(parts)
