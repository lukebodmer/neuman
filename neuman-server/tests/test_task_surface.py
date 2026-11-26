"""The task surface: authoring is a policy decision, and a run walks its recipe.

The route tests are about the contract the browser sees — validation refuses a
draft the owner cannot support, a run with no connected client is `waiting`
rather than a failure, and one account cannot read another's tasks. The run
tests drive `start_run` against a connected fake client, because what they
prove is the order and shape of the dispatches a recipe becomes: the record
first, then each step, then the record again. The scheduler test drives `tick`
directly, because the loop's timing is not what it is proving.
"""

from __future__ import annotations

import copy
import json
import shlex
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fakes import manifest
from surface import build_surface

from neuman_server.composition import tasks as task_service
from neuman_server.decision.harness.ports import Chunk, DispatchOutcome

COOKIE = "neuman_session"
WORKSPACE = "/srv/eng/site"
RUN_ROOT = "/srv/eng/work/runs"


@pytest.fixture
def surface(monkeypatch, tmp_path):
    built = build_surface(monkeypatch, tmp_path)
    # A run step is a `shell` dispatch (D96), so the owner must hold `shell`.
    built.services.registry.tools["shell"] = manifest(
        "shell",
        needs={"read", "write"},
        mutating=True,
        critical=False,
        wait_ms=120_000,
        lifetime_ms=3_600_000,
    )
    grant(built, "shell")
    return built


def grant(surface, tool: str) -> None:
    """Grant `tool` in the stored baseline, and in memory for tests that never start the app.

    The app loads `services.baseline` from the stored row when it starts; a run
    test drives the service directly and never starts it, so it needs both.
    """

    surface.settings.values["baseline"]["defaults"]["tools"].append(tool)
    surface.services.baseline = copy.deepcopy(surface.settings.values["baseline"])


@pytest.fixture
def client(surface):
    with surface.client() as running:
        yield running


def sign_in(surface, client, name: str = "alice") -> str:
    token, csrf = surface.login(name)
    client.cookies.set(COOKIE, token)
    return csrf


def research(**overrides) -> dict:
    step = {
        "kind": "agent",
        "name": "research",
        "instruction": "Read brief.md in your working directory and follow it.",
        "model": "m",
        "envelope": {
            "tools": ["fs_read", "fs_write"],
            "files_read": ["$RUN_DIR"],
            "files_write": ["$RUN_DIR"],
            "network": "none",
            "models": ["m"],
            "max_iterations": 20,
        },
    }
    step.update(overrides)
    return step


def draft(**overrides) -> dict:
    body = {
        "name": "Site update",
        "workspace": WORKSPACE,
        "run_root": RUN_ROOT,
        # Where the recipe runs. Required, and never inferred: a task that does
        # not say is a task that waits for whichever client happens to be up
        # (D112).
        "machine": "ernest",
        "inputs": [
            {
                "name": "kind",
                "values": ["sweep", "refresh"],
                "required": True,
                "default": "sweep",
            },
            {"name": "entity"},
        ],
        "steps": [
            {"kind": "run", "name": "prepare", "argv": ["pnpm", "prepare"], "timeout_s": 600},
            research(),
            {
                "kind": "run",
                "name": "check",
                "argv": ["pnpm", "check"],
                "timeout_s": 600,
                "run_if": "ok_or_budget",
            },
        ],
        "schedule": {"kind": "daily", "time": "06:15", "timezone": "Europe/Zurich"},
        "outputs": ["claims.json", "report.md"],
        "enabled": True,
    }
    body.update(overrides)
    return body


def create(surface, client, body: dict, *, name: str = "alice"):
    csrf = sign_in(surface, client, name)
    return client.post("/api/tasks", json=body, headers={"x-csrf-token": csrf})


async def stored_task(surface, **overrides):
    """A task row written straight to the store, as an authored one would be."""

    body = draft(**overrides)
    return await surface.tasks.create(
        account_id="acct-alice",
        name=body["name"],
        workspace=body["workspace"],
        run_root=body["run_root"],
        machine=body["machine"],
        inputs=body["inputs"],
        steps=body["steps"],
        schedule=body["schedule"],
        outputs=body["outputs"],
        enabled=True,
        next_run_at=datetime.now(UTC),
    )


