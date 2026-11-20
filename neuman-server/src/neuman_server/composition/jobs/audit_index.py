"""Refresh of the derived audit search index.

The index is maintained inline on every append, so this job is a *repair*
mechanism rather than the normal path: it rebuilds from the chain when the two
have diverged, which can happen after a restore, a manual purge, or a crash
between the chain insert and the index insert.

Because the index is derived data (docs/decisions.md D8), rebuilding it is always safe and
never touches the record it indexes.
"""

from __future__ import annotations

from typing import Any

from ...adapters.persistence import AuditStore
from ...decision.audit import rebuild
from ...foundation import get_logger

__all__ = ["reconcile_index"]

_log = get_logger("neuman.server.jobs.audit_index")


async def reconcile_index(services: Any) -> int:
    """Rebuild the index if it disagrees with the chain. Returns rows indexed.

    Called at startup rather than on a timer: divergence is caused by events
    (restore, crash), not by the passage of time.
    """

    async with services.database.connection() as conn:
        cursor = await conn.execute(
            """
            SELECT (SELECT count(*) FROM audit_chain)  AS chain,
                   (SELECT count(*) FROM audit_search) AS indexed
            """
        )
        row = await cursor.fetchone()
        chain_rows = int(row["chain"])
        indexed_rows = int(row["indexed"])

        if chain_rows == indexed_rows:
            return indexed_rows

        _log.warning(
            "audit index diverged from the chain; rebuilding",
            fields={"chain": chain_rows, "indexed": indexed_rows},
        )
        return await rebuild(AuditStore(conn))
