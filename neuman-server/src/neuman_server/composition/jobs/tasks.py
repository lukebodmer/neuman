"""The task clock.

One loop for the appliance: find tasks whose next run has arrived, create the
run rows, and start whatever can start. The loop is the only place that reads
`tasks.next_run_at`, so a run is created once and a restart does not duplicate
it — the row was advanced in the same transaction that created the run.

A run whose owner has no client connected stays `waiting` until the catch-up
window ends; that is the honest state, not an error. The client is the executor
(D55), and a scheduled run on a machine that is asleep has nowhere to go.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ...foundation import get_logger
from ..tasks import tick

__all__ = ["INTERVAL_S", "task_loop"]

_log = get_logger("neuman.server.jobs.tasks")

# Thirty seconds is short enough that a 06:15 task is not visibly late and long
# enough that the loop is not a busy poll. The work is one indexed query and a
# handful of rows.
INTERVAL_S = 30.0


async def task_loop(services: Any, *, interval_s: float = INTERVAL_S) -> None:
    """Run the scheduler until cancelled."""

    while True:
        try:
            await tick(services)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad pass must not end the clock
            _log.warning("task tick failed", fields={"error": str(exc)})
        await asyncio.sleep(interval_s)