async def queued_run(surface, task, inputs=None):
    return await surface.task_runs.create(
        task_id=task.id,
        account_id="acct-alice",
        trigger="manual",
        due_at=datetime.now(UTC),
        inputs=inputs or {"kind": "sweep"},
        steps=[
            {"name": step["name"], "kind": step["kind"], "status": "pending"}
            for step in task.steps
        ],
    )


def connect(surface) -> None:
    surface.services.execution.connected_names = ["alice"]
    surface.services.execution.home_value = "/home/alice"


async def run_to_end(surface, run):
    started = await task_service.start_run(surface.services, run.id)
    assert started is not None
    assert started.status == "running"
    await surface.services.task_runners[run.id]
    return await surface.task_runs.get(run.id)


def stops() -> list:
    return [[Chunk("content", {"text": "done"}), Chunk("finish", {"reason": "stop"})]]


# -- authoring ----------------------------------------------------------------


def test_create_list_and_open_a_task(surface, client):
    response = create(surface, client, draft())
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["account"] == "alice"
    assert task["name"] == "Site update"
    assert task["enabled"] is True
    assert task["next_run_at"] is not None
    assert [step["name"] for step in task["steps"]] == ["prepare", "research", "check"]

    listed = client.get("/api/tasks").json()
    assert [row["id"] for row in listed["tasks"]] == [task["id"]]
    assert listed["latest"] == {}
    assert listed["recent"] == []
    # No client is connected in this surface, and the overview says so.
    assert listed["connected"] is False

    opened = client.get(f"/api/tasks/{task['id']}").json()
    assert opened["task"]["id"] == task["id"]
    assert opened["runs"] == []


def test_a_draft_outside_the_owners_policy_is_refused(surface, client):
    step = research(envelope={**research()["envelope"], "files_read": ["$RUN_DIR", "/etc"]})
    response = create(surface, client, draft(steps=[step]))
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "task_not_allowed"
    assert any("/etc" in problem for problem in body["problems"])


def test_an_agent_step_that_could_write_a_run_step_is_refused(surface, client):
    """D96, through the route: the problem names the overlap to fix."""

    step = research(
        envelope={**research()["envelope"], "files_write": ["$RUN_DIR", "/srv/eng/work"]}
    )
    body = draft(
        workspace="/srv/eng/work/site",
        run_root="/srv/eng/work/runs",
        steps=[
            step,
            {"kind": "run", "name": "check", "argv": ["pnpm", "check"], "timeout_s": 60},
        ],
    )
    response = create(surface, client, body)
    assert response.status_code == 400
    assert "must not be able to write what a run step runs" in response.text


def test_a_critical_tool_cannot_be_pre_approved(surface, client):
    surface.services.registry.tools["wipe"] = manifest("wipe", needs={"read"}, critical=True)
    grant(surface, "wipe")

    step = research(envelope={**research()["envelope"], "tools": ["wipe"]})
    response = create(surface, client, draft(steps=[step]))
    assert response.status_code == 400
    assert "critical" in response.text


def test_a_missing_field_is_a_bad_request(surface, client):
    body = draft()
    del body["run_root"]
    response = create(surface, client, body)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_a_draft_cannot_set_the_task_id(surface, client):
    response = create(surface, client, {**draft(), "id": "a" * 32})
    assert response.status_code == 400
    assert "server-owned" in response.text


