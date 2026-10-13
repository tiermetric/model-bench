"""Parse and format human-readable duration strings.

A duration string is one or more ``<non-negative-integer><unit>`` segments
concatenated without spaces, e.g. ``"1d2h3m4s"``. Units must appear in
strictly descending order of size and may each appear at most once.
"""

import re

__all__ = ["parse_duration", "format_duration"]

# Units, largest first. The order of this tuple defines both the required
# ordering for parsing and the emission order for formatting.
_UNITS = (
    ("d", 86400),
    ("h", 3600),
    ("m", 60),
    ("s", 1),
)

_UNIT_SECONDS = dict(_UNITS)
_UNIT_RANK = {unit: rank for rank, (unit, _) in enumerate(_UNITS)}

# One segment: digits followed by a single unit letter. Anchored scanning below
# guarantees the whole string is consumed, so anything else is rejected.
_SEGMENT_RE = re.compile(r"([0-9]+)([dhms])")


def parse_duration(text: str) -> int:
    """Return the total whole seconds described by ``text``.

    Raises ValueError if ``text`` is not a valid duration string.
    """
    if not isinstance(text, str):
        raise ValueError(f"duration must be a string, got {type(text).__name__}")

    stripped = text.strip()
    if not stripped:
        raise ValueError("duration must not be empty")

    total = 0
    pos = 0
    last_rank = -1
    while pos < len(stripped):
        match = _SEGMENT_RE.match(stripped, pos)
        if match is None:
            raise ValueError(
                f"invalid duration {text!r}: expected <number><unit> at "
                f"offset {pos}"
            )
        number, unit = match.group(1), match.group(2)
        rank = _UNIT_RANK[unit]
        if rank <= last_rank:
            raise ValueError(
                f"invalid duration {text!r}: unit {unit!r} is duplicated or "
                f"out of descending order"
            )
        last_rank = rank
        total += int(number) * _UNIT_SECONDS[unit]
        pos = match.end()

    return total


def format_duration(seconds: int) -> str:
    """Return the canonical shortest duration string for ``seconds``.

    Raises ValueError if ``seconds`` is negative or not an integer.
    """
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        raise ValueError(
            f"seconds must be an int, got {type(seconds).__name__}"
        )
    if seconds < 0:
        raise ValueError(f"seconds must not be negative, got {seconds}")
    if seconds == 0:
        return "0s"

    parts = []
    remaining = seconds
    for unit, size in _UNITS:
        value, remaining = divmod(remaining, size)
        if value:
            parts.append(f"{value}{unit}")
    return "".join(parts)
