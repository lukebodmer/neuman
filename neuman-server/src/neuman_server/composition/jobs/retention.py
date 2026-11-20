"""Scheduled purge of expired sessions and out-of-window audit rows.

Retention of 0 means keep forever, and that is the default: silently deleting a
governance record because nobody configured a policy is the wrong failure
direction (docs/decisions.md D8).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from ...adapters.persistence import (
    AgentSessionStore,
    AuditStore,
    ImageStore,
    SessionStore,
)
from ...decision.audit import purge_audit
from ...foundation import get_logger

__all__ = ["retention_loop", "run_once"]

_log = get_logger("neuman.server.jobs.retention")

INTERVAL_S = 3600.0


async def run_once(services: Any) -> dict[str, int]:
    """One retention pass. Returns what it removed."""

    settings = services.settings
    now = datetime.now(UTC)
    removed = {
        "sessions": 0,
        "audit": 0,
        "agent_sessions": 0,
        "images": 0,
        "image_bytes": 0,
    }

    async with services.database.connection() as conn:
        # Expired login sessions are always purged: they confer nothing and
        # keeping them is pure cost.
        removed["sessions"] = await SessionStore(conn).purge_expired(now=now)

        if settings.session_retention_days > 0:
            cutoff = now - timedelta(days=settings.session_retention_days)
            # One predicate and one delete with the manual purge: `purgeable`
            # means "no activity since", so the job and
            # `neuman-admin session purge` can never disagree about what is old.
            # Purging cascades the image references away, so the sweep is the
            # second half of the deletion and runs in the same transaction. It
            # runs whenever retention is on, not only when this pass removed a
            # session, so a sweep interrupted after a purge is retried rather
            # than leaving the bytes forever.
            async with conn.transaction():
                candidates = await AgentSessionStore(conn).purgeable(cutoff)
                removed["agent_sessions"] = await AgentSessionStore(conn).purge_ids(
                    [candidate.session.id for candidate in candidates]
                )
                swept = await ImageStore(conn).sweep()
            removed["images"] = swept.images
            removed["image_bytes"] = swept.bytes

        result = await purge_audit(
            AuditStore(conn),
            retention_days=settings.audit_retention_days,
            now=now,
        )
        removed["audit"] = result.removed

    if any(removed.values()):
        _log.info("retention pass complete", fields=removed)
    return removed


async def retention_loop(services: Any, *, interval_s: float = INTERVAL_S) -> None:
    """Run retention on a schedule until cancelled."""

    while True:
        try:
            await run_once(services)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a bad pass must not end the job
            _log.warning("retention pass failed", fields={"error": str(exc)})
        await asyncio.sleep(interval_s)
