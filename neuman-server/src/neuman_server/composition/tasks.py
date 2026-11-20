"""Tasks: authoring, the scheduler, and running a recipe.

This is the composition half of the task feature. It validates a draft against
the owner's *live* policy, stores it, computes the next due instant, and — when
a run is due and the owner's client is connected — walks the recipe's steps in
order (D95):

- a **run step** is one pre-approved `shell` dispatch of the command the owner
  wrote, through the same execution port and into the same audit chain as any
  call (D96);
- an **agent step** is a harness turn with `pre_approved=True`, in a session
  that belongs to the run rather than to chat, whose policy is its envelope
  intersected with the owner's live policy on every step (D93, D99).

What a run did is read back here too: the overview's history, a step's
transcript, and stopping a run. The task view polls these rather than holding
a socket (D99).

**The client needs no task-specific surface.** Everything a run asks of it is a
dispatch it already executes: the commands, the turns' tool calls, and the
`fs_write` that leaves the run's record in its directory (D94, D97).
"""

from __future__ import annotations

import asyncio
import json
import posixpath
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from ..adapters.persistence import (
    AccountStore,
    AgentSessionStore,
    AuditStore,
    BranchStore,
    EventStore,
    GrantStore,
    ImageStore,
    TaskRow,
    TaskRunRow,
    TaskRunStore,
    TaskStore,
    UsageStore,
)
from ..decision.audit import chain
from ..decision.harness import PROJECTION_KINDS, Actor, Harness, HarnessConfig, project
from ..decision.harness.loop import (
    BUDGET,
    INTERRUPTED,
    MAX_ITERATIONS,
    MODEL_ERROR,
    STOP,
)
from ..decision.policy.ops import preview_policy
from ..decision.policy.paths import expand_paths, within_reach
from ..decision.tasks import (
    CATCH_UP,
    RECORD_TOOL,
    RUN_RECORD,
    SHELL,
    AgentStep,
    Recipe,
    RunStep,
    TaskSchedule,
    TaskValidationError,
    command_line,
    intersect,
    resolve_inputs,
    run_dir_for,
    run_status,
    should_run,
    step_environment,
    validate,
    within_write,
)
from ..foundation import get_logger

__all__ = [
    "TaskBusy",
    "create_task",
    "delete_task",
    "get_task",
    "list_tasks",
    "reconcile_orphans",
    "run_json",
    "run_now",
    "start_run",
    "step_transcript",
    "stop_run",
    "task_json",
    "tick",
    "update_task",
]

_log = get_logger("neuman.server.tasks")

# The tool the declared outputs are looked for with: an ordinary dispatch, and
# optional — an owner without it gets no presence list rather than a failed run.
LIST_TOOL = "fs_list"

# How far back the overview's history reaches: two weeks of runs per task.
HISTORY = timedelta(days=14)

# How much of a run step's own output its record keeps. The end, because that
# is where a command says how it went; a whole build log belongs in the run
# directory, not in a database row read on every poll.
OUTPUT_TAIL = 8000


class TaskBusy(RuntimeError):
    """The task already has a run in flight; one run per task at a time."""


def _describe(exc: BaseException) -> str:
    message = str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def task_json(row: TaskRow, *, account: str) -> dict[str, Any]:
    """The stored task, in the contract's shape."""

    return {
        "id": row.id,
        "account": account,
        "name": row.name,
        "workspace": row.workspace,
        "run_root": row.run_root,
        "inputs": list(row.inputs),
        "steps": list(row.steps),
        "schedule": dict(row.schedule),
        "outputs": list(row.outputs),
        "enabled": row.enabled,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "next_run_at": _iso(row.next_run_at),
        "last_run_at": _iso(row.last_run_at),
    }


