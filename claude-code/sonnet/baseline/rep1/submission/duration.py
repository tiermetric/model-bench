import re

_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_UNIT_ORDER = {"d": 0, "h": 1, "m": 2, "s": 3}
_SEGMENT_RE = re.compile(r"(\d+[a-zA-Z]+)+")
_TOKEN_RE = re.compile(r"(\d+)([a-zA-Z]+)")


def parse_duration(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        raise ValueError("duration string is empty")
    if any(ch.isspace() for ch in stripped):
        raise ValueError("duration string contains internal whitespace")
    if _SEGMENT_RE.fullmatch(stripped) is None:
        raise ValueError(f"invalid duration string: {text!r}")

    total = 0
    last_rank = -1
    seen = set()
    for match in _TOKEN_RE.finditer(stripped):
        number_str, unit = match.group(1), match.group(2)
        if unit not in _UNIT_SECONDS:
            raise ValueError(f"unknown unit: {unit!r}")
        if unit in seen:
            raise ValueError(f"duplicate unit: {unit!r}")
        rank = _UNIT_ORDER[unit]
        if rank <= last_rank:
            raise ValueError(f"units out of order: {unit!r}")
        seen.add(unit)
        last_rank = rank
        total += int(number_str) * _UNIT_SECONDS[unit]
    return total


def format_duration(seconds: int) -> str:
    if seconds < 0:
        raise ValueError("seconds must be non-negative")
    if seconds == 0:
        return "0s"

    parts = []
    remaining = seconds
    for unit in ("d", "h", "m", "s"):
        unit_seconds = _UNIT_SECONDS[unit]
        value, remaining = divmod(remaining, unit_seconds)
        if value:
            parts.append(f"{value}{unit}")
    return "".join(parts)
