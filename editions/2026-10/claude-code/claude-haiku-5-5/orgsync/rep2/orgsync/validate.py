"""Well-formedness rules for records received from the two services.

A record is well-formed when it has every required field with exactly the JSON
type the service documents, and its values are in range. Extra fields never
make a record malformed. Nothing else in the package decides this, so the rules
live here where they can be read in one place.
"""

from __future__ import annotations

from typing import Any

OPEN = "open"
DONE = "done"
STATUSES = (OPEN, DONE)


def _is_text(value: Any) -> bool:
    return isinstance(value, str)


def _is_count(value: Any) -> bool:
    # `type(...) is int` rather than isinstance: a JSON boolean is a Python bool,
    # which is a subclass of int, and must not pass as a number.
    return type(value) is int and value >= 0


def is_well_formed_item(record: Any) -> bool:
    """A Directory work item: `{"id": str, "owner": str, "status": "open"|"done", "points": int >= 0}`."""
    return (isinstance(record, dict)
            and _is_text(record.get("id"))
            and _is_text(record.get("owner"))
            and record.get("status") in STATUSES
            and _is_count(record.get("points")))


def is_well_formed_account(record: Any) -> bool:
    """A Ledger account: `{"id": str, "owner": str, "active": bool}`."""
    return (isinstance(record, dict)
            and _is_text(record.get("id"))
            and _is_text(record.get("owner"))
            and isinstance(record.get("active"), bool))


def is_well_formed_summary(record: Any) -> bool:
    """A Ledger summary: an owner plus three non-negative integer counters."""
    return (isinstance(record, dict)
            and _is_text(record.get("owner"))
            and _is_count(record.get("open_items"))
            and _is_count(record.get("done_items"))
            and _is_count(record.get("total_points")))
