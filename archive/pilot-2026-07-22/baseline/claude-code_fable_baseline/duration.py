"""Parse and format human-readable duration strings.

Public API:
    parse_duration(text) -> int
    format_duration(seconds) -> str
"""

import re

# Units in strictly descending order of size.
_UNITS = (("d", 86400), ("h", 3600), ("m", 60), ("s", 1))
_UNIT_SECONDS = dict(_UNITS)
_UNIT_ORDER = {unit: i for i, (unit, _) in enumerate(_UNITS)}

_SEGMENT_RE = re.compile(r"([0-9]+)([dhms])")
_FULL_RE = re.compile(r"(?:[0-9]+[dhms])+\Z")


def parse_duration(text: str) -> int:
    """Parse a duration string like "1h30m" into total whole seconds.

    Units: d=86400, h=3600, m=60, s=1 (lowercase only). Segments must be
    concatenated without spaces, in strictly descending unit order, each
    unit at most once. Component values are unbounded non-negative integers.
    Leading/trailing whitespace is ignored. Raises ValueError on any
    malformed input.
    """
    if not isinstance(text, str):
        raise ValueError("duration must be a string")
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty duration string")
    if not _FULL_RE.fullmatch(stripped):
        raise ValueError("invalid duration string: %r" % (text,))

    total = 0
    last_order = -1
    for number, unit in _SEGMENT_RE.findall(stripped):
        order = _UNIT_ORDER[unit]
        if order <= last_order:
            raise ValueError(
                "units must be in strictly descending order without "
                "duplicates: %r" % (text,)
            )
        last_order = order
        total += int(number) * _UNIT_SECONDS[unit]
    return total


def format_duration(seconds: int) -> str:
    """Format whole seconds as the canonical shortest duration string.

    Largest units first, zero components omitted; 0 formats as "0s".
    Raises ValueError for negative input.
    """
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        raise ValueError("seconds must be an int")
    if seconds < 0:
        raise ValueError("seconds must be non-negative")
    if seconds == 0:
        return "0s"

    parts = []
    remaining = seconds
    for unit, size in _UNITS:
        value, remaining = divmod(remaining, size)
        if value:
            parts.append("%d%s" % (value, unit))
    return "".join(parts)
