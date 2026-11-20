"""Background tasks. Process-level concerns; they decide nothing."""

from __future__ import annotations

import asyncio
from typing import Any

from ...foundation import get_logger
from ..tasks import reconcile_orphans
from .audit_index import reconcile_index
from .retention import retention_loop
from .session_index import reconcile_session_index
from .settle import settle_loop
from .tasks import task_loop
from .watchers import watch_directory

__all__ = [
    "reconcile_index",
    "reconcile_session_index",
    "retention_loop",
    "settle_loop",
    "start_jobs",
    "stop_jobs",
]

_log = get_logger("neuman.server.jobs")


async def start_jobs(services: Any) -> list[asyncio.Task[Any]]:
    """Start the background jobs. Called last, once the app is built."""

    # A one-shot repair before the periodic work begins.
    try:
        await reconcile_index(services)
    except Exception as exc:  # noqa: BLE001 - never block startup on a repair
        _log.warning("audit index reconcile failed", fields={"error": str(exc)})

    # The session search index, repaired the same way and for the same reasons.
    # Separately guarded: one index failing to rebuild must not stop the other
    # from trying, and neither may block the appliance from serving.
    try:
        await reconcile_session_index(services)
    except Exception as exc:  # noqa: BLE001 - never block startup on a repair
        _log.warning("session index reconcile failed", fields={"error": str(exc)})

    # A run cannot outlive the process that started it. This one is a repair of
    # a *record*, not an index: a row left `running` by a restart is a lie the
    # rail would show until someone looked.
    try:
        await reconcile_orphans(services)
    except Exception as exc:  # noqa: BLE001 - never block startup on a repair
        _log.warning("task run reconcile failed", fields={"error": str(exc)})

    async def on_overlay_change(filename: str) -> None:
        # Policy is re-read per dispatch, so nothing needs invalidating; this
        # exists so live clients can be told, and so the change is visible in
        # the log (docs/decisions.md D14).
        services.directory.invalidate()
        _log.info("policy overlay changed", fields={"file": filename})

    tasks = [
        asyncio.create_task(retention_loop(services), name="neuman.retention"),
        asyncio.create_task(settle_loop(services), name="neuman.settle"),
        asyncio.create_task(task_loop(services), name="neuman.tasks"),
        asyncio.create_task(
            watch_directory(services.settings.overlay_dir, on_overlay_change),
            name="neuman.watch-overlay",
        ),
    ]
    return tasks


async def stop_jobs(tasks: list[asyncio.Task[Any]]) -> None:
    """Cancel every job and wait for it to finish."""

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
