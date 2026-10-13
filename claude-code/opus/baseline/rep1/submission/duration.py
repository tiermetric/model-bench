"""Parse and format human-readable duration strings.

A duration string is a concatenation of ``<non-negative-integer><unit>``
segments with no spaces, e.g. ``"1d2h3m4s"``. Units must appear in strictly
descending order of size and each may appear at most once.
"""

import re

__all__ = ["parse_duration", "format_duration"]

# Units in descending order of size.
_UNITS = (("d", 86400), ("h", 3600), ("m", 60), ("s", 1))

_UNIT_SECONDS = dict(_UNITS)
_UNIT_RANK = {unit: rank for rank, (unit, _) in enumerate(_UNITS)}

_SEGMENT_RE = re.compile(r"([0-9]+)([a-zA-Z]?)")


def parse_duration(text: str) -> int:
    """Return the total whole seconds described by ``text``.

    Raises ValueError if ``text`` is not a valid duration string.
    """
    if not isinstance(text, str):
        raise TypeError(f"expected str, got {type(text).__name__}")

    stripped = text.strip()
    if not stripped:
        raise ValueError("duration is empty")

    total = 0
    pos = 0
    last_rank = -1

    while pos < len(stripped):
        match = _SEGMENT_RE.match(stripped, pos)
        if match is None:
            raise ValueError(
                f"expected a number at position {pos} in {text!r}"
            )

        number, unit = match.group(1), match.group(2)
        if not unit:
            raise ValueError(f"number {number!r} has no unit in {text!r}")
        if unit not in _UNIT_SECONDS:
            raise ValueError(f"unknown unit {unit!r} in {text!r}")

        rank = _UNIT_RANK[unit]
        if rank == last_rank:
            raise ValueError(f"duplicate unit {unit!r} in {text!r}")
        if rank < last_rank:
            raise ValueError(f"unit {unit!r} is out of order in {text!r}")
        last_rank = rank

        total += int(number) * _UNIT_SECONDS[unit]
        pos = match.end()

    return total


def format_duration(seconds: int) -> str:
    """Return the canonical shortest duration string for ``seconds``.

    Raises ValueError if ``seconds`` is negative.
    """
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        raise TypeError(f"expected int, got {type(seconds).__name__}")
    if seconds < 0:
        raise ValueError(f"seconds must be non-negative, got {seconds}")
    if seconds == 0:
        return "0s"

    parts = []
    remainder = seconds
    for unit, size in _UNITS:
        value, remainder = divmod(remainder, size)
        if value:
            parts.append(f"{value}{unit}")
    return "".join(parts)
