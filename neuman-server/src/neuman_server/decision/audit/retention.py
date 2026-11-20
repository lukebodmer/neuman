"""Retention over the audit chain and session logs.

Retention is in tension with a hash chain: purging a prefix breaks
verification from genesis, because the rows the chain commits to are gone. The
resolution is that purging is **from the front only**, and verification restarts
from the oldest surviving row, whose ``prev_hash`` becomes a checkpoint. The
mirror on disk keeps the full history for as long as the operator keeps it, so
the purge bounds the database, not the record.

Retention of 0 days means "keep forever", which is the default: silently
deleting a governance record because nobody configured a policy would be the
wrong failure direction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ...foundation.logging import get_logger
from .ports import AuditRepository

__all__ = ["PurgeResult", "purge_audit"]

_log = get_logger("neuman.server.audit.retention")


@dataclass(frozen=True, slots=True)
class PurgeResult:
    removed: int
    cutoff: datetime | None


async def purge_audit(
    repository: AuditRepository,
    *,
    retention_days: int,
    now: datetime | None = None,
) -> PurgeResult:
    """Purge chain rows older than the retention window."""

    if retention_days <= 0:
        return PurgeResult(removed=0, cutoff=None)

    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(days=retention_days)
    removed = await repository.purge_before(cutoff)
    if removed:
        _log.info(
            "audit rows purged",
            fields={"removed": removed, "cutoff": cutoff.isoformat()},
        )
    return PurgeResult(removed=removed, cutoff=cutoff)
