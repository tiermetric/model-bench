"""Parse and format human-readable duration strings like "1h30m"."""

import re

_UNIT_SECONDS = (("d", 86400), ("h", 3600), ("m", 60), ("s", 1))

# Units optional but must appear in d, h, m, s order, each at most once,
# each as <digits><unit> with no separators. ASCII digits only.
_DURATION_RE = re.compile(
    r"(?:([0-9]+)d)?(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+)s)?", re.ASCII
)


def parse_duration(text: str) -> int:
    """Parse a duration string such as "1h30m" into total seconds.

    Raises ValueError if the string is empty, contains inner whitespace,
    uses unknown or uppercase units, has units out of descending order or
    duplicated, or has a number without a unit / unit without a number.
    """
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty duration string")
    match = _DURATION_RE.fullmatch(stripped)
    if match is None or not any(match.groups()):
        raise ValueError(f"invalid duration string: {text!r}")
    total = 0
    for value, (_, seconds) in zip(match.groups(), _UNIT_SECONDS):
        if value is not None:
            total += int(value) * seconds
    return total


def format_duration(seconds: int) -> str:
    """Format a number of seconds as the shortest duration string.

    Zero-valued components are omitted; format_duration(0) == "0s".
    Raises ValueError for negative input.
    """
    if seconds < 0:
        raise ValueError("seconds must be non-negative")
    if seconds == 0:
        return "0s"
    parts = []
    remaining = seconds
    for unit, unit_seconds in _UNIT_SECONDS:
        value, remaining = divmod(remaining, unit_seconds)
        if value:
            parts.append(f"{value}{unit}")
    return "".join(parts)
