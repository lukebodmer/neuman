"""Search over the audit chain.

The search index is **derived data**: rebuildable from the chain at any time and
never authoritative. That separation is what lets the index be dropped,
rebuilt, or corrupted without touching the integrity guarantee — the chain is
the record, the index is a convenience over it (docs/decisions.md D8).

Search is a core requirement rather than a follow-up because an audit log you
cannot query is an audit log nobody reads.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .ports import AuditRepository, AuditRow

__all__ = ["Hit", "rebuild", "search"]


@dataclass(frozen=True, slots=True)
class Hit:
    seq: int
    ts: datetime
    actor: str | None
    action: str
    payload: dict[str, Any]

    @classmethod
    def of(cls, row: AuditRow) -> Hit:
        return cls(
            seq=row.seq, ts=row.ts, actor=row.actor, action=row.action, payload=row.payload
        )


async def search(
    repository: AuditRepository,
    query: str = "",
    *,
    actor: str | None = None,
    action: str | None = None,
    limit: int = 50,
) -> Sequence[Hit]:
    """Search the chain. An empty query with filters is a plain listing."""

    limit = max(1, min(limit, 1000))
    rows = await repository.search(query, actor=actor, action=action, limit=limit)
    return [Hit.of(row) for row in rows]


async def rebuild(repository: AuditRepository) -> int:
    """Rebuild the derived index from the chain. Returns rows indexed."""

    return await repository.reindex()
