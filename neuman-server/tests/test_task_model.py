"""The task recipe, envelope and schedule: the decisions a task cannot delegate.

The point of these tests is the property the feature rests on: a task is a
*narrowing* of its owner's policy. If `intersect` ever produced a field the
account did not hold, the standing approval would be a grant, and the security model
would be false — and if an agent step could write what a run step runs, a page
the agent read could choose the next command (D96).
"""

from __future__ import annotations

import shlex
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeRegistry, manifest

from neuman_server.decision.policy.compose import EffectivePolicy
from neuman_server.decision.tasks import (
    Recipe,
    TaskEnvelope,
    TaskInput,
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


def account(**overrides) -> EffectivePolicy:
    base = dict(
        tools=("fs_read", "fs_list", "fs_write", "web_fetch"),
        files_read=("/srv", "/var/lib/runs"),
        files_write=("/srv/work", "/var/lib/runs"),
        models=("DeepSeek-V4.1-Flash", "Qwen3.8-27B-Q4"),
        network="any",
        auto_accept="never",
        auto_accept_max="never",
        max_iterations=120,
        tokens_daily=2_000_000,
    )
    base.update(overrides)
    return EffectivePolicy(**base)


def envelope(**overrides) -> TaskEnvelope:
    base = dict(
        tools=("fs_read", "fs_write", "web_fetch"),
        files_read=("/var/lib/runs",),
        files_write=("/var/lib/runs",),
        network="any",
        models=("DeepSeek-V4.1-Flash",),
        max_iterations=80,
    )
    base.update(overrides)
    return TaskEnvelope(**base)


# -- schedules ---------------------------------------------------------------


def test_a_daily_schedule_keeps_its_own_wall_clock():
    """06:15 in Zurich is 06:15 in Zurich after the clocks change, not 05:15 UTC.

    A saved UTC offset would be right until the first transition and wrong
    twice a year after it, which is exactly the bug a person would report as
    "it ran at the wrong hour".
    """

    schedule = TaskSchedule(kind="daily", time="06:15", timezone="Europe/Zurich")
    # 2026-11-09 is after the autumn transition (CET, UTC+1). 01:00 UTC is
    # 02:00 local, so today's 06:15 is still ahead: 05:15 UTC, not 06:15 UTC.
    due = schedule.next_after(datetime(2026, 11, 9, 1, 0, tzinfo=UTC))
    assert due == datetime(2026, 11, 9, 5, 15, tzinfo=UTC)
    assert due.astimezone(schedule._zone()).hour == 6

    # 2026-03-30 is after the spring transition (CEST, UTC+2).
    due = schedule.next_after(datetime(2026, 3, 30, 1, 0, tzinfo=UTC))
    assert due == datetime(2026, 3, 30, 4, 15, tzinfo=UTC)
    assert due.astimezone(schedule._zone()).hour == 6


def test_a_schedule_that_has_not_passed_today_is_today():
    schedule = TaskSchedule(kind="daily", time="06:15", timezone="UTC")
    due = schedule.next_after(datetime(2026, 11, 9, 1, 0, tzinfo=UTC))
    assert due == datetime(2026, 11, 9, 6, 15, tzinfo=UTC)


def test_a_schedule_that_has_passed_today_is_tomorrow():
    schedule = TaskSchedule(kind="daily", time="06:15", timezone="UTC")
    due = schedule.next_after(datetime(2026, 11, 9, 7, 0, tzinfo=UTC))
    assert due == datetime(2026, 11, 10, 6, 15, tzinfo=UTC)


def test_weekly_finds_the_next_named_day():
    # 2026-11-09 is a Monday. Ask for Friday at 09:00.
    schedule = TaskSchedule(kind="weekly", time="09:00", weekday=4, timezone="Europe/Zurich")
    due = schedule.next_after(datetime(2026, 11, 9, 12, 0, tzinfo=UTC))
    assert due == datetime(2026, 11, 13, 8, 0, tzinfo=UTC)


def test_weekly_on_the_same_day_after_the_time_is_next_week():
    # Monday 09:00 has passed on Monday 12:00; the next one is a week later.
    schedule = TaskSchedule(kind="weekly", time="09:00", weekday=0, timezone="UTC")
    due = schedule.next_after(datetime(2026, 11, 9, 12, 0, tzinfo=UTC))
    assert due == datetime(2026, 11, 16, 9, 0, tzinfo=UTC)


def test_an_interval_is_measured_from_now():
    schedule = TaskSchedule(kind="interval", minutes=90)
    now = datetime(2026, 11, 9, 6, 0, tzinfo=UTC)
    assert schedule.next_after(now) == now + timedelta(minutes=90)


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "hourly"},
        {"kind": "daily", "time": "25:00", "timezone": "UTC"},
        {"kind": "daily", "time": "06:15", "timezone": "Not/AZone"},
        {"kind": "weekly", "time": "06:15", "timezone": "UTC"},
        {"kind": "interval", "minutes": 5},
    ],
)
def test_bad_schedules_are_refused(body):
    with pytest.raises(TaskValidationError):
        TaskSchedule.of(body)