def test_disabling_clears_the_next_run(surface, client):
    task = create(surface, client, draft()).json()
    csrf = sign_in(surface, client)
    response = client.patch(
        f"/api/tasks/{task['id']}", json={"enabled": False}, headers={"x-csrf-token": csrf}
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["enabled"] is False
    assert updated["next_run_at"] is None


def test_delete_removes_the_task(surface, client):
    task = create(surface, client, draft()).json()
    csrf = sign_in(surface, client)
    response = client.delete(f"/api/tasks/{task['id']}", headers={"x-csrf-token": csrf})
    assert response.status_code == 204
    assert client.get(f"/api/tasks/{task['id']}").status_code == 404


def test_one_account_cannot_read_anothers_task(surface, client):
    task = create(surface, client, draft()).json()
    token, csrf = surface.login("bob")
    client.cookies.set(COOKIE, token)
    assert client.get(f"/api/tasks/{task['id']}").status_code == 404
    assert (
        client.patch(
            f"/api/tasks/{task['id']}",
            json={"name": "mine"},
            headers={"x-csrf-token": csrf},
        ).status_code
        == 404
    )


# -- run now ------------------------------------------------------------------


def test_run_now_waits_for_a_connected_client(surface, client):
    task = create(surface, client, draft()).json()
    csrf = sign_in(surface, client)
    response = client.post(
        f"/api/tasks/{task['id']}/run",
        json={"inputs": {"kind": "refresh", "entity": "model/x"}},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 202, response.text
    run = response.json()["run"]
    assert run["status"] == "waiting"
    assert run["trigger"] == "manual"
    assert run["inputs"] == {"kind": "refresh", "entity": "model/x"}
    assert run["run_dir"] is None
    assert [step["status"] for step in run["steps"]] == ["pending"] * 3
    assert "waiting" in (run["error"] or "")


def test_run_now_fills_defaults_without_a_body(surface, client):
    task = create(surface, client, draft()).json()
    csrf = sign_in(surface, client)
    response = client.post(f"/api/tasks/{task['id']}/run", headers={"x-csrf-token": csrf})
    assert response.status_code == 202, response.text
    assert response.json()["run"]["inputs"] == {"kind": "sweep"}


def test_run_now_refuses_an_input_outside_its_values(surface, client):
    task = create(surface, client, draft()).json()
    csrf = sign_in(surface, client)
    response = client.post(
        f"/api/tasks/{task['id']}/run",
        json={"inputs": {"kind": "delete"}},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_inputs"


def test_a_second_run_while_one_is_pending_is_refused(surface, client):
    task = create(surface, client, draft()).json()
    csrf = sign_in(surface, client)
    first = client.post(f"/api/tasks/{task['id']}/run", headers={"x-csrf-token": csrf})
    assert first.status_code == 202
    second = client.post(f"/api/tasks/{task['id']}/run", headers={"x-csrf-token": csrf})
    assert second.status_code == 409
    assert second.json()["error"] == "task_busy"


# -- a run walks its recipe ---------------------------------------------------


async def test_a_run_walks_its_steps_in_their_own_directory(surface):
    """The whole point: a recipe becomes dispatches and a turn, in order.

    The record is written first (which is what makes the directory), then the
    prepare command, then the research turn in the run directory, then the
    check command, then the record again with how it ended.
    """

    connect(surface)
    surface.gateway.scripts = stops()
    task = await stored_task(surface)
    run = await queued_run(surface, task, inputs={"kind": "refresh", "entity": "model/x"})

    finished = await run_to_end(surface, run)

    run_dir = f"{RUN_ROOT}/{run.due_at.date().isoformat()}-{run.id}"
    assert finished.run_dir == run_dir
    assert finished.status == "succeeded"
    assert [step["status"] for step in finished.steps] == ["succeeded"] * 3

    dispatches = surface.services.execution.dispatches
    shells = [call for call in dispatches if call["tool"] == "shell"]
    assert [shlex.split(call["args"]["command"])[-2:] for call in shells] == [
        ["pnpm", "prepare"],
        ["pnpm", "check"],
    ]
    first = shlex.split(shells[0]["args"]["command"])
    assert f"NEUMAN_RUN_DIR={run_dir}" in first
    # The owner's deadline, waited out whole: past `shell`'s two-minute wait,
    # within its hour, and never a job — no session follows a run step and the
    # step after it must not start beside it (D96, D98).
    assert [(call["wait_ms"], call["lifetime_ms"]) for call in shells] == [
        (600_000, 600_000)
    ] * 2
    assert all(call["session_id"] is None for call in shells)
    assert "NEUMAN_INPUT_ENTITY=model/x" in first
    assert shells[0]["args"]["cwd"] == WORKSPACE

    records = [
        call
        for call in dispatches
        if call["tool"] == "fs_write" and call["args"]["path"] == f"{run_dir}/neuman-run.json"
    ]
    assert dispatches[0] is records[0]
    assert json.loads(records[0]["args"]["content"])["status"] == "running"
    final = json.loads(records[-1]["args"]["content"])
    assert final["status"] == "succeeded"
    assert [step["name"] for step in final["steps"]] == ["prepare", "research", "check"]

    agent = finished.steps[1]
    session = surface.agent_sessions.rows[agent["session_id"]]
    # A task runs as its owner, not as a service account.
    assert session.account_id == "acct-alice"
    # Whatever the step left running ended with it, before `check` started.
    assert surface.services.execution.ended_sessions == [agent["session_id"]]


async def test_a_run_step_is_in_the_audit_chain_with_its_task(surface):
    connect(surface)
    surface.gateway.scripts = stops()
    task = await stored_task(surface)
    run = await queued_run(surface, task)

    await run_to_end(surface, run)

    steps = [
        row.payload
        for row in surface.audit.rows
        if row.action == "tool.dispatch" and row.payload.get("tool") == "shell"
    ]
    assert [payload["step"] for payload in steps] == ["prepare", "check"]
    assert all(payload["task"] == task.id and payload["run"] == run.id for payload in steps)


async def test_a_failed_step_skips_what_needs_it_and_runs_what_does_not(surface):
    connect(surface)
    surface.services.execution.outcomes["shell"] = DispatchOutcome(
        exit_code=2, stderr="pnpm: no such script"
    )
    steps = draft()["steps"]
    steps[2] = {**steps[2], "run_if": "always"}
    task = await stored_task(surface, steps=steps)
    run = await queued_run(surface, task)

    finished = await run_to_end(surface, run)

    assert finished.status == "failed"
    assert [step["status"] for step in finished.steps] == ["failed", "skipped", "failed"]
    assert finished.steps[0]["exit_code"] == 2
    assert finished.steps[0]["error"] == "exit 2"
    # The command's own words are kept on the step for the task view to show.
    assert finished.steps[0]["output"] == "pnpm: no such script"
    # The research turn never started, so the model was never asked.
    assert surface.gateway.scripts == []


async def test_a_budget_stop_still_runs_an_ok_or_budget_step(surface):
    connect(surface)
    steps = draft()["steps"]
    steps[1] = research(envelope={**research()["envelope"], "max_iterations": 1})
    surface.gateway.scripts = [
        [
            Chunk("tool_call", {"index": 0, "id": "c1", "function": {"name": "fs_read"}}),
            Chunk("tool_call", {"index": 0, "function": {"arguments": '{"path": "/x"}'}}),
            Chunk("finish", {"reason": "tool_calls"}),
        ],
    ]
    task = await stored_task(surface, steps=steps)
    run = await queued_run(surface, task)

    finished = await run_to_end(surface, run)

    assert [step["status"] for step in finished.steps] == ["succeeded", "budget", "succeeded"]
    assert finished.status == "budget"


async def test_an_agent_step_dispatches_inside_its_run_directory(surface):
    """The envelope reaches the client as the surfaces it checks paths against.

    The gate decides which tools are advertised; the client decides which paths
    a path-taking tool may touch. An agent step must carry its envelope's
    intersection, resolved to this run's directory — not the owner's whole
    reach, or the envelope would be a display rather than a boundary.
    """

    connect(surface)
    surface.gateway.scripts = [
        [
            Chunk("tool_call", {"index": 0, "id": "c1", "function": {"name": "fs_read"}}),
            Chunk(
                "tool_call", {"index": 0, "function": {"arguments": '{"path": "/etc/passwd"}'}}
            ),
            Chunk("finish", {"reason": "tool_calls"}),
        ],
        [
            Chunk("content", {"text": "I could not read it."}),
            Chunk("finish", {"reason": "stop"}),
        ],
    ]
    task = await stored_task(surface, steps=[research()])
    run = await queued_run(surface, task)

    finished = await run_to_end(surface, run)

    assert finished.status == "succeeded"
    model_call = next(
        call
        for call in surface.services.execution.dispatches
        if call["args"].get("path") == "/etc/passwd"
    )
    assert model_call["tool"] == "fs_read"
    assert model_call["surfaces"]["read"] == [finished.run_dir]
    assert model_call["surfaces"]["write"] == [finished.run_dir]
    assert model_call["working_dir"] == finished.run_dir


async def test_declared_outputs_are_looked_for_when_the_run_ends(surface):
    connect(surface)
    surface.services.registry.tools["fs_list"] = manifest("fs_list", needs={"read"})
    grant(surface, "fs_list")
    surface.services.execution.outcomes["fs_list"] = DispatchOutcome(
        exit_code=0, stdout="f\tclaims.json\nf\tneuman-run.json\n"
    )
    surface.gateway.scripts = stops()
    task = await stored_task(surface)
    run = await queued_run(surface, task)

    finished = await run_to_end(surface, run)

    assert finished.outputs_present == ["claims.json"]


async def test_a_restart_fails_a_run_left_running(surface):
    """A run cannot outlive the process that was executing it.

    Without the startup repair the row would say `running` forever; the rail
    would show a task that is not running, and no later tick would touch it.
    """

    run = await surface.task_runs.create(
        task_id="a" * 32,
        account_id="acct-alice",
        trigger="schedule",
        due_at=datetime.now(UTC),
        inputs={},
        steps=[],
        status="running",
    )

    count = await task_service.reconcile_orphans(surface.services)

    assert count == 1
    assert surface.task_runs.rows[run.id].status == "failed"
    assert "restarted" in (surface.task_runs.rows[run.id].error or "")


# -- the clock ----------------------------------------------------------------


async def test_tick_creates_a_due_run_with_the_schedules_inputs(surface):
    """The scheduler's one job: one run per due task, then move the next run.

    No client is connected, so the run it creates is `waiting` — which is the
    state the rail and the run history are meant to show, not an error.
    """

    schedule = {
        "kind": "daily",
        "time": "06:15",
        "timezone": "Europe/Zurich",
        "inputs": {"kind": "sweep"},
    }
    task = await stored_task(surface, schedule=schedule)
    surface.tasks.rows[task.id] = replace(
        task, next_run_at=datetime.now(UTC) - timedelta(minutes=5)
    )

    await task_service.tick(surface.services)

    runs = list(surface.task_runs.rows.values())
    assert len(runs) == 1
    assert runs[0].task_id == task.id
    assert runs[0].trigger == "schedule"
    assert runs[0].status == "waiting"
    assert runs[0].inputs == {"kind": "sweep"}

    updated = surface.tasks.rows[task.id]
    assert updated.next_run_at is not None
    assert updated.next_run_at > datetime.now(UTC)
    assert updated.last_run_at is not None


async def test_tick_records_a_run_whose_inputs_no_longer_resolve(surface):
    """An input made required after the schedule was set: the run says why it failed."""

    inputs = [{"name": "entity", "required": True}]
    await stored_task(surface, inputs=inputs)

    await task_service.tick(surface.services)

    (run,) = surface.task_runs.rows.values()
    assert run.status == "failed"
    assert "'entity' is required" in (run.error or "")


# -- what a run did, read back --------------------------------------------------


async def test_a_run_step_keeps_the_end_of_its_output(surface):
    """Stdout then stderr, each held to half the tail, so stderr is never pushed off."""

    connect(surface)
    surface.gateway.scripts = stops()
    chatty = "line\n" * 5000
    surface.services.execution.outcomes["shell"] = DispatchOutcome(
        exit_code=0, stdout=chatty, stderr="warning: 2 pages skipped"
    )
    task = await stored_task(surface)
    run = await queued_run(surface, task)

    finished = await run_to_end(surface, run)

    output = finished.steps[0]["output"]
    assert output.startswith("…")
    assert output.endswith("line\nwarning: 2 pages skipped")
    assert len(output) <= task_service.OUTPUT_TAIL


async def test_an_agent_steps_session_is_the_tasks_not_chats(surface):
    """D99: the transcript is read through the run, and chat never lists it."""

    connect(surface)
    surface.gateway.scripts = stops()
    task = await stored_task(surface)
    run = await queued_run(surface, task)

    finished = await run_to_end(surface, run)

    agent = finished.steps[1]
    session = surface.agent_sessions.rows[agent["session_id"]]
    assert session.task_run_id == run.id
    sessions = surface.agent_sessions
    assert await sessions.visible_to(session.id, "acct-alice") is None
    assert session.id not in [
        row.id for row in await sessions.list_for_account("acct-alice", sort="used")
    ]

    read = await task_service.step_transcript(
        surface.services, account_id="acct-alice", task_id=task.id, run_id=run.id, index=1
    )
    assert read["status"] == "succeeded"
    assert [m["role"] for m in read["messages"]] == ["user", "assistant"]
    assert read["messages"][-1]["content"] == "done"


async def test_a_step_transcript_is_scoped_to_the_owner_and_to_agent_steps(surface):
    connect(surface)
    surface.gateway.scripts = stops()
    task = await stored_task(surface)
    run = await queued_run(surface, task)
    await run_to_end(surface, run)

    def read(**overrides):
        args = {"account_id": "acct-alice", "task_id": task.id, "run_id": run.id, "index": 1}
        args.update(overrides)
        return task_service.step_transcript(surface.services, **args)

    assert await read() is not None
    assert await read(account_id="acct-bob") is None
    # A run step has no transcript; its output is on its record.
    assert await read(index=0) is None
    assert await read(index=9) is None
    assert await read(run_id="0" * 32) is None


async def test_stopping_a_queued_run_cancels_it(surface):
    task = await stored_task(surface)
    run = await queued_run(surface, task)

    stopped = await task_service.stop_run(
        surface.services, account_id="acct-alice", account="alice", task_id=task.id
    )

    assert stopped["id"] == run.id
    assert stopped["status"] == "cancelled"
    # Nothing left to stop, and nobody else's task to stop.
    assert (
        await task_service.stop_run(
            surface.services, account_id="acct-alice", account="alice", task_id=task.id
        )
        is None
    )


async def test_stopping_a_running_run_cancels_its_step(surface):
    """The runner is cancelled, the open step closed, and the row says so."""

    import asyncio

    connect(surface)
    started = asyncio.Event()

    execution = surface.services.execution
    real = execution.dispatch

    # The record is written and then the first command never returns, the way
    # a long build holds its step.
    async def hang(**kwargs):
        if kwargs["tool"] == "shell":
            started.set()
            await asyncio.sleep(3600)
        return await real(**kwargs)

    execution.dispatch = hang
    task = await stored_task(surface)
    run = await queued_run(surface, task)
    assert await task_service.start_run(surface.services, run.id) is not None
    await asyncio.wait_for(started.wait(), 5)

    stopped = await task_service.stop_run(
        surface.services, account_id="acct-alice", account="alice", task_id=task.id
    )

    assert stopped["status"] == "cancelled"
    assert stopped["steps"][0]["status"] == "cancelled"
    assert run.id not in surface.services.task_runners


def test_the_overview_lists_the_recent_runs(surface, client):
    csrf = sign_in(surface, client)
    created = client.post("/api/tasks", json=draft(), headers={"x-csrf-token": csrf}).json()
    client.post(f"/api/tasks/{created['id']}/run", headers={"x-csrf-token": csrf})

    listed = client.get("/api/tasks").json()

    assert [run["task_id"] for run in listed["recent"]] == [created["id"]]
    assert listed["recent"][0]["status"] == "waiting"


def test_stop_and_step_routes_answer_for_the_owner_only(surface, client):
    csrf = sign_in(surface, client)
    created = client.post("/api/tasks", json=draft(), headers={"x-csrf-token": csrf}).json()
    run = client.post(f"/api/tasks/{created['id']}/run", headers={"x-csrf-token": csrf}).json()

    step = client.get(f"/api/tasks/{created['id']}/runs/{run['run']['id']}/steps/1")
    # Queued, so the agent step has no session yet.
    assert step.status_code == 404

    stopped = client.post(f"/api/tasks/{created['id']}/stop", headers={"x-csrf-token": csrf})
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["run"]["status"] == "cancelled"
    again = client.post(f"/api/tasks/{created['id']}/stop", headers={"x-csrf-token": csrf})
    assert again.status_code == 409

    bob = sign_in(surface, client, "bob")
    other = client.post(f"/api/tasks/{created['id']}/stop", headers={"x-csrf-token": bob})
    assert other.status_code == 409
