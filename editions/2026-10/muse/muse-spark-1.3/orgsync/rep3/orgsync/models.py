"""Record shapes orgsync moves around.

These are containers, not validators: constructing one asserts nothing about
its contents, and nothing here decides whether a record received from a service
is usable. Deciding that — and what to do about a record that is not — is the
job of the sync logic, which is the only place that knows the rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class Account:
    """An account as the Ledger describes it."""

    id: str
    owner: str
    active: bool

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "owner": self.owner, "active": self.active}


@dataclass(frozen=True)
class Item:
    """A work item as the Directory describes it."""

    id: str
    owner: str
    status: str
    points: int

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "owner": self.owner,
                "status": self.status, "points": self.points}


@dataclass(frozen=True)
class Summary:
    """A per-owner summary, in the field order the Ledger's API uses."""

    owner: str
    open_items: int = 0
    done_items: int = 0
    total_points: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"owner": self.owner, "open_items": self.open_items,
                "done_items": self.done_items, "total_points": self.total_points}
