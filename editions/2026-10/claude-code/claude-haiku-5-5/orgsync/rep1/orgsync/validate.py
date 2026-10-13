"""Well-formedness rules for the records the two services send.

A record is well-formed only when every field the rules name is present with
exactly the JSON type (and range) they require; extra fields are ignored. The
parsers here return the model for a well-formed record and `None` for anything
else. What a malformed record means for the sync is decided in `sync`.
"""

from __future__ import annotations

from typing import Any, Optional

from .models import Account, Item

#: The only statuses a work item may carry.
ITEM_STATUSES = ("open", "done")


def is_json_int(value: Any) -> bool:
    """True for a JSON integer. Python's bool is an int subclass; JSON's is not."""
    return isinstance(value, int) and not isinstance(value, bool)


def parse_item(record: Any) -> Optional[Item]:
    """The `Item` for a well-formed Directory item, else None."""
    if not isinstance(record, dict):
        return None
    item_id, owner = record.get("id"), record.get("owner")
    status, points = record.get("status"), record.get("points")
    if not isinstance(item_id, str) or not isinstance(owner, str):
        return None
    if not isinstance(status, str) or status not in ITEM_STATUSES:
        return None
    if not is_json_int(points) or points < 0:
        return None
    return Item(id=item_id, owner=owner, status=status, points=points)


def parse_account(record: Any) -> Optional[Account]:
    """The `Account` for a well-formed Ledger account, else None."""
    if not isinstance(record, dict):
        return None
    account_id, owner, active = record.get("id"), record.get("owner"), record.get("active")
    if not isinstance(account_id, str) or not isinstance(owner, str):
        return None
    if not isinstance(active, bool):
        return None
    return Account(id=account_id, owner=owner, active=active)
