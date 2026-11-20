"""The audit repository: the hash chain and its derived search view.

Implements `decision.audit.ports.AuditRepository`.

**Appends serialize.** Two appends computing a digest from the same
``prev_hash`` would fork the chain, and a forked chain is indistinguishable from
a tampered one. A Postgres advisory lock held for the append transaction makes
the read-compute-write sequence atomic across connections and across processes;
a `UNIQUE` constraint on `hash` is the backstop if it ever is not.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

__all__ = ["AuditRowRecord", "AuditStore"]

# Arbitrary but fixed: the advisory-lock key for the audit chain.
_CHAIN_LOCK = 0x4E45574D41554449  # "NEWMAUDI"


@dataclass(frozen=True, slots=True)
class AuditRowRecord:
    seq: int
    ts: datetime
    actor: str | None
    action: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str

    @classmethod
    def of(cls, row: dict[str, Any]) -> AuditRowRecord:
        return cls(
            seq=int(row["seq"]),
            ts=row["ts"],
            actor=row["actor"],
            action=row["action"],
            payload=row["payload"] or {},
            prev_hash=row["prev_hash"],
            hash=row["hash"],
        )


class AuditStore:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def append(
        self,
        *,
        actor: str | None,
        action: str,
        payload: dict[str, Any],
        prev_hash: str,
        digest: str,
        document: str,
    ) -> AuditRowRecord:
        from psycopg.types.json import Jsonb

        async with self._conn.transaction():
            await self._conn.execute("SELECT pg_advisory_xact_lock(%s)", (_CHAIN_LOCK,))

            cursor = await self._conn.execute(
                """
                INSERT INTO audit_chain (actor, action, payload, prev_hash, hash)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING *
                """,
                (actor, action, Jsonb(payload), prev_hash, digest),
            )
            row = await cursor.fetchone()

            await self._conn.execute(
                """
                INSERT INTO audit_search (seq, document)
                VALUES (%s, to_tsvector('simple', %s))
                ON CONFLICT (seq) DO UPDATE SET document = EXCLUDED.document
                """,
                (row["seq"], document),
            )

        return AuditRowRecord.of(row)

    async def head(self) -> AuditRowRecord | None:
        cursor = await self._conn.execute("SELECT * FROM audit_chain ORDER BY seq DESC LIMIT 1")
        row = await cursor.fetchone()
        return AuditRowRecord.of(row) if row else None

    async def read(self, *, after: int = 0, limit: int = 1000) -> Sequence[AuditRowRecord]:
        cursor = await self._conn.execute(
            "SELECT * FROM audit_chain WHERE seq > %s ORDER BY seq LIMIT %s",
            (after, limit),
        )
        return [AuditRowRecord.of(row) for row in await cursor.fetchall()]

    async def search(
        self,
        query: str,
        *,
        actor: str | None = None,
        action: str | None = None,
        limit: int = 50,
    ) -> Sequence[AuditRowRecord]:
        clauses: list[str] = []
        params: list[Any] = []

        if query:
            clauses.append(
                "c.seq IN (SELECT seq FROM audit_search "
                "WHERE document @@ plainto_tsquery('simple', %s))"
            )
            params.append(query)
        if actor:
            clauses.append("c.actor = %s")
            params.append(actor)
        if action:
            clauses.append("c.action = %s")
            params.append(action)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)

        cursor = await self._conn.execute(
            f"SELECT c.* FROM audit_chain c {where} ORDER BY c.seq DESC LIMIT %s",
            tuple(params),
        )
        return [AuditRowRecord.of(row) for row in await cursor.fetchall()]

    async def recent_of(self, prefixes: Sequence[str], limit: int) -> Sequence[AuditRowRecord]:
        """The newest entries whose action starts with one of `prefixes`."""

        cursor = await self._conn.execute(
            """
            SELECT * FROM audit_chain
             WHERE action LIKE ANY(%s)
             ORDER BY seq DESC LIMIT %s
            """,
            ([f"{prefix}%" for prefix in prefixes], limit),
        )
        return [AuditRowRecord.of(row) for row in await cursor.fetchall()]

    async def reindex(self) -> int:
        """Rebuild the derived view from the chain.

        Never touches `audit_chain`: the index is derived data, and rebuilding
        it must not be able to damage the record it indexes.
        """

        async with self._conn.transaction():
            await self._conn.execute("TRUNCATE audit_search")
            cursor = await self._conn.execute(
                """
                INSERT INTO audit_search (seq, document)
                SELECT seq,
                       to_tsvector(
                           'simple',
                           coalesce(actor, '') || ' ' || action || ' ' || payload::text
                       )
                  FROM audit_chain
                """
            )
        return cursor.rowcount

    async def purge_before(self, cutoff: datetime) -> int:
        """Delete chain rows older than ``cutoff``.

        Front-only, so verification restarts from the oldest surviving row and
        its `prev_hash` becomes the checkpoint. The JSONL mirror keeps whatever
        the operator keeps.
        """

        cursor = await self._conn.execute("DELETE FROM audit_chain WHERE ts < %s", (cutoff,))
        return cursor.rowcount
