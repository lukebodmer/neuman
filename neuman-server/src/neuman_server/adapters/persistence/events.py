"""Operational events for the admin panel.

The audit chain records governance: who changed what. It is not a log sink, and
journald is not portable into a container. This table holds the warnings and
errors an operator wants to see in the panel, with enough context to act on
them. It is deliberately not every INFO line: a table that mirrors the log
would grow without bound and answer less than the log does.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

__all__ = ["ServerEventRow", "ServerEventStore"]


@dataclass(frozen=True, slots=True)
class ServerEventRow:
    id: int
    at: datetime
    level: str
    logger: str
    message: str
    fields: dict[str, Any]

    @classmethod
    def of(cls, row: dict[str, Any]) -> ServerEventRow:
        fields = row.get("fields") or {}
        return cls(
            id=row["id"],
            at=row["at"],
            level=row["level"],
            logger=row["logger"],
            message=row["message"],
            fields=json.loads(fields) if isinstance(fields, str) else fields,
        )


class ServerEventStore:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def add(
        self,
        *,
        level: str,
        logger: str,
        message: str,
        fields: dict[str, Any] | None = None,
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO events (level, logger, message, fields)
            VALUES (%s, %s, %s, %s::jsonb)
            """,
            (level, logger, message, json.dumps(fields or {})),
        )

    async def recent(
        self, *, limit: int = 100, level: str | None = None
    ) -> Sequence[ServerEventRow]:
        if level is None:
            cursor = await self._conn.execute(
                "SELECT * FROM events ORDER BY at DESC, id DESC LIMIT %s", (limit,)
            )
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM events WHERE level = %s ORDER BY at DESC, id DESC LIMIT %s",
                (level, limit),
            )
        return [ServerEventRow.of(row) for row in await cursor.fetchall()]

    async def count_since(self, moment: datetime, levels: tuple[str, ...]) -> dict[str, int]:
        """How many events of each of `levels` were recorded since `moment`."""

        cursor = await self._conn.execute(
            """
            SELECT level, count(*) AS n FROM events
             WHERE at >= %s AND level = ANY(%s)
             GROUP BY level
            """,
            (moment, list(levels)),
        )
        found = {row["level"]: int(row["n"]) for row in await cursor.fetchall()}
        return {level: found.get(level, 0) for level in levels}

    async def purge_before(self, moment: datetime) -> int:
        cursor = await self._conn.execute("DELETE FROM events WHERE at < %s", (moment,))
        return cursor.rowcount
