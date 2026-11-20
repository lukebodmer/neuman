"""Ports the audit domain needs."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

__all__ = ["AuditRepository", "AuditRow"]


class AuditRow(Protocol):
    """One row of the hash chain."""

    @property
    def seq(self) -> int: ...

    @property
    def ts(self) -> datetime: ...

    @property
    def actor(self) -> str | None: ...

    @property
    def action(self) -> str: ...

    @property
    def payload(self) -> dict[str, Any]: ...

    @property
    def prev_hash(self) -> str: ...

    @property
    def hash(self) -> str: ...


class AuditRepository(Protocol):
    """Append-only storage for the chain, plus its derived search view."""

    async def append(
        self,
        *,
        actor: str | None,
        action: str,
        payload: dict[str, Any],
        prev_hash: str,
        digest: str,
        document: str,
    ) -> AuditRow:
        """Append one row. Must serialize against concurrent appends.

        The implementation takes the chain lock: two appends racing on the same
        ``prev_hash`` would fork the chain, and a forked chain is indistinguish-
        able from a tampered one.
        """

    async def head(self) -> AuditRow | None:
        """The most recent row, or None on an empty chain."""

    async def read(self, *, after: int = 0, limit: int = 1000) -> Sequence[AuditRow]:
        """Rows in chain order, for verification and export."""

    async def search(
        self,
        query: str,
        *,
        actor: str | None = None,
        action: str | None = None,
        limit: int = 50,
    ) -> Sequence[AuditRow]: ...

    async def reindex(self) -> int:
        """Rebuild the derived search view from the chain. Returns rows indexed."""

    async def purge_before(self, cutoff: datetime) -> int:
        """Delete rows older than ``cutoff``. Returns rows removed."""
