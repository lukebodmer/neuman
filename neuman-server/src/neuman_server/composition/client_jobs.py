"""Recording the end of a background job on a client (D98).

Not `jobs/`, which is the appliance's own periodic maintenance. These are a
model's commands that outlived their wait and kept running on the acting
human's machine; the hub hears how each one ended, and this writes it down.

Two records, for two readers. The audit chain gets `job.settled` every time,
because the effect ran past the call that authorised it and the record of how
long and how it ended is the operator's. The branch that started the job gets
a `harness_event` carrying a one-line notice — unless a `job_output` or
`job_kill` was waiting on it and has already told the model, which the client
reports as `observed`. The notice is in the log rather than held in memory
because history is re-derived from the log at every step (projection.py), and
it is written whether or not a turn is running: a job that ends between turns
is told at the start of the next one.
"""

from __future__ import annotations

from typing import Any

from ..adapters.clients import Job, JobSettled
from ..adapters.persistence import AuditStore, EventStore
from ..decision.audit import chain
from ..decision.harness import events
from ..decision.harness.jobs import SETTLED, ended_notice
from ..foundation import get_logger

__all__ = ["record_job_end"]

_log = get_logger("neuman.server.composition.client_jobs")


def record_job_end(services: Any) -> JobSettled:
    """The hub's `on_job_settled`, bound to this appliance's stores."""

    async def record(account: str, job: Job, frame: dict[str, Any]) -> None:
        ended = str(frame.get("ended") or "exited")
        exit_code = int(frame.get("exit_code") or 0)
        wall_ms = int(frame.get("wall_ms") or 0)
        observed = bool(frame.get("observed"))
        try:
            async with services.database.connection() as conn:
                await chain.append(
                    AuditStore(conn),
                    actor=account,
                    action="job.settled",
                    payload={
                        "tool": job.tool,
                        "job_id": job.job_id,
                        "dispatch_id": job.dispatch_id,
                        "session": job.session_id,
                        "branch": job.branch_id,
                        "ended": ended,
                        "exit_code": exit_code,
                        "wall_ms": wall_ms,
                        "observed": observed,
                    },
                    mirror=services.settings.audit_mirror_path,
                )
                if job.branch_id is None or observed:
                    return
                await events.append(
                    EventStore(conn),
                    branch_id=job.branch_id,
                    kind="harness_event",
                    payload={
                        "type": SETTLED,
                        "job_id": job.job_id,
                        "tool": job.tool,
                        "ended": ended,
                        "exit_code": exit_code,
                        "wall_ms": wall_ms,
                        "content": ended_notice(
                            job.job_id,
                            tool=job.tool,
                            ended=ended,
                            exit_code=exit_code,
                            wall_ms=wall_ms,
                            persisted_out=job.persisted_out,
                        ),
                    },
                )
        except Exception as exc:  # noqa: BLE001 - one record must not take the socket down
            _log.warning(
                "could not record a job's end",
                fields={"account": account, "job": job.job_id, "error": str(exc)},
            )

    return record
