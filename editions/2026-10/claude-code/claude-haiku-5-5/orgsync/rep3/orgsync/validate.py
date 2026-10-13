"""Well-formedness rules for records received from the two services.

A record that fails these is skipped by sync and never counted. The checks are
on JSON types, not Python truthiness: `True` is not an integer here, and `3.0`
is not one either.
"""

from __future__ import annotations

from typing import Any, Optional

from .models import Account, Item

OPEN_STATUS = "open"
DONE_STATUS = "done"
STATUSES = (OPEN_STATUS, DONE_STATUS)


def _is_int(value: Any) -> bool:
    return type(value) is int


def parse_account(raw: Any) -> Optional[Account]:
    """The account described by `raw`, or None if it is not well-formed."""
    if not isinstance(raw, dict):
        return None
    if not isinstance(raw.get("id"), str) or not isinstance(raw.get("owner"), str):
        return None
    if not isinstance(raw.get("active"), bool):
        return None
    return Account(id=raw["id"], owner=raw["owner"], active=raw["active"])


def parse_item(raw: Any) -> Optional[Item]:
    """The item described by `raw`, or None if it is not well-formed."""
    if not isinstance(raw, dict):
        return None
    if not isinstance(raw.get("id"), str) or not isinstance(raw.get("owner"), str):
        return None
    if raw.get("status") not in STATUSES:
        return None
    points = raw.get("points")
    if not _is_int(points) or points < 0:
        return None
    return Item(id=raw["id"], owner=raw["owner"], status=raw["status"], points=points)