# -- the envelope narrows, never widens --------------------------------------


def test_intersect_takes_the_narrower_of_every_field():
    effective = intersect(account(), envelope(), home=None)
    assert effective.tools == ("fs_read", "fs_write", "web_fetch")
    assert effective.files_read == ("/var/lib/runs",)
    assert effective.files_write == ("/var/lib/runs",)
    assert effective.models == ("DeepSeek-V4.1-Flash",)
    assert effective.network == "any"
    assert effective.max_iterations == 80


def test_intersect_drops_what_the_account_does_not_hold():
    """The account lost `fs_write`; the task's copy of it must not survive."""

    effective = intersect(
        account(tools=("fs_read",), files_write=()),
        envelope(),
        home=None,
    )
    assert "fs_write" not in effective.tools
    assert effective.files_write == ()


def test_intersect_keeps_only_the_overlapping_prefix():
    """A task over `/srv/work/today` is narrower than a grant of `/srv/work`."""

    effective = intersect(
        account(files_read=("/srv",), files_write=("/srv/work",)),
        envelope(
            files_read=("/srv/work/today",),
            files_write=("/srv/work/today",),
        ),
        home=None,
    )
    assert effective.files_read == ("/srv/work/today",)
    assert effective.files_write == ("/srv/work/today",)


def test_intersect_does_not_treat_a_sibling_as_inside():
    effective = intersect(
        account(files_read=("/srv/eng",), files_write=()),
        envelope(files_read=("/srv/engine",), files_write=()),
        home=None,
    )
    assert effective.files_read == ()


def test_intersect_expands_home_before_narrowing():
    effective = intersect(
        account(files_read=("$HOME",), files_write=()),
        envelope(files_read=("/home/alice/Documents",), files_write=()),
        home="/home/alice",
    )
    assert effective.files_read == ("/home/alice/Documents",)


def test_network_is_any_only_when_both_hold_it():
    effective = intersect(
        account(network="none"),
        envelope(network="any"),
        home=None,
    )
    assert effective.network == "none"


def test_max_iterations_is_the_smaller():
    effective = intersect(
        account(max_iterations=40),
        envelope(max_iterations=80),
        home=None,
    )
    assert effective.max_iterations == 40


# -- runs ---------------------------------------------------------------------


def test_the_envelope_resolves_the_run_directory():
    resolved = envelope(files_read=("$RUN_DIR", "/srv"), files_write=("$RUN_DIR",)).resolve(
        "/var/lib/runs/2026-11-10-abc"
    )
    assert resolved.files_read == ("/var/lib/runs/2026-11-10-abc", "/srv")
    assert resolved.files_write == ("/var/lib/runs/2026-11-10-abc",)


