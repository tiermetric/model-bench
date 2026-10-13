"""Parse and format human-readable duration strings.

A duration string is a concatenation of ``<non-negative-integer><unit>``
segments with no spaces, e.g. ``"1d2h3m4s"``. Units must appear in strictly
descending order of size and each may appear at most once.
"""

import re

__all__ = ["parse_duration", "format_duration"]

# Units, largest first. The order of this tuple defines both the ordering
# constraint enforced by parse_duration and the output order of format_duration.
_UNITS = (
    ("d", 86400),
    ("h", 3600),
    ("m", 60),
    ("s", 1),
)

_UNIT_SECONDS = dict(_UNITS)
_UNIT_RANK = {unit: rank for rank, (unit, _) in enumerate(_UNITS)}

# One segment: digits followed by a single lowercase unit letter.
_SEGMENT_RE = re.compile(r"([0-9]+)([dhms])")
# The whole string must be one or more segments and nothing else.
_FULL_RE = re.compile(r"(?:[0-9]+[dhms])+\Z")


def parse_duration(text: str) -> int:
    """Return the total number of whole seconds in ``text``.

    Leading and trailing whitespace is ignored; whitespace anywhere inside the
    string is invalid. Component magnitudes are unbounded, so ``"1h90m"`` is
    accepted and equals 9000.

    Raises:
        ValueError: if ``text`` is not a valid duration string.
    """
    if not isinstance(text, str):
        raise ValueError(f"duration must be a string, got {type(text).__name__}")

    stripped = text.strip()
    if not stripped:
        raise ValueError("duration is empty")

    if not _FULL_RE.match(stripped):
        raise ValueError(f"invalid duration: {text!r}")

    total = 0
    last_rank = -1
    for number, unit in _SEGMENT_RE.findall(stripped):
        rank = _UNIT_RANK[unit]
        if rank <= last_rank:
            raise ValueError(
                f"invalid duration: {text!r} (unit {unit!r} out of order or duplicated)"
            )
        last_rank = rank
        total += int(number) * _UNIT_SECONDS[unit]

    return total


def format_duration(seconds: int) -> str:
    """Return the canonical shortest duration string for ``seconds``.

    Components with a zero value are omitted, except for ``format_duration(0)``
    which returns ``"0s"``.

    Raises:
        ValueError: if ``seconds`` is negative.
    """
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        raise ValueError(f"seconds must be an int, got {type(seconds).__name__}")
    if seconds < 0:
        raise ValueError(f"seconds must be non-negative, got {seconds}")
    if seconds == 0:
        return "0s"

    parts = []
    remaining = seconds
    for unit, size in _UNITS:
        value, remaining = divmod(remaining, size)
        if value:
            parts.append(f"{value}{unit}")

    return "".join(parts)
