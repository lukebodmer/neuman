"""Sweep of stale budget reservations.

A reservation is taken before a model call and resolved after it. A crash
between those two points leaves tokens reserved against an account forever,
which eventually looks like an exhausted budget with no usage behind it.

v1 zeroed an aggregate `reserved` column at startup, which works only while
calls are single-flight — a restart cannot leave a live call behind if there can
be only one. D19 targets hundreds of concurrent sessions, so reservations are
individual rows and are swept *by age* instead: a restart no longer has to be
the garbage collector, and a long-running server collects its own litter.

The window is generous on purpose. Sweeping a reservation whose call is still
running would let that call overshoot the budget.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ...adapters.persistence import UsageStore
from ...foundation import get_logger

__all__ = ["run_once", "settle_loop"]

_log = get_logger("neuman.server.jobs.settle")

INTERVAL_S = 300.0
# Comfortably longer than any single model call is allowed to take.
STALE_AFTER_S = 3600.0


async def run_once(services: Any, *, stale_after_s: float = STALE_AFTER_S) -> int:
    """Release reservations older than the window. Returns how many."""

    async with services.database.connection() as conn:
        released = await UsageStore(conn).sweep_stale(older_than_s=stale_after_s)

    if released:
        _log.warning("released stale reservations", fields={"count": released})
    return released


async def settle_loop(services: Any, *, interval_s: float = INTERVAL_S) -> None:
    """Sweep on a schedule until cancelled."""

    while True:
        try:
            await run_once(services)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            _log.warning("settle sweep failed", fields={"error": str(exc)})
        await asyncio.sleep(interval_s)
