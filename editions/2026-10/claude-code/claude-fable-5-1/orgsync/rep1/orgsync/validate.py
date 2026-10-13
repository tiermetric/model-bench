"""Well-formedness rules for the records the two services deliver.

This is the only place that knows what a usable account or item looks like.
Each checker takes the record exactly as parsed from JSON and returns a model
when the record is well-formed, or None when it is not. Nothing is coerced:
`"3"` is not an int, `"yes"` is not a bool, and a JSON `true` is not a count.
Extra fields are ignored, so a service may grow its schema without breaking
the tool.
"""

from __future__ import annotations

from typing import Any, Optional

from .models import Account, Item

ITEM_STATUSES = ("open", "done")


def _is_int(value: Any) -> bool:
    """True for a JSON integer. `bool` is excluded: it subclasses int in
    Python, but `true` is not a number on the wire."""
    return isinstance(value, int) and not isinstance(value, bool)


def validate_account(record: Any) -> Optional[Account]:
    """Return an Account if `record` is a well-formed account, else None."""
    if not isinstance(record, dict):
        return None
    account_id, owner, active = record.get("id"), record.get("owner"), record.get("active")
    if not isinstance(account_id, str) or not isinstance(owner, str):
        return None
    if not isinstance(active, bool):
        return None
    return Account(id=account_id, owner=owner, active=active)


def validate_item(record: Any) -> Optional[Item]:
    """Return an Item if `record` is a well-formed work item, else None."""
    if not isinstance(record, dict):
        return None
    item_id, owner = record.get("id"), record.get("owner")
    status, points = record.get("status"), record.get("points")
    if not isinstance(item_id, str) or not isinstance(owner, str):
        return None
    if status not in ITEM_STATUSES:
        return None
    if not _is_int(points) or points < 0:
        return None
    return Item(id=item_id, owner=owner, status=status, points=points)
