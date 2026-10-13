"""Deciding whether a record received from a service is well-formed.

The services are allowed to send anything; these functions are the one place
that says what counts. Each returns the typed model for a usable record and
`None` for one that is not, so callers can route the raw record to
`errors.jsonl` without re-deriving the rules.

JSON types are checked exactly: `bool` is a subclass of `int` in Python, so an
`active: 1` or a `points: true` must be rejected explicitly.
"""

from __future__ import annotations

from typing import Any, Optional

from .models import Account, Item

ITEM_STATUSES = ("open", "done")


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def parse_account(raw: Any) -> Optional[Account]:
    """Return an `Account` if `raw` is a well-formed account, else None.

    Well-formed: a JSON object with `id` (string), `owner` (string) and
    `active` (boolean). Extra keys are ignored.
    """
    if not isinstance(raw, dict):
        return None
    if not (_is_str(raw.get("id")) and _is_str(raw.get("owner"))
            and _is_bool(raw.get("active"))):
        return None
    return Account(id=raw["id"], owner=raw["owner"], active=raw["active"])


def parse_item(raw: Any) -> Optional[Item]:
    """Return an `Item` if `raw` is a well-formed work item, else None.

    Well-formed: a JSON object with `id` (string), `owner` (string), `status`
    ("open" or "done") and `points` (integer >= 0). Extra keys are ignored.
    """
    if not isinstance(raw, dict):
        return None
    if not (_is_str(raw.get("id")) and _is_str(raw.get("owner"))):
        return None
    if raw.get("status") not in ITEM_STATUSES:
        return None
    points = raw.get("points")
    if not _is_int(points) or points < 0:
        return None
    return Item(id=raw["id"], owner=raw["owner"], status=raw["status"], points=points)


def summary_owner(raw: Any) -> Optional[str]:
    """The owner named by a summary record the Ledger already holds, or None.

    The Ledger is authoritative for what it stores, so the only thing asked of
    a stored summary is whose it is: a record that does not even name an owner
    cannot stand in for anyone's summary.
    """
    if isinstance(raw, dict) and _is_str(raw.get("owner")):
        return raw["owner"]
    return None
