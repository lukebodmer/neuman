"""Tasks and their runs: the rows, not the decisions.

A task row is the standing approval; a run row is one execution of it. Neither
store validates policy or computes a schedule — those are `decision/tasks` and
the composition service's job. Keeping SQL here and judgement there is the same
split the session stores use, and it is what lets the schedule arithmetic be
tested without a database.

A run's `steps` are its step records as the `task-run` contract shapes them.
The session ids inside them are text, not foreign keys: retention may purge a
transcript, but "this task ran at 06:15 and its research step hit its budget" is
a governance fact that outlives the conversation.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

__all__ = ["TaskRow", "TaskRunRow", "TaskRunStore", "TaskStore"]


@dataclass(frozen=True, slots=True)
class TaskRow:
    id: str
    account_id: str
    name: str
    workspace: str
    run_root: str
    # The computer this task's steps run on (D112). A recipe is about files
    # somewhere, and the person who wrote it was looking at them.
    machine: str
    inputs: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    schedule: dict[str, Any]
    outputs: list[str]
    enabled: bool
    created_at: datetime
    updated_at: datetime
    next_run_at: datetime | None
    last_run_at: datetime | None

    @classmethod
    def of(cls, row: dict[str, Any]) -> TaskRow:
        return cls(
            id=row["id"],
            account_id=row["account_id"],
            name=row["name"],
            workspace=row["workspace"],
            run_root=row["run_root"],
            machine=row["machine"],
            inputs=list(row.get("inputs") or []),
            steps=list(row.get("steps") or []),
            schedule=dict(row.get("schedule") or {}),
            outputs=list(row.get("outputs") or []),
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            next_run_at=row.get("next_run_at"),
            last_run_at=row.get("last_run_at"),
        )


@dataclass(frozen=True, slots=True)
class TaskRunRow:
    id: str
    task_id: str
    account_id: str
    trigger: str
    status: str
    inputs: dict[str, str]
    run_dir: str | None
    steps: list[dict[str, Any]]
    outputs_present: list[str] | None
    due_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    cost_usd: float | None
    tokens: int | None
    created_at: datetime

    @classmethod
    def of(cls, row: dict[str, Any]) -> TaskRunRow:
        present = row.get("outputs_present")
        return cls(
            id=row["id"],
            task_id=row["task_id"],
            account_id=row["account_id"],
            trigger=row["trigger"],
            status=row["status"],
            inputs=dict(row.get("inputs") or {}),
            run_dir=row.get("run_dir"),
            steps=list(row.get("steps") or []),
            outputs_present=list(present) if present is not None else None,
            due_at=row.get("due_at"),
            started_at=row.get("started_at"),
            finished_at=row.get("finished_at"),
            error=row.get("error"),
            cost_usd=row.get("cost_usd"),
            tokens=row.get("tokens"),
            created_at=row["created_at"],
        )


class TaskStore:
    """The task rows. One account's tasks; no cross-account read here."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def create(
        self,
        *,
        account_id: str,
        name: str,
        workspace: str,
        run_root: str,
        machine: str,
        inputs: Sequence[dict[str, Any]],
        steps: Sequence[dict[str, Any]],
        schedule: dict[str, Any],
        outputs: Sequence[str],
        enabled: bool,
        next_run_at: datetime | None,
    ) -> TaskRow:
        from psycopg.types.json import Jsonb

        cursor = await self._conn.execute(
            """
            INSERT INTO tasks (
                id, account_id, name, workspace, run_root, machine, inputs, steps,
                schedule, outputs, enabled, next_run_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                uuid.uuid4().hex,
                account_id,
                name,
                workspace,
                run_root,
                machine,
                Jsonb(list(inputs)),
                Jsonb(list(steps)),
                Jsonb(schedule),
                Jsonb(list(outputs)),
                enabled,
                next_run_at,
            ),
        )
        return TaskRow.of(await cursor.fetchone())

    async def get(self, task_id: str) -> TaskRow | None:
        cursor = await self._conn.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
        row = await cursor.fetchone()
        return TaskRow.of(row) if row else None

    async def get_for_account(self, task_id: str, account_id: str) -> TaskRow | None:
        cursor = await self._conn.execute(
            "SELECT * FROM tasks WHERE id = %s AND account_id = %s",
            (task_id, account_id),
        )
        row = await cursor.fetchone()
        return TaskRow.of(row) if row else None

    async def list_for_account(self, account_id: str) -> Sequence[TaskRow]:
        cursor = await self._conn.execute(
            "SELECT * FROM tasks WHERE account_id = %s ORDER BY created_at DESC",
            (account_id,),
        )
        return [TaskRow.of(row) for row in await cursor.fetchall()]

    async def update(
        self,
        task_id: str,
        *,
        name: str,
        workspace: str,
        run_root: str,
        machine: str,
        inputs: Sequence[dict[str, Any]],
        steps: Sequence[dict[str, Any]],
        schedule: dict[str, Any],
        outputs: Sequence[str],
        enabled: bool,
        next_run_at: datetime | None,
    ) -> TaskRow | None:
        from psycopg.types.json import Jsonb

        cursor = await self._conn.execute(
            """
            UPDATE tasks
               SET name = %s,
                   workspace = %s,
                   run_root = %s,
                   machine = %s,
                   inputs = %s,
                   steps = %s,
                   schedule = %s,
                   outputs = %s,
                   enabled = %s,
                   next_run_at = %s,
                   updated_at = now()
             WHERE id = %s
            RETURNING *
            """,
            (
                name,
                workspace,
                run_root,
                machine,
                Jsonb(list(inputs)),
                Jsonb(list(steps)),
                Jsonb(schedule),
                Jsonb(list(outputs)),
                enabled,
                next_run_at,
                task_id,
            ),
        )
        row = await cursor.fetchone()
        return TaskRow.of(row) if row else None

    async def set_enabled(
        self, task_id: str, *, enabled: bool, next_run_at: datetime | None
    ) -> TaskRow | None:
        cursor = await self._conn.execute(
            """
            UPDATE tasks
               SET enabled = %s, next_run_at = %s, updated_at = now()
             WHERE id = %s
            RETURNING *
            """,
            (enabled, next_run_at, task_id),
        )
        row = await cursor.fetchone()
        return TaskRow.of(row) if row else None

    async def delete(self, task_id: str, account_id: str) -> bool:
        cursor = await self._conn.execute(
            "DELETE FROM tasks WHERE id = %s AND account_id = %s RETURNING id",
            (task_id, account_id),
        )
        return await cursor.fetchone() is not None

    async def due(self, now: datetime, *, limit: int = 10) -> Sequence[TaskRow]:
        """Tasks whose clock has reached them. Locked for one scheduler pass.

        `FOR UPDATE SKIP LOCKED` so a second scheduler (a restart racing the
        old process, or a future second instance) takes other rows rather than
        double-running this one. The transaction is the caller's.
        """

        cursor = await self._conn.execute(
            """
            SELECT *
              FROM tasks
             WHERE enabled
               AND next_run_at IS NOT NULL
               AND next_run_at <= %s
             ORDER BY next_run_at
             LIMIT %s
               FOR UPDATE SKIP LOCKED
            """,
            (now, limit),
        )
        return [TaskRow.of(row) for row in await cursor.fetchall()]

    async def advance(
        self, task_id: str, *, next_run_at: datetime | None, last_run_at: datetime
    ) -> None:
        await self._conn.execute(
            """
            UPDATE tasks
               SET next_run_at = %s, last_run_at = %s, updated_at = now()
             WHERE id = %s
            """,
            (next_run_at, last_run_at, task_id),
        )


class TaskRunStore:
    """One execution per row, and the queue the runner drains."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def create(
        self,
        *,
        task_id: str,
        account_id: str,
        trigger: str,
        due_at: datetime,
        inputs: Mapping[str, str],
        steps: Sequence[dict[str, Any]],
        status: str = "due",
        error: str | None = None,
    ) -> TaskRunRow:
        from psycopg.types.json import Jsonb

        cursor = await self._conn.execute(
            """
            INSERT INTO task_runs (
                id, task_id, account_id, trigger, status, inputs, steps, due_at, error
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                uuid.uuid4().hex,
                task_id,
                account_id,
                trigger,
                status,
                Jsonb(dict(inputs)),
                Jsonb(list(steps)),
                due_at,
                error,
            ),
        )
        return TaskRunRow.of(await cursor.fetchone())

    async def get(self, run_id: str) -> TaskRunRow | None:
        cursor = await self._conn.execute("SELECT * FROM task_runs WHERE id = %s", (run_id,))
        row = await cursor.fetchone()
        return TaskRunRow.of(row) if row else None

    async def list_for_task(self, task_id: str, *, limit: int = 20) -> Sequence[TaskRunRow]:
        cursor = await self._conn.execute(
            """
            SELECT *
              FROM task_runs
             WHERE task_id = %s
             ORDER BY created_at DESC
             LIMIT %s
            """,
            (task_id, limit),
        )
        return [TaskRunRow.of(row) for row in await cursor.fetchall()]

    async def since_for_account(
        self, account_id: str, moment: datetime
    ) -> Sequence[TaskRunRow]:
        """Every run of this account's tasks created since `moment`, newest first.

        The overview's history and its week's totals; one query rather than one
        per task, because the overview shows every task at once.
        """

        cursor = await self._conn.execute(
            """
            SELECT *
              FROM task_runs
             WHERE account_id = %s AND created_at >= %s
             ORDER BY created_at DESC
            """,
            (account_id, moment),
        )
        return [TaskRunRow.of(row) for row in await cursor.fetchall()]

    async def latest_for_task(self, task_id: str) -> TaskRunRow | None:
        cursor = await self._conn.execute(
            """
            SELECT *
              FROM task_runs
             WHERE task_id = %s
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (task_id,),
        )
        row = await cursor.fetchone()
        return TaskRunRow.of(row) if row else None

    async def pending(self, now: datetime, *, limit: int = 10) -> Sequence[TaskRunRow]:
        """Runs waiting for a client, oldest due first.

        Not locked here: `claim` is the atomic step that decides who runs a
        row, and a lock held by a reader that then starts a turn would serialize
        the scheduler behind the work it is starting.
        """

        cursor = await self._conn.execute(
            """
            SELECT *
              FROM task_runs
             WHERE status IN ('due', 'waiting')
               AND due_at <= %s
             ORDER BY due_at
             LIMIT %s
            """,
            (now, limit),
        )
        return [TaskRunRow.of(row) for row in await cursor.fetchall()]

    async def claim(self, run_id: str, *, run_dir: str) -> TaskRunRow | None:
        """Move a queued run to `running` in its directory, or None if someone else got it."""

        cursor = await self._conn.execute(
            """
            UPDATE task_runs
               SET status = 'running', started_at = now(), run_dir = %s, error = NULL
             WHERE id = %s AND status IN ('due', 'waiting')
            RETURNING *
            """,
            (run_dir, run_id),
        )
        row = await cursor.fetchone()
        return TaskRunRow.of(row) if row else None

    async def set_steps(self, run_id: str, steps: Sequence[dict[str, Any]]) -> None:
        """The step records, written as each step starts and ends."""

        from psycopg.types.json import Jsonb

        await self._conn.execute(
            "UPDATE task_runs SET steps = %s WHERE id = %s",
            (Jsonb(list(steps)), run_id),
        )

    async def wait(self, run_id: str, *, error: str | None = None) -> None:
        """No client is connected; keep the run for the next pass."""

        await self._conn.execute(
            "UPDATE task_runs SET status = 'waiting', error = %s WHERE id = %s",
            (error, run_id),
        )

    async def finish(
        self,
        run_id: str,
        *,
        status: str,
        error: str | None = None,
        steps: Sequence[dict[str, Any]] | None = None,
        outputs_present: Sequence[str] | None = None,
        cost_usd: float | None = None,
        tokens: int | None = None,
    ) -> None:
        from psycopg.types.json import Jsonb

        await self._conn.execute(
            """
            UPDATE task_runs
               SET status = %s,
                   finished_at = now(),
                   error = %s,
                   steps = COALESCE(%s, steps),
                   outputs_present = %s,
                   cost_usd = %s,
                   tokens = %s
             WHERE id = %s
            """,
            (
                status,
                error,
                Jsonb(list(steps)) if steps is not None else None,
                Jsonb(list(outputs_present)) if outputs_present is not None else None,
                cost_usd,
                tokens,
                run_id,
            ),
        )

    async def cancel_pending(self, task_id: str, *, reason: str) -> int:
        """A disabled or deleted task does not leave yesterday's run in the queue."""

        cursor = await self._conn.execute(
            """
            UPDATE task_runs
               SET status = 'cancelled', finished_at = now(), error = %s
             WHERE task_id = %s AND status IN ('due', 'waiting')
            RETURNING id
            """,
            (reason, task_id),
        )
        return len(await cursor.fetchall())

    async def reconcile_running(self, *, error: str) -> int:
        """A run cannot outlive the process running it.

        The steps run in an asyncio task on this server; a restart kills it
        without a terminal event. Without this pass the row would say `running`
        forever, and the rail would show a task that is not running. The step
        records and the transcripts are still the place to see how far it got.
        """

        cursor = await self._conn.execute(
            """
            UPDATE task_runs
               SET status = 'failed', finished_at = now(), error = %s
             WHERE status = 'running'
            RETURNING id
            """,
            (error,),
        )
        return len(await cursor.fetchall())