def test_a_run_directory_is_named_by_day_and_run():
    due = datetime(2026, 11, 10, 23, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert run_dir_for("/data/runs/", due, "abc") == "/data/runs/2026-11-11-abc"


def test_inputs_take_defaults_and_refuse_what_is_not_declared():
    inputs = (
        TaskInput(name="kind", values=("sweep", "refresh"), required=True, default="sweep"),
        TaskInput(name="entity"),
    )
    assert resolve_inputs(inputs, {}) == {"kind": "sweep"}
    assert resolve_inputs(inputs, {"kind": "refresh", "entity": "model/x"}) == {
        "kind": "refresh",
        "entity": "model/x",
    }
    with pytest.raises(TaskValidationError) as error:
        resolve_inputs(inputs, {"kind": "delete", "other": "1"})
    assert any("one of" in problem for problem in error.value.problems)
    assert any("unknown input 'other'" in problem for problem in error.value.problems)


def test_a_required_input_with_nothing_to_fill_it_is_refused():
    with pytest.raises(TaskValidationError):
        resolve_inputs((TaskInput(name="entity", required=True),), {})


def test_an_input_is_a_value_in_the_command_never_syntax():
    """D96: the shell parses only what `shlex` produced."""

    env = step_environment(
        task_id="t", run_id="r", run_dir="/runs/x", inputs={"entity": "x; rm -rf ~"}
    )
    line = command_line(["pnpm", "prepare"], env)
    assert shlex.split(line) == [
        "env",
        "NEUMAN_TASK_ID=t",
        "NEUMAN_RUN_ID=r",
        "NEUMAN_RUN_DIR=/runs/x",
        "NEUMAN_INPUT_ENTITY=x; rm -rf ~",
        "pnpm",
        "prepare",
    ]


@pytest.mark.parametrize(
    ("run_if", "before", "runs"),
    [
        ("ok", ["succeeded"], True),
        ("ok", ["succeeded", "budget"], False),
        ("ok", ["failed", "skipped"], False),
        ("ok_or_budget", ["succeeded", "budget"], True),
        ("ok_or_budget", ["failed"], False),
        ("always", ["failed", "skipped"], True),
        ("always", ["cancelled"], False),
    ],
)
def test_run_if_reads_the_steps_before(run_if, before, runs):
    assert should_run(run_if, before) is runs


def test_a_run_is_as_bad_as_its_worst_step():
    assert run_status(["succeeded", "succeeded"]) == "succeeded"
    assert run_status(["succeeded", "budget", "succeeded"]) == "budget"
    assert run_status(["succeeded", "failed", "skipped"]) == "failed"
    assert run_status(["succeeded", "cancelled"]) == "cancelled"


# -- authoring validation ----------------------------------------------------


def registry() -> FakeRegistry:
    return FakeRegistry(
        [
            manifest("fs_read", needs={"read"}),
            manifest("fs_list", needs={"read"}),
            manifest("fs_write", needs={"write"}),
            manifest("web_fetch", needs={"network"}),
            manifest("shell", needs={"read", "write"}, mutating=True),
            manifest("python", needs={"read", "write"}, mutating=True),
            manifest("wipe", critical=True),
            manifest("session_query", kind="server-native"),
        ]
    )


def owner(**overrides) -> EffectivePolicy:
    tools = ("fs_read", "fs_list", "fs_write", "web_fetch", "shell", "python", "wipe")
    return account(**{"tools": tools, **overrides})


def research(**overrides) -> dict:
    body = {
        "kind": "agent",
        "name": "research",
        "instruction": "Read brief.md in your working directory and follow it.",
        "model": "DeepSeek-V4.1-Flash",
        "envelope": {
            "tools": ["fs_read", "fs_list", "fs_write", "web_fetch"],
            "files_read": ["$RUN_DIR"],
            "files_write": ["$RUN_DIR"],
            "network": "any",
            "models": ["DeepSeek-V4.1-Flash"],
            "max_iterations": 80,
        },
    }
    body.update(overrides)
    return body


def command(name: str, *argv: str, **overrides) -> dict:
    return {"kind": "run", "name": name, "argv": list(argv), "timeout_s": 600, **overrides}


def recipe(steps=None, *, workspace="/srv/site", run_root="/var/lib/runs", inputs=()):
    if steps is None:
        steps = [
            command("prepare", "pnpm", "prepare"),
            research(),
            command("check", "pnpm", "check", run_if="ok_or_budget"),
        ]
    return Recipe.of(workspace=workspace, run_root=run_root, inputs=inputs, steps=steps)


DAILY = TaskSchedule(kind="daily", time="06:15", timezone="UTC")


def check(rec, policy=None, schedule=DAILY, *, enabled=True):
    validate(
        policy or owner(),
        rec,
        schedule,
        enabled=enabled,
        registry=registry(),
        home=None,
    )


def problems(rec, policy=None, schedule=DAILY, *, enabled=True) -> str:
    with pytest.raises(TaskValidationError) as error:
        check(rec, policy, schedule, enabled=enabled)
    return "\n".join(error.value.problems)


def test_the_site_recipe_is_accepted():
    check(recipe())


def test_a_recipe_round_trips_through_its_json():
    original = recipe()
    again = Recipe.of(
        workspace=original.workspace,
        run_root=original.run_root,
        inputs=original.inputs_json(),
        steps=original.steps_json(),
    )
    assert again == original


def test_a_run_step_needs_shell_granted():
    tools = ("fs_read", "fs_list", "fs_write", "web_fetch")
    assert "'shell' is not granted" in problems(recipe(), owner(tools=tools))


def test_an_agent_step_may_not_write_the_workspace():
    """D96: the workspace is where run steps run, so a write there is a write to them."""

    rec = recipe(
        [
            research(
                envelope={**research()["envelope"], "files_write": ["$RUN_DIR", "/srv/work"]}
            ),
            command("check", "pnpm", "check"),
        ],
        workspace="/srv/work/site",
    )
    assert "overlaps the workspace" in problems(rec)


def test_an_agent_step_may_not_write_a_run_steps_program():
    rec = recipe(
        [
            research(
                envelope={**research()["envelope"], "files_write": ["$RUN_DIR", "/srv/work"]}
            ),
            command("check", "/srv/work/bin/check"),
        ]
    )
    assert "step 'check''s program" in problems(rec)


def test_an_agent_step_may_not_write_a_run_steps_working_directory():
    rec = recipe(
        [
            research(
                envelope={**research()["envelope"], "files_write": ["$RUN_DIR", "/srv/work"]}
            ),
            command("check", "pnpm", "check", cwd="/srv/work/site"),
        ]
    )
    assert "working directory" in problems(rec)


def test_the_run_root_must_be_outside_the_workspace():
    rec = recipe(workspace="/var/lib/runs/site")
    assert "overlaps the workspace" in problems(rec)


def test_an_agent_step_before_a_run_step_may_not_hold_shell():
    rec = recipe(
        [
            research(envelope={**research()["envelope"], "tools": ["fs_read", "shell"]}),
            command("check", "pnpm", "check"),
        ]
    )
    assert "'shell' declares no paths" in problems(rec)


def test_an_agent_step_with_no_run_step_after_it_may_hold_shell():
    check(
        recipe(
            [
                command("prepare", "pnpm", "prepare"),
                research(envelope={**research()["envelope"], "tools": ["fs_read", "shell"]}),
            ]
        )
    )


def test_a_program_that_env_would_misread_is_refused():
    assert "is not a program name" in problems(recipe([command("x", "-i", "sh")]))
    assert "is not a program name" in problems(recipe([command("x", "A=b", "sh")]))


def test_step_names_are_unique():
    rec = recipe([command("x", "true"), command("x", "true")])
    assert "used twice" in problems(rec)


def test_the_run_record_needs_fs_write():
    tools = ("fs_read", "fs_list", "web_fetch", "shell")
    rec = recipe([command("prepare", "pnpm", "prepare")])
    assert "'fs_write' is not granted" in problems(rec, owner(tools=tools))


def test_a_critical_tool_is_refused():
    rec = recipe([research(envelope={**research()["envelope"], "tools": ["wipe"]})])
    assert "critical" in problems(rec)


def test_a_server_native_tool_is_refused():
    policy = owner(tools=(*owner().tools, "session_query"))
    rec = recipe([research(envelope={**research()["envelope"], "tools": ["session_query"]})])
    assert "server-native" in problems(rec, policy)


def test_a_network_tool_without_envelope_network_is_refused():
    rec = recipe([research(envelope={**research()["envelope"], "network": "none"})])
    assert "'web_fetch' needs ['network']" in problems(rec)


def test_a_path_outside_reach_is_refused():
    rec = recipe(
        [research(envelope={**research()["envelope"], "files_read": ["$RUN_DIR", "/etc"]})]
    )
    assert "'/etc' is outside" in problems(rec)


def test_a_model_outside_the_envelope_is_refused():
    rec = recipe([research(model="Qwen3.8-27B-Q4")])
    assert "not in the step's own model list" in problems(rec)


def test_max_iterations_above_the_account_is_refused():
    rec = recipe([research(envelope={**research()["envelope"], "max_iterations": 500})])
    assert "max_iterations" in problems(rec)


def test_an_agent_working_directory_outside_its_read_paths_is_refused():
    rec = recipe([research(cwd="/srv/site")])
    assert "working directory" in problems(rec)


def test_an_enabled_schedule_must_fill_required_inputs():
    inputs = [{"name": "kind", "values": ["sweep", "refresh"], "required": True}]
    rec = recipe(inputs=inputs)
    assert "schedule: input 'kind' is required" in problems(rec)
    check(rec, enabled=False)
    check(
        rec,
        schedule=TaskSchedule(
            kind="daily", time="06:15", timezone="UTC", inputs=(("kind", "sweep"),)
        ),
    )


def test_within_write_respects_the_path_boundary():
    policy = account(files_write=("/srv/work",))
    assert within_write("/srv/work/today", policy, home=None)
    assert not within_write("/srv/work2", policy, home=None)
    assert not within_write("/srv", policy, home=None)
