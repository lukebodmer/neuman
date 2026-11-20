"""Repair of the derived session search index.

The index is maintained inline on every event append, so this is a *repair*
mechanism rather than the normal path — the same shape and the same reasoning as
`audit_index.py`. It rebuilds when the index and the log have diverged, which
happens after a restore, a retention purge, or a crash between the event insert
and the index insert.

Because the index is derived data (docs/decisions.md D8), rebuilding it is
always safe and never touches the event log it indexes.
"""

from __future__ import annotations

from typing import Any

from ...adapters.persistence import AgentSessionStore
from ...foundation import get_logger

__all__ = ["reconcile_session_index"]

_log = get_logger("neuman.server.jobs.session_index")


async def reconcile_session_index(services: Any) -> int:
    """Rebuild the index if it disagrees with the log. Returns rows indexed.

    Compared on *sessions that said something* rather than on row counts: the
    index holds one row per session, the log holds one per event, so the two
    numbers are never equal and a count comparison would rebuild on every boot.

    Called at startup rather than on a timer, like the audit reconcile:
    divergence is caused by events, not by the passage of time.
    """

    async with services.database.connection() as conn:
        cursor = await conn.execute(
            """
            SELECT (
                     SELECT count(DISTINCT session_id)
                       FROM session_events
                      WHERE kind IN ('user_message', 'response_final')
                   ) AS spoken,
                   (SELECT count(*) FROM session_search) AS indexed
            """
        )
        row = await cursor.fetchone()
        spoken = int(row["spoken"])
        indexed = int(row["indexed"])

        if spoken == indexed:
            return indexed

        _log.warning(
            "session search index diverged from the log; rebuilding",
            fields={"sessions_with_messages": spoken, "indexed": indexed},
        )
        return await AgentSessionStore(conn).reindex_search()