def run_json(row: TaskRunRow, *, account: str | None = None) -> dict[str, Any]:
    """One execution, in the contract's shape. Statuses come from structured results."""

    return {
        "id": row.id,
        "task_id": row.task_id,
        "account": account or row.account_id,
        "trigger": row.trigger,
        "status": row.status,
        "inputs": dict(row.inputs),
        "run_dir": row.run_dir,
        "steps": list(row.steps),
        "outputs_present": row.outputs_present,
        "due_at": _iso(row.due_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "error": row.error,
        "cost_usd": row.cost_usd,
        "tokens": row.tokens,
        "created_at": _iso(row.created_at),
    }


def _recipe(row: TaskRow) -> Recipe:
    return Recipe.of(
        workspace=row.workspace, run_root=row.run_root, inputs=row.inputs, steps=row.steps
    )


def _pending(recipe: Recipe) -> list[dict[str, Any]]:
    return [
        {"name": step.name, "kind": step.kind, "status": "pending"} for step in recipe.steps
    ]


async def _policy(services: Any, account: str) -> Any:
    async with services.database.connection() as conn:
        return await preview_policy(
            account,
            source=services.policy_source(),
            directory=services.directory,
            grants=GrantStore(conn),
        )


def _next_run(schedule: TaskSchedule, *, enabled: bool, now: datetime) -> datetime | None:
    return schedule.next_after(now) if enabled else None


def _check_draft(body: dict[str, Any]) -> None:
    """The wire contract, before the policy check.

    Shape first, then permission: a missing `workspace` should read as a bad
    request rather than as a policy refusal, and an unknown key should be a 400
    rather than a silently ignored field.
    """

    from neuman_contracts import task as contract

    contract.validate(body)


async def _validated(
    services: Any, account: str, body: dict[str, Any]
) -> tuple[Recipe, TaskSchedule, bool]:
    _check_draft(body)
    recipe = Recipe.of(
        workspace=body["workspace"],
        run_root=body["run_root"],
        inputs=body.get("inputs") or (),
        steps=body["steps"],
    )
    schedule = TaskSchedule.of(body["schedule"])
    enabled = bool(body.get("enabled", True))
    policy = await _policy(services, account)
    validate(
        policy,
        recipe,
        schedule,
        enabled=enabled,
        registry=services.registry,
        home=services.execution.home(account),
        data_dirs=services.settings.home_data_dirs,
    )
    return recipe, schedule, enabled


async def create_task(
    services: Any, *, account_id: str, account: str, body: dict[str, Any]
) -> dict[str, Any]:
    recipe, schedule, enabled = await _validated(services, account, body)
    now = datetime.now(UTC)
    async with services.database.connection() as conn:
        row = await TaskStore(conn).create(
            account_id=account_id,
            name=body["name"],
            workspace=recipe.workspace,
            run_root=recipe.run_root,
            inputs=recipe.inputs_json(),
            steps=recipe.steps_json(),
            schedule=schedule.to_json(),
            outputs=body.get("outputs") or (),
            enabled=enabled,
            next_run_at=_next_run(schedule, enabled=enabled, now=now),
        )
    return task_json(row, account=account)


async def list_tasks(services: Any, *, account_id: str, account: str) -> dict[str, Any]:
    """Everything the rail and the overview draw, in one read.

    `latest` is each task's newest run however old, so a task paused a month
    ago still says how it last went; `recent` is every run of the last
    `HISTORY`, which the overview's strip and its week's totals are drawn from.
    `connected` answers the question a scheduled run depends on: whether this
    account's client is there to carry it out (D94).
    """

    since = datetime.now(UTC) - HISTORY
    async with services.database.connection() as conn:
        rows = await TaskStore(conn).list_for_account(account_id)
        runs = {row.id: await TaskRunStore(conn).latest_for_task(row.id) for row in rows}
        recent = await TaskRunStore(conn).since_for_account(account_id, since)
    return {
        "tasks": [task_json(row, account=account) for row in rows],
        "latest": {
            task_id: run_json(run, account=account)
            for task_id, run in runs.items()
            if run is not None
        },
        "recent": [run_json(run, account=account) for run in recent],
        "connected": services.execution.connected(account),
    }


async def get_task(
    services: Any, *, account_id: str, account: str, task_id: str
) -> dict[str, Any] | None:
    async with services.database.connection() as conn:
        row = await TaskStore(conn).get(task_id)
        if row is None or row.account_id != account_id:
            return None
        runs = await TaskRunStore(conn).list_for_task(task_id)
    return {
        "task": task_json(row, account=account),
        "runs": [run_json(run, account=account) for run in runs],
    }


async def update_task(
    services: Any, *, account_id: str, account: str, task_id: str, body: dict[str, Any]
) -> dict[str, Any] | None:
    async with services.database.connection() as conn:
        existing = await TaskStore(conn).get(task_id)
        if existing is None or existing.account_id != account_id:
            return None

    merged = {
        "name": body.get("name", existing.name),
        "workspace": body.get("workspace", existing.workspace),
        "run_root": body.get("run_root", existing.run_root),
        "inputs": body.get("inputs", existing.inputs),
        "steps": body.get("steps", existing.steps),
        "schedule": body.get("schedule", existing.schedule),
        "outputs": body.get("outputs", existing.outputs),
        "enabled": bool(body.get("enabled", existing.enabled)),
    }
    recipe, schedule, enabled = await _validated(services, account, merged)

    now = datetime.now(UTC)
    schedule_changed = schedule.to_json() != existing.schedule
    if not enabled:
        next_run = None
    elif schedule_changed or not existing.enabled or existing.next_run_at is None:
        next_run = schedule.next_after(now)
    else:
        next_run = existing.next_run_at

    async with services.database.connection() as conn:
        row = await TaskStore(conn).update(
            task_id,
            name=merged["name"],
            workspace=recipe.workspace,
            run_root=recipe.run_root,
            inputs=recipe.inputs_json(),
            steps=recipe.steps_json(),
            schedule=schedule.to_json(),
            outputs=merged["outputs"] or (),
            enabled=enabled,
            next_run_at=next_run,
        )
        if not enabled:
            await TaskRunStore(conn).cancel_pending(task_id, reason="task disabled")
    return task_json(row, account=account) if row is not None else None


async def delete_task(services: Any, *, account_id: str, task_id: str) -> bool:
    async with services.database.connection() as conn:
        row = await TaskStore(conn).get(task_id)
        if row is None or row.account_id != account_id:
            return False
        await TaskRunStore(conn).cancel_pending(task_id, reason="task deleted")
        return await TaskStore(conn).delete(task_id, row.account_id)


async def run_now(
    services: Any,
    *,
    account_id: str,
    account: str,
    task_id: str,
    inputs: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Start a manual run with the given inputs. Raises `TaskValidationError` on bad inputs."""

    async with services.database.connection() as conn:
        task = await TaskStore(conn).get(task_id)
        if task is None or task.account_id != account_id:
            return None
        recipe = _recipe(task)
        resolved = resolve_inputs(recipe.inputs, dict(inputs or {}))
        latest = await TaskRunStore(conn).latest_for_task(task_id)
        if latest is not None and latest.status in ("due", "waiting", "running"):
            raise TaskBusy(f"task {task_id} already has a {latest.status} run")
        run = await TaskRunStore(conn).create(
            task_id=task.id,
            account_id=task.account_id,
            trigger="manual",
            due_at=datetime.now(UTC),
            inputs=resolved,
            steps=_pending(recipe),
        )
    started = await start_run(services, run.id)
    if started is None:
        async with services.database.connection() as conn:
            started = await TaskRunStore(conn).get(run.id)
    return run_json(started or run, account=account)


async def stop_run(
    services: Any, *, account_id: str, account: str, task_id: str
) -> dict[str, Any] | None:
    """Stop this task's run, whatever state it is in. None when there is nothing to stop.

    A queued run is cancelled in the store; a running one by cancelling its
    asyncio task, which `_execute` turns into a `cancelled` row with its open
    step closed — the same path a shutdown takes. An agent step's turn is a
    child of that task and goes with it.
    """

    async with services.database.connection() as conn:
        task = await TaskStore(conn).get(task_id)
        if task is None or task.account_id != account_id:
            return None
        latest = await TaskRunStore(conn).latest_for_task(task_id)
        if latest is None or latest.status not in ("due", "waiting", "running"):
            return None
        if latest.status != "running":
            await TaskRunStore(conn).cancel_pending(task_id, reason="stopped")
            return run_json(await TaskRunStore(conn).get(latest.id) or latest, account=account)

    runner = services.task_runners.get(latest.id)
    if runner is not None:
        runner.cancel()
        # Waited for, so the answer is the row as it ended rather than a run
        # that still says `running` for the moment `_finish` takes.
        await asyncio.gather(runner, return_exceptions=True)
    async with services.database.connection() as conn:
        final = await TaskRunStore(conn).get(latest.id)
    return run_json(final or latest, account=account)


async def step_transcript(
    services: Any, *, account_id: str, task_id: str, run_id: str, index: int
) -> dict[str, Any] | None:
    """What one agent step did: its transcript, as the chat window would replay it.

    Scoped through the task: the run must be this task's and the task this
    account's, and the session is the one the run's record names. The session
    is never opened by its own id here, so a task view cannot become a way into
    a conversation (D99). None for anything that does not resolve, including a
    run step, whose output is on its record, and a transcript retention purged.
    """

    async with services.database.connection() as conn:
        task = await TaskStore(conn).get(task_id)
        if task is None or task.account_id != account_id:
            return None
        run = await TaskRunStore(conn).get(run_id)
        if run is None or run.task_id != task_id or not 0 <= index < len(run.steps):
            return None
        record = run.steps[index]
        session_id, branch_id = record.get("session_id"), record.get("branch_id")
        if record.get("kind") != "agent" or not session_id or not branch_id:
            return None
        session = await AgentSessionStore(conn).owned_by(session_id, account_id)
        if session is None or session.task_run_id != run_id:
            return None
        events = await EventStore(conn).read(branch_id, kinds=PROJECTION_KINDS)
    return {
        "status": record.get("status"),
        "messages": project(events, for_display=True),
    }


async def reconcile_orphans(services: Any) -> int:
    """Fail every run a previous process left `running`.

    Called once at startup, before the scheduler's first tick. The steps were
    an asyncio task in the process that died; there is nothing to resume, and a
    row that stays `running` forever is a lie the rail shows.
    """

    async with services.database.connection() as conn:
        count = await TaskRunStore(conn).reconcile_running(
            error="server restarted before the run finished"
        )
    if count:
        _log.warning("reconciled interrupted task runs", fields={"count": count})
    return count


async def tick(services: Any) -> None:
    """One scheduler pass: create due runs, then start whatever can run."""

    now = datetime.now(UTC)
    created: list[str] = []
    async with services.database.connection() as conn:
        async with conn.transaction():
            due = await TaskStore(conn).due(now)
            for task in due:
                schedule = TaskSchedule.of(task.schedule)
                recipe = _recipe(task)
                runs = TaskRunStore(conn)
                due_at = task.next_run_at or now
                try:
                    resolved = resolve_inputs(recipe.inputs, dict(schedule.inputs))
                except TaskValidationError as exc:
                    # Authoring refuses this for an enabled task; an input edited
                    # since is what reaches here. Recorded, not skipped, so the
                    # history says why the morning's run did not happen.
                    run = await runs.create(
                        task_id=task.id,
                        account_id=task.account_id,
                        trigger="schedule",
                        due_at=due_at,
                        inputs={},
                        steps=_pending(recipe),
                    )
                    await runs.finish(run.id, status="failed", error=str(exc))
                else:
                    run = await runs.create(
                        task_id=task.id,
                        account_id=task.account_id,
                        trigger="schedule",
                        due_at=due_at,
                        inputs=resolved,
                        steps=_pending(recipe),
                    )
                    created.append(run.id)
                await TaskStore(conn).advance(
                    task.id,
                    next_run_at=schedule.next_after(now),
                    last_run_at=due_at,
                )

    # Start outside the scheduler transaction: `start_run` begins work on the
    # client, and holding the due-row locks across that would make the next
    # tick wait on work rather than on the clock.
    async with services.database.connection() as conn:
        pending = await TaskRunStore(conn).pending(now)
    for run in pending:
        await _start_if_possible(services, run, now=now)

    if created:
        _log.info("tasks due", fields={"runs": len(created)})


async def _start_if_possible(services: Any, run: TaskRunRow, *, now: datetime) -> None:
    async with services.database.connection() as conn:
        task = await TaskStore(conn).get(run.task_id)
        if task is None:
            await TaskRunStore(conn).finish(run.id, status="cancelled", error="task deleted")
            return
        if not task.enabled and run.trigger == "schedule":
            await TaskRunStore(conn).finish(run.id, status="cancelled", error="task disabled")
            return
        account = await AccountStore(conn).by_id(run.account_id)
        if account is None or account.disabled_at is not None:
            await TaskRunStore(conn).finish(
                run.id, status="failed", error="account disabled or missing"
            )
            return

    due_at = run.due_at or now
    if now - due_at > CATCH_UP:
        async with services.database.connection() as conn:
            await TaskRunStore(conn).finish(
                run.id,
                status="missed",
                error="no connected client before the catch-up window ended",
            )
        return

    if not services.execution.connected(account.name):
        if run.status == "due":
            async with services.database.connection() as conn:
                await TaskRunStore(conn).wait(run.id, error="waiting for a connected client")
        return

    await start_run(services, run.id)


async def start_run(services: Any, run_id: str) -> TaskRunRow | None:
    """Claim a queued run and start its steps. Returns the running row, or None.

    The steps run in an asyncio task registered in `Services.task_runners`, so a
    caller returns as soon as the run is under way; the run's terminal status is
    written by `_execute` when the last step ends.
    """

    async with services.database.connection() as conn:
        runs = TaskRunStore(conn)
        run = await runs.get(run_id)
        if run is None or run.status not in ("due", "waiting"):
            return None
        task = await TaskStore(conn).get(run.task_id)
        if task is None:
            await runs.finish(run_id, status="cancelled", error="task deleted")
            return await runs.get(run_id)
        account = await AccountStore(conn).by_id(task.account_id)
        if account is None or account.disabled_at is not None:
            await runs.finish(run_id, status="failed", error="account disabled or missing")
            return await runs.get(run_id)
        home = services.execution.home(account.name)
        if home is None:
            await runs.wait(run_id, error="waiting for a connected client")
            return await runs.get(run_id)

        # The policy can have narrowed since the task was authored. A run whose
        # workspace or run root the account can no longer reach would start and
        # fail on its first dispatch; name it now instead.
        policy = await preview_policy(
            account.name,
            source=services.policy_source(),
            directory=services.directory,
            grants=GrantStore(conn),
        )
        data_dirs = services.settings.home_data_dirs
        if not within_reach(task.workspace, policy, home=home, data_dirs=data_dirs):
            await runs.finish(
                run_id,
                status="failed",
                error=f"workspace {task.workspace!r} is outside the account's current reach",
            )
            return await runs.get(run_id)
        if not within_write(task.run_root, policy, home=home, data_dirs=data_dirs):
            await runs.finish(
                run_id,
                status="failed",
                error=f"run root {task.run_root!r} is outside the account's current reach",
            )
            return await runs.get(run_id)

        run_dir = run_dir_for(task.run_root, run.due_at or datetime.now(UTC), run.id)
        claimed = await runs.claim(run_id, run_dir=run_dir)
        if claimed is None:
            return None

    runner = asyncio.create_task(
        _execute(services, task=task, run=claimed, account=account, home=home),
        name=f"neuman.task.{run_id}",
    )
    services.task_runners[run_id] = runner
    runner.add_done_callback(lambda _done: services.task_runners.pop(run_id, None))
    return claimed


# -- execution ----------------------------------------------------------------


async def _execute(
    services: Any, *, task: TaskRow, run: TaskRunRow, account: Any, home: str
) -> None:
    """Walk the recipe, recording each step, then leave the run's record."""

    recipe = _recipe(task)
    records = [dict(record) for record in run.steps] or _pending(recipe)
    statuses: list[str] = []
    status = "failed"
    error: str | None = None
    current: int | None = None
    run_dir = run.run_dir or run_dir_for(task.run_root, datetime.now(UTC), run.id)

    async def save() -> None:
        async with services.database.connection() as conn:
            await TaskRunStore(conn).set_steps(run.id, records)

    try:
        # The directory first, by writing the record into it: a run whose
        # directory cannot be made has nowhere for its steps to work.
        written = await _write_record(
            services,
            account=account,
            home=home,
            task=task,
            run=run,
            run_dir=run_dir,
            record=_record(task, run, run_dir, records, status="running"),
        )
        if written is not None:
            raise _RunError(f"the run directory could not be made: {written}")

        for index, step in enumerate(recipe.steps):
            if not should_run(step.run_if, statuses):
                records[index]["status"] = "skipped"
                statuses.append("skipped")
                await save()
                continue
            current = index
            records[index].update(status="running", started_at=_now())
            await save()
            if isinstance(step, RunStep):
                outcome = await _run_command(
                    services,
                    account=account,
                    home=home,
                    task=task,
                    run=run,
                    run_dir=run_dir,
                    recipe=recipe,
                    step=step,
                )
            else:
                outcome = await _run_agent(
                    services,
                    account=account,
                    home=home,
                    task=task,
                    run=run,
                    run_dir=run_dir,
                    step=step,
                )
            records[index].update(outcome, finished_at=_now())
            statuses.append(records[index]["status"])
            current = None
            await save()
        status = run_status(statuses)
    except asyncio.CancelledError:
        status, error = "cancelled", "interrupted"
        _close_open_step(records, current, "cancelled", "interrupted")
        raise
    except _RunError as exc:
        status, error = "failed", str(exc)
    except BaseException as exc:  # noqa: BLE001 - a run that dies still has a row
        status, error = "failed", _describe(exc)
        _close_open_step(records, current, "failed", error)
        _log.exception("task run failed", fields={"run": run.id, "error": error})
    finally:
        await _finish(
            services,
            account=account,
            home=home,
            task=task,
            run=run,
            run_dir=run_dir,
            records=records,
            status=status,
            error=error,
        )


class _RunError(RuntimeError):
    """A run-level failure that is not one step's: no directory to work in."""


def _close_open_step(
    records: list[dict[str, Any]], index: int | None, status: str, error: str
) -> None:
    if index is not None and records[index].get("status") == "running":
        records[index].update(status=status, error=error, finished_at=_now())


async def _finish(
    services: Any,
    *,
    account: Any,
    home: str,
    task: TaskRow,
    run: TaskRunRow,
    run_dir: str,
    records: list[dict[str, Any]],
    status: str,
    error: str | None,
) -> None:
    """Total the spend, look for the outputs, write the row and the record."""

    priced = [record["cost_usd"] for record in records if record.get("cost_usd") is not None]
    cost = round(sum(priced), 6) if priced else None
    tokens = sum(record.get("tokens") or 0 for record in records) or None
    present = None
    try:
        present = await _outputs_present(
            services, account=account, home=home, task=task, run=run, run_dir=run_dir
        )
    except Exception as exc:  # noqa: BLE001 - presence is a courtesy, not the result
        _log.warning(
            "task outputs could not be listed", fields={"run": run.id, "error": str(exc)}
        )

    try:
        async with services.database.connection() as conn:
            runs = TaskRunStore(conn)
            await runs.finish(
                run.id,
                status=status,
                error=error,
                steps=records,
                outputs_present=present,
                cost_usd=cost,
                tokens=tokens,
            )
            final = await runs.get(run.id)
    except Exception as exc:  # noqa: BLE001 - the transcript already has the failure
        _log.warning(
            "task run status could not be written",
            fields={"run": run.id, "error": _describe(exc)},
        )
        return

    if final is not None:
        try:
            await _write_record(
                services,
                account=account,
                home=home,
                task=task,
                run=run,
                run_dir=run_dir,
                record=run_json(final, account=account.name),
            )
        except Exception as exc:  # noqa: BLE001 - the row is the record of last resort
            _log.warning(
                "task run record could not be written",
                fields={"run": run.id, "error": _describe(exc)},
            )


def _record(
    task: TaskRow,
    run: TaskRunRow,
    run_dir: str,
    records: list[dict[str, Any]],
    *,
    status: str,
) -> dict[str, Any]:
    """The record as it stands while the run is under way."""

    return {
        "id": run.id,
        "task_id": task.id,
        "account": run.account_id,
        "trigger": run.trigger,
        "status": status,
        "inputs": dict(run.inputs),
        "run_dir": run_dir,
        "steps": records,
        "outputs_present": None,
        "due_at": _iso(run.due_at),
        "started_at": _iso(run.started_at),
        "finished_at": None,
        "error": None,
        "cost_usd": None,
        "tokens": None,
        "created_at": _iso(run.created_at),
    }


async def _owner_dispatch(
    services: Any,
    *,
    account: Any,
    home: str,
    task: TaskRow,
    run: TaskRunRow,
    step: str | None,
    tool: str,
    args: dict[str, Any],
    wait_ms: int,
    working_dir: str | None = None,
) -> tuple[str, Any]:
    """One pre-approved dispatch the owner wrote rather than a model chose.

    Gated on the owner's live policy and recorded in the audit chain as a
    `tool.dispatch` with the task, run and step beside the dispatch id — the
    same action a model's call records, so an operator reading the chain sees
    every effect a task had, whoever chose it (D96).

    Waits as long as it lives: the owner's deadline, held under the tool's
    lifetime. Never a job — there is no session to follow one, and the step
    after it must not start while it still runs (D98).
    """

    manifest = services.registry.get(tool)
    if manifest is None or manifest.kind != "client":
        raise PermissionError(f"{tool!r} is not a client tool on this appliance")
    if manifest.critical:
        raise PermissionError(f"{tool!r} is critical; a task cannot be pre-approved for it")
    policy = await _policy(services, account.name)
    if tool not in policy.tools:
        raise PermissionError(f"{tool!r} is not granted to {account.name}")

    dispatch_id = uuid.uuid4().hex
    async with services.database.connection() as conn:
        await chain.append(
            AuditStore(conn),
            actor=account.name,
            action="tool.dispatch",
            payload={
                "tool": tool,
                "task": task.id,
                "run": run.id,
                "step": step,
                "dispatch_id": dispatch_id,
                "mutating": manifest.mutating,
                "critical": manifest.critical,
            },
            mirror=services.settings.audit_mirror_path,
        )

    data_dirs = services.settings.home_data_dirs
    outcome = await services.execution.dispatch(
        user=account.name,
        dispatch_id=dispatch_id,
        tool=tool,
        args=args,
        wait_ms=min(wait_ms, manifest.lifetime_ms),
        lifetime_ms=min(wait_ms, manifest.lifetime_ms),
        surfaces={
            "read": expand_paths(policy.files_read, home=home, data_dirs=data_dirs),
            "write": expand_paths(policy.files_write, home=home, data_dirs=data_dirs),
        },
        working_dir=working_dir,
    )
    return dispatch_id, outcome


async def _run_command(
    services: Any,
    *,
    account: Any,
    home: str,
    task: TaskRow,
    run: TaskRunRow,
    run_dir: str,
    recipe: Recipe,
    step: RunStep,
) -> dict[str, Any]:
    """A run step: the owner's command, quoted here, dispatched as `shell` (D96)."""

    cwd = recipe.cwd_of(step)
    env = step_environment(task_id=task.id, run_id=run.id, run_dir=run_dir, inputs=run.inputs)
    try:
        dispatch_id, outcome = await _owner_dispatch(
            services,
            account=account,
            home=home,
            task=task,
            run=run,
            step=step.name,
            tool=SHELL,
            args={"command": command_line(step.argv, env), "cwd": cwd},
            wait_ms=step.timeout_s * 1000,
            working_dir=cwd,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - the step fails; the run records why
        return {"status": "failed", "exit_code": None, "error": _describe(exc)}

    if outcome.timed_out:
        error = f"timed out after {outcome.wall_ms} ms"
    elif outcome.cancelled:
        error = "cancelled"
    elif outcome.exit_code != 0:
        error = f"exit {outcome.exit_code}"
    else:
        error = None
    return {
        "status": "succeeded" if outcome.ok else "failed",
        "exit_code": None if outcome.timed_out or outcome.cancelled else outcome.exit_code,
        "error": error,
        "output": _output(outcome),
        "dispatch_id": dispatch_id,
    }


def _output(outcome: Any) -> str | None:
    """The command's own words for the task view: stdout, then stderr, each's end.

    Two streams rather than one interleaved, because the client reports them
    apart; stderr last because it is where a failing command says why. Each is
    held to half of `OUTPUT_TAIL` so a chatty stdout cannot push the error off.
    """

    half = OUTPUT_TAIL // 2
    parts = []
    for stream in (outcome.stdout, outcome.stderr):
        text = (stream or "").rstrip()
        if not text:
            continue
        parts.append(text if len(text) <= half else "…" + text[-(half - 1) :])
    return "\n".join(parts) or None


def _status_for(reason: str) -> str:
    if reason == STOP:
        return "succeeded"
    if reason in (MAX_ITERATIONS, BUDGET):
        return "budget"
    if reason == INTERRUPTED:
        return "cancelled"
    if reason == MODEL_ERROR:
        return "failed"
    return "failed"


async def _no_emit(_event: dict[str, Any]) -> None:
    """A task turn has no browser socket behind it; the log is the transcript.

    The task view follows a running step by reading that log (`step_transcript`)
    rather than by receiving these frames (D99).
    """


def _opening(run: TaskRunRow, run_dir: str, instruction: str) -> str:
    """The step's instruction, opened with what is specific to this run (D97)."""

    lines = [f"Task run {run.id}.", f"Run directory: {run_dir}"]
    if run.inputs:
        lines.append("Inputs: " + ", ".join(f"{k}={v}" for k, v in sorted(run.inputs.items())))
    return "\n".join(lines) + "\n\n" + instruction


async def _run_agent(
    services: Any,
    *,
    account: Any,
    home: str,
    task: TaskRow,
    run: TaskRunRow,
    run_dir: str,
    step: AgentStep,
) -> dict[str, Any]:
    """An agent step: a session, a branch and one pre-approved turn (D93).

    The session is the run's, not a conversation: it never appears in the chat
    window, and the task view reads it through the run (D99).
    """

    envelope = step.envelope.resolve(run_dir)
    working_dir = run_dir if step.cwd == "$RUN_DIR" else step.cwd

    async with services.database.connection() as conn:
        session, branch = await AgentSessionStore(conn).create(
            account_id=account.id, title=f"{task.name} · {step.name}", task_run_id=run.id
        )
        branches = BranchStore(conn)
        await branches.set_model_reasoning(
            branch.id, model=step.model, reasoning=step.reasoning
        )
        await branches.set_working_dir(branch.id, working_dir)

    inbox = services.inbox_for(branch.id)

    async def turn() -> Any:
        async with services.database.connection() as conn:
            grants = GrantStore(conn)
            source = services.policy_source()

            async def policy_for(name: str):
                base = await preview_policy(
                    name,
                    source=source,
                    directory=services.directory,
                    grants=grants,
                )
                return intersect(
                    base,
                    envelope,
                    home=home,
                    data_dirs=services.settings.home_data_dirs,
                )

            harness = Harness(
                gateway=services.gateway,
                execution=services.execution,
                registry=services.registry,
                audit_repository=AuditStore(conn),
                event_repository=EventStore(conn),
                usage=UsageStore(conn),
                images=ImageStore(conn),
                policy_for=policy_for,
                resolve_model=services.model_catalog().resolve,
                config=HarnessConfig(
                    home_data_dirs=services.settings.home_data_dirs,
                    tool_wait_ms=services.settings.tool_wait_ms,
                    approval_timeout_s=services.settings.approval_timeout_s,
                    max_tokens=services.settings.max_tokens,
                    operator_prompt=services.operator_prompt,
                    agent_name=services.agent_name,
                    build_id=services.settings.build_id,
                ),
                context=services.context,
                # A task may not use a server-native tool (the authoring check
                # refuses one), so there is no handler to wire.
                server_native={},
                emit=_no_emit,
                audit_mirror=services.settings.audit_mirror_path,
            )
            return await harness.run_turn(
                branch_id=branch.id,
                session_id=session.id,
                actor=Actor(account_id=account.id, name=account.name, home=home),
                message=_opening(run, run_dir, step.instruction),
                model=step.model,
                inbox=inbox,
                working_dir=working_dir,
                reasoning=step.reasoning,
                account_prompt=account.instructions,
                pre_approved=True,
            )

    # Its own asyncio task, registered as the branch's running turn, so an
    # operator's overview counts it and an interrupt reaches its inbox exactly as
    # it reaches a chat turn's — and so the registry entry ends with the turn
    # rather than with the run around it. The chat rail does not show it: its
    # session is the run's (D99).
    running = asyncio.create_task(turn(), name=f"neuman.task.{run.id}.{step.name}")
    services.begin_turn(
        branch.id,
        account=account.name,
        account_id=account.id,
        session_id=session.id,
        inbox=inbox,
        task=running,
    )
    record: dict[str, Any] = {"session_id": session.id, "branch_id": branch.id}
    try:
        result = await running
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - the step fails; the run records why
        _log.exception("task agent step failed", fields={"run": run.id, "step": step.name})
        return {**record, "status": "failed", "error": _describe(exc)}
    finally:
        # A job the step started ends with the step (D98). Its session is
        # over, so nothing will look at the job again, and the step after this
        # one must not start beside a build still writing. Each kill is
        # recorded as `job.settled` when the client reports it.
        ended = await services.execution.end_session_jobs(
            user=account.name, session_id=session.id
        )
        if ended:
            _log.warning(
                "task step ended with jobs running; killed them",
                fields={"run": run.id, "step": step.name, "jobs": ended},
            )

    status = _status_for(result.reason)
    return {
        **record,
        "status": status,
        "error": None if status == "succeeded" else result.reason,
        "iterations": result.steps,
        "cost_usd": result.cost_usd,
        "tokens": result.tokens,
    }


async def _write_record(
    services: Any,
    *,
    account: Any,
    home: str,
    task: TaskRow,
    run: TaskRunRow,
    run_dir: str,
    record: dict[str, Any],
) -> str | None:
    """Write `neuman-run.json` into the run directory. Returns an error, or None."""

    try:
        _, outcome = await _owner_dispatch(
            services,
            account=account,
            home=home,
            task=task,
            run=run,
            step=None,
            tool=RECORD_TOOL,
            args={
                "path": f"{run_dir}/{RUN_RECORD}",
                "content": json.dumps(record, indent=2) + "\n",
            },
            wait_ms=services.settings.tool_wait_ms,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - reported to the caller as a reason
        return _describe(exc)
    if not outcome.ok:
        return (outcome.stderr or outcome.stdout or f"exit {outcome.exit_code}").strip()
    return None


async def _outputs_present(
    services: Any, *, account: Any, home: str, task: TaskRow, run: TaskRunRow, run_dir: str
) -> list[str] | None:
    """Which declared outputs exist. Presence only; whether they are right is not ours."""

    if not task.outputs:
        return []
    policy = await _policy(services, account.name)
    if LIST_TOOL not in policy.tools:
        return None
    present: list[str] = []
    listed: dict[str, set[str]] = {}
    for output in task.outputs:
        parent, name = posixpath.split(posixpath.join(run_dir, output))
        if parent not in listed:
            _, outcome = await _owner_dispatch(
                services,
                account=account,
                home=home,
                task=task,
                run=run,
                step=None,
                tool=LIST_TOOL,
                args={"path": parent, "summarise": False},
                wait_ms=services.settings.tool_wait_ms,
            )
            listed[parent] = (
                {
                    line.split("\t", 1)[1]
                    for line in outcome.stdout.splitlines()
                    if line.startswith("f\t")
                }
                if outcome.ok
                else set()
            )
        if name in listed[parent]:
            present.append(output)
    return present
