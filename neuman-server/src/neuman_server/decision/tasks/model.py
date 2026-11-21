"""Tasks: the standing approval's arithmetic.

A task is a saved recipe: a workspace, a run root, inputs, an ordered list of
steps and a schedule. This module is the part that decides rather than stores or
draws: what a schedule's next due instant is, what a run's inputs resolve to,
whether a step runs given the ones before it, and what an agent step's envelope
means once it meets the owner's live policy.

**A task never widens reach.** An agent step's envelope is a fragment the owner
authored; `intersect` composes it with the account's policy and keeps the
narrower of each field. The run-time gate then sees only that intersection, so a
tool the owner lost, a path they can no longer reach, or a model they were
un-granted is gone from the task on the next run without a second write (D92,
D93).

**No agent step may write what a run step runs.** A run step is a command the
owner wrote; it reaches what the owner reaches, and nothing on the client could
narrow that (N146). What keeps it safe is that its program, its arguments and
its working directory are fixed, and that no agent step's write reach overlaps
them (D96). `validate` is where that is checked.

**A task has no human to ask.** `critical` tools are refused at authoring time
and again at dispatch: pre-approving a call is what a task is, and the one
thing that must never be pre-approved is the action a person would have to be
asked about anyway (D93).
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..policy.compose import EffectivePolicy
from ..policy.paths import expand_paths, within_reach
from ..policy.ports import ToolRegistry

__all__ = [
    "CATCH_UP",
    "RECORD_TOOL",
    "RUN_DIR",
    "RUN_RECORD",
    "SHELL",
    "AgentStep",
    "Recipe",
    "RunStep",
    "TaskEnvelope",
    "TaskInput",
    "TaskSchedule",
    "TaskValidationError",
    "command_line",
    "intersect",
    "resolve_inputs",
    "run_dir_for",
    "run_status",
    "should_run",
    "step_environment",
    "validate",
    "within_write",
]

# The schedule kinds the contract admits. Kept beside the arithmetic rather than
# in the schema alone: JSON Schema can say a `weekly` needs a weekday, but only
# `zoneinfo` can say whether `Europe/Zurich` is a zone this machine knows.
SCHEDULE_KINDS = ("daily", "weekly", "interval")

# The catch-up window a due run gets while its owner's client is unreachable.
# Long enough to survive a laptop sleeping through the morning, short enough
# that yesterday's sweep does not run after tomorrow's is already due.
CATCH_UP = timedelta(hours=6)

# The token an agent step's paths may name for its run's own directory, which
# does not exist when the task is authored (D97).
RUN_DIR = "$RUN_DIR"

# The run record, written into the run directory when the run ends (D97).
RUN_RECORD = "neuman-run.json"

# The tool a run step is dispatched through (D96).
SHELL = "shell"

# The tool the run directory is made and the run record written with (D97).
RECORD_TOOL = "fs_write"

# Tools that take no declared paths, so an envelope cannot say where they write.
# An agent step that holds one could rewrite a later run step's program (D96).
UNDECLARED_PATHS = ("shell", "python")


class TaskValidationError(ValueError):
    """A task the owner's own policy, registry or schedule does not allow."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True, slots=True)
class TaskEnvelope:
    """The declared reach of one agent step, before the owner's policy narrows it."""

    tools: tuple[str, ...] = ()
    files_read: tuple[str, ...] = ()
    files_write: tuple[str, ...] = ()
    network: str = "none"
    models: tuple[str, ...] = ()
    max_iterations: int = 40

    @classmethod
    def of(cls, body: Mapping) -> TaskEnvelope:
        return cls(
            tools=tuple(str(item) for item in body.get("tools") or ()),
            files_read=tuple(str(item) for item in body.get("files_read") or ()),
            files_write=tuple(str(item) for item in body.get("files_write") or ()),
            network=str(body.get("network") or "none"),
            models=tuple(str(item) for item in body.get("models") or ()),
            max_iterations=int(body.get("max_iterations") or 40),
        )

    def to_json(self) -> dict:
        return {
            "tools": list(self.tools),
            "files_read": list(self.files_read),
            "files_write": list(self.files_write),
            "network": self.network,
            "models": list(self.models),
            "max_iterations": self.max_iterations,
        }

    def resolve(self, run_dir: str) -> TaskEnvelope:
        """The envelope with `$RUN_DIR` replaced by one run's directory."""

        return replace(
            self,
            files_read=tuple(_concrete(path, run_dir) for path in self.files_read),
            files_write=tuple(_concrete(path, run_dir) for path in self.files_write),
        )


@dataclass(frozen=True, slots=True)
class TaskInput:
    """A parameter a run supplies. Strings only; a run step sees it in its environment."""

    name: str
    description: str | None = None
    values: tuple[str, ...] | None = None
    required: bool = False
    default: str | None = None

    @classmethod
    def of(cls, body: Mapping) -> TaskInput:
        values = body.get("values")
        return cls(
            name=str(body["name"]),
            description=body.get("description"),
            values=tuple(str(item) for item in values) if values is not None else None,
            required=bool(body.get("required", False)),
            default=body.get("default"),
        )

    def to_json(self) -> dict:
        out: dict = {"name": self.name, "required": self.required}
        if self.description is not None:
            out["description"] = self.description
        if self.values is not None:
            out["values"] = list(self.values)
        if self.default is not None:
            out["default"] = self.default
        return out


@dataclass(frozen=True, slots=True)
class RunStep:
    """A command the owner wrote word for word (D96)."""

    name: str
    argv: tuple[str, ...]
    timeout_s: int
    cwd: str | None = None
    run_if: str = "ok"

    kind = "run"

    def to_json(self) -> dict:
        out: dict = {
            "kind": "run",
            "name": self.name,
            "argv": list(self.argv),
            "timeout_s": self.timeout_s,
        }
        if self.cwd is not None:
            out["cwd"] = self.cwd
        if self.run_if != "ok":
            out["run_if"] = self.run_if
        return out


@dataclass(frozen=True, slots=True)
class AgentStep:
    """One harness turn inside its own envelope (D93)."""

    name: str
    instruction: str
    model: str
    envelope: TaskEnvelope
    reasoning: str | None = None
    cwd: str = RUN_DIR
    run_if: str = "ok"

    kind = "agent"

    def to_json(self) -> dict:
        out: dict = {
            "kind": "agent",
            "name": self.name,
            "instruction": self.instruction,
            "model": self.model,
            "reasoning": self.reasoning,
            "envelope": self.envelope.to_json(),
        }
        if self.cwd != RUN_DIR:
            out["cwd"] = self.cwd
        if self.run_if != "ok":
            out["run_if"] = self.run_if
        return out


Step = RunStep | AgentStep


def _step_of(body: Mapping) -> Step:
    run_if = str(body.get("run_if") or "ok")
    if body.get("kind") == "run":
        return RunStep(
            name=str(body["name"]),
            argv=tuple(str(item) for item in body["argv"]),
            timeout_s=int(body["timeout_s"]),
            cwd=body.get("cwd"),
            run_if=run_if,
        )
    return AgentStep(
        name=str(body["name"]),
        instruction=str(body["instruction"]),
        model=str(body["model"]),
        envelope=TaskEnvelope.of(body.get("envelope") or {}),
        reasoning=body.get("reasoning"),
        cwd=str(body.get("cwd") or RUN_DIR),
        run_if=run_if,
    )


@dataclass(frozen=True, slots=True)
class Recipe:
    """What a task does: where, with what inputs, in which steps."""

    workspace: str
    run_root: str
    inputs: tuple[TaskInput, ...]
    steps: tuple[Step, ...]

    @classmethod
    def of(
        cls,
        *,
        workspace: str,
        run_root: str,
        inputs: Sequence[Mapping],
        steps: Sequence[Mapping],
    ) -> Recipe:
        return cls(
            workspace=workspace,
            run_root=run_root,
            inputs=tuple(TaskInput.of(item) for item in inputs),
            steps=tuple(_step_of(item) for item in steps),
        )

    def inputs_json(self) -> list[dict]:
        return [item.to_json() for item in self.inputs]

    def steps_json(self) -> list[dict]:
        return [step.to_json() for step in self.steps]

    def cwd_of(self, step: RunStep) -> str:
        return step.cwd or self.workspace


@dataclass(frozen=True, slots=True)
class TaskSchedule:
    """When a task is due, in a zone the server owns.

    The client's clock is never consulted. A task created in Zurich keeps
    Zurich's wall time across a DST change, which is what a person means by
    "every day at 06:15" and what a UTC offset would get wrong twice a year.
    """

    kind: str
    time: str | None = None
    weekday: int | None = None
    minutes: int | None = None
    timezone: str = "UTC"
    inputs: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in SCHEDULE_KINDS:
            raise TaskValidationError([f"unknown schedule kind: {self.kind!r}"])
        if self.kind in ("daily", "weekly"):
            self._clock()
            self._zone()
        if self.kind == "weekly" and (self.weekday is None or not 0 <= self.weekday <= 6):
            raise TaskValidationError(["a weekly schedule needs a weekday from 0 to 6"])
        if self.kind == "interval" and (
            self.minutes is None or not 15 <= self.minutes <= 10080
        ):
            raise TaskValidationError(
                ["an interval schedule needs minutes between 15 and 10080"]
            )

    @classmethod
    def of(cls, body: Mapping) -> TaskSchedule:
        return cls(
            kind=str(body.get("kind") or ""),
            time=body.get("time"),
            weekday=body.get("weekday"),
            minutes=body.get("minutes"),
            timezone=str(body.get("timezone") or "UTC"),
            inputs=tuple(
                sorted((str(k), str(v)) for k, v in (body.get("inputs") or {}).items())
            ),
        )

    def to_json(self) -> dict:
        out: dict = {"kind": self.kind}
        if self.kind in ("daily", "weekly"):
            out["time"] = self.time
            out["timezone"] = self.timezone
        if self.kind == "weekly":
            out["weekday"] = self.weekday
        if self.kind == "interval":
            out["minutes"] = self.minutes
        if self.inputs:
            out["inputs"] = dict(self.inputs)
        return out

    def next_after(self, now: datetime) -> datetime:
        """The first due instant strictly after `now`, as an aware UTC datetime."""

        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        now = now.astimezone(UTC)

        if self.kind == "interval":
            return now + timedelta(minutes=int(self.minutes or 0))

        zone = self._zone()
        local = now.astimezone(zone)
        clock = self._clock()

        if self.kind == "daily":
            candidate = datetime.combine(local.date(), clock, tzinfo=zone)
            if candidate <= local:
                candidate = datetime.combine(
                    local.date() + timedelta(days=1), clock, tzinfo=zone
                )
            return candidate.astimezone(UTC)

        # weekly
        ahead = (int(self.weekday) - local.weekday()) % 7
        candidate = datetime.combine(local.date() + timedelta(days=ahead), clock, tzinfo=zone)
        if candidate <= local:
            candidate = datetime.combine(
                local.date() + timedelta(days=ahead + 7), clock, tzinfo=zone
            )
        return candidate.astimezone(UTC)

    def _clock(self) -> time:
        try:
            hour, minute = (int(part) for part in str(self.time).split(":"))
        except (TypeError, ValueError) as exc:
            raise TaskValidationError([f"schedule time must be HH:MM: {self.time!r}"]) from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise TaskValidationError([f"schedule time must be HH:MM: {self.time!r}"])
        return time(hour=hour, minute=minute)

    def _zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise TaskValidationError([f"unknown timezone: {self.timezone!r}"]) from exc


# -- runs ---------------------------------------------------------------------


def resolve_inputs(inputs: Sequence[TaskInput], given: Mapping[str, str]) -> dict[str, str]:
    """The values a run works with: what it was given, then the defaults.

    Refused rather than repaired: a value outside an input's choices, a name the
    task does not declare, or a required input with nothing to fill it would
    otherwise reach a script as something its author never wrote for.
    """

    declared = {item.name: item for item in inputs}
    problems = [f"unknown input {name!r}" for name in given if name not in declared]
    resolved: dict[str, str] = {}
    for item in inputs:
        value = given.get(item.name, item.default)
        if value is None:
            if item.required:
                problems.append(f"input {item.name!r} is required")
            continue
        value = str(value)
        if item.values is not None and value not in item.values:
            problems.append(f"input {item.name!r} must be one of {list(item.values)}")
            continue
        resolved[item.name] = value
    if problems:
        raise TaskValidationError(problems)
    return resolved


def run_dir_for(run_root: str, due_at: datetime, run_id: str) -> str:
    """`<run_root>/<due date>-<run id>`: sorted by day, unique by id (D97)."""

    day = due_at.astimezone(UTC).date().isoformat()
    return f"{_normalise(run_root)}/{day}-{run_id}"


def step_environment(
    *, task_id: str, run_id: str, run_dir: str, inputs: Mapping[str, str]
) -> dict[str, str]:
    """What a run step sees: where it is, and the run's inputs, never in its argv."""

    env = {
        "NEUMAN_TASK_ID": task_id,
        "NEUMAN_RUN_ID": run_id,
        "NEUMAN_RUN_DIR": run_dir,
    }
    for name, value in sorted(inputs.items()):
        env[f"NEUMAN_INPUT_{name.upper()}"] = value
    return env


def command_line(argv: Sequence[str], env: Mapping[str, str]) -> str:
    """The `shell` command for a run step, quoted here and nowhere else.

    `env` sets the variables and then executes the argv as given, so the shell
    the client starts parses only what `shlex` produced: an input with a quote
    or a `;` in it is a value, not syntax (D96).
    """

    assignments = [f"{name}={value}" for name, value in env.items()]
    return shlex.join(["env", *assignments, *argv])


# What a step's predecessors make of `run_if`. A cancellation ends the run.
_BLOCKS = {
    "ok": ("failed", "budget", "cancelled"),
    "ok_or_budget": ("failed", "cancelled"),
    "always": ("cancelled",),
}


def should_run(run_if: str, before: Sequence[str]) -> bool:
    """Whether a step runs, given the statuses of the steps before it."""

    blocked = _BLOCKS.get(run_if, _BLOCKS["ok"])
    return not any(status in blocked for status in before)


def run_status(statuses: Sequence[str]) -> str:
    """The run's status from its steps': the worst of them."""

    for status in ("cancelled", "failed", "budget"):
        if status in statuses:
            return status
    return "succeeded"


# -- paths --------------------------------------------------------------------


def _normalise(path: str) -> str:
    stripped = path.rstrip("/")
    return stripped or "/"


def _concrete(path: str, run_dir: str) -> str:
    return run_dir if path == RUN_DIR else path


def _inside(child: str, parent: str) -> bool:
    child = _normalise(child)
    parent = _normalise(parent)
    if parent == "/":
        return child.startswith("/")
    return child == parent or child.startswith(f"{parent}/")


def _overlap(left: str, right: str) -> str | None:
    """The narrower of two paths, or None when they do not overlap."""

    if _inside(left, right):
        return _normalise(left)
    if _inside(right, left):
        return _normalise(right)
    return None


def _intersect_paths(left: Sequence[str], right: Sequence[str]) -> tuple[str, ...]:
    """Every pairwise overlap, in the owner's order, without duplicates.

    A path list is a union; the intersection of two unions is the union of
    their pairwise intersections. `/srv` and `/srv/runs/2026` overlap at the
    latter, which is what makes a narrow task over a broad grant expressible.
    """

    seen: set[str] = set()
    out: list[str] = []
    for one in left:
        for other in right:
            shared = _overlap(one, other)
            if shared is not None and shared not in seen:
                seen.add(shared)
                out.append(shared)
    return tuple(out)


def within_write(
    candidate: str,
    policy: EffectivePolicy,
    *,
    home: str | None,
) -> bool:
    """Is `candidate` inside what policy already grants for writing?

    The write twin of `policy.paths.within_reach`, with the same normalisation
    and the same boundary test: `/srv/engine` is not inside `/srv/eng`.
    """

    target = _normalise(candidate)
    if not target.startswith("/") or ".." in target.split("/"):
        return False
    for granted in expand_paths(policy.files_write, home=home):
        if target == granted or target.startswith(f"{granted}/"):
            return True
    return False


def intersect(
    account: EffectivePolicy,
    envelope: TaskEnvelope,
    *,
    home: str | None,
) -> EffectivePolicy:
    """The policy an agent step actually executes under.

    `envelope` must already be resolved to its run's directory. Every field is
    the narrower of the account's and the envelope's. Provenance keeps the
    account's labels and adds one for the layer that narrowed, so an operator
    asking "why can this task not read X" sees the task envelope and the grant
    beneath it rather than a bare list.
    """

    provenance = dict(account.provenance)
    provenance["task"] = "task-envelope"
    return EffectivePolicy(
        tools=tuple(name for name in account.tools if name in envelope.tools),
        files_read=_intersect_paths(
            expand_paths(account.files_read, home=home),
            envelope.files_read,
        ),
        files_write=_intersect_paths(
            expand_paths(account.files_write, home=home),
            envelope.files_write,
        ),
        models=tuple(name for name in account.models if name in envelope.models),
        network=("any" if account.network == "any" and envelope.network == "any" else "none"),
        auto_accept=account.auto_accept,
        auto_accept_max=account.auto_accept_max,
        max_iterations=min(account.max_iterations, envelope.max_iterations),
        tokens_daily=account.tokens_daily,
        provenance=provenance,
    )


# -- authoring ----------------------------------------------------------------


def validate(
    account: EffectivePolicy,
    recipe: Recipe,
    schedule: TaskSchedule,
    *,
    enabled: bool,
    registry: ToolRegistry,
    home: str | None,
) -> None:
    """Refuse a task the owner's policy, the registry or D96 does not allow.

    The reach checks are enforced again at run time, by `intersect` and the
    gate. The point of the first check is the message: an authoring error names
    the field to fix, while a run-time denial would arrive after the run had
    already spent tokens discovering it. The D96 checks have no run-time twin —
    they are about the shape of the recipe, which does not change between saves.
    """

    problems: list[str] = []

    names = [step.name for step in recipe.steps]
    for name in sorted({name for name in names if names.count(name) > 1}):
        problems.append(f"step name {name!r} is used twice")
    input_names = [item.name for item in recipe.inputs]
    for name in sorted({name for name in input_names if input_names.count(name) > 1}):
        problems.append(f"input name {name!r} is used twice")
    for item in recipe.inputs:
        if (
            item.values is not None
            and item.default is not None
            and item.default not in item.values
        ):
            problems.append(f"input {item.name!r} has a default outside its values")
    if enabled:
        try:
            resolve_inputs(recipe.inputs, dict(schedule.inputs))
        except TaskValidationError as exc:
            problems.extend(f"schedule: {problem}" for problem in exc.problems)

    if not within_reach(recipe.workspace, account, home=home):
        problems.append(f"workspace {recipe.workspace!r} is outside the account's reach")
    if not within_write(recipe.run_root, account, home=home):
        problems.append(f"run root {recipe.run_root!r} is outside the account's write reach")
    if RECORD_TOOL not in account.tools:
        problems.append(
            f"{RECORD_TOOL!r} is not granted to this account; a run's directory and "
            "record are written with it"
        )
    if _overlap(recipe.run_root, recipe.workspace) is not None:
        problems.append(
            f"run root {recipe.run_root!r} overlaps the workspace; an agent step writes "
            "there, so it must be outside everything a run step runs"
        )

    # What run steps execute: their working directories and absolute programs.
    # No agent step may write any of it (D96).
    protected: list[tuple[str, str]] = [("the workspace", recipe.workspace)]
    for index, step in enumerate(recipe.steps):
        if isinstance(step, RunStep):
            problems.extend(_run_step_problems(step, recipe, account, registry, home))
            protected.append((f"step {step.name!r}'s working directory", recipe.cwd_of(step)))
            if step.argv and step.argv[0].startswith("/"):
                protected.append((f"step {step.name!r}'s program", step.argv[0]))
        else:
            later_run = any(isinstance(other, RunStep) for other in recipe.steps[index + 1 :])
            problems.extend(
                _agent_step_problems(step, recipe, account, registry, home, later_run=later_run)
            )

    for label, path in protected[1:]:
        if _overlap(recipe.run_root, path) is not None:
            problems.append(f"run root {recipe.run_root!r} overlaps {label}")
    for step in recipe.steps:
        if not isinstance(step, AgentStep):
            continue
        for entry in step.envelope.files_write:
            if entry == RUN_DIR:
                continue
            for label, path in protected:
                if _overlap(entry, path) is not None:
                    problems.append(
                        f"step {step.name!r} may write {entry!r}, which overlaps {label}; "
                        "an agent step must not be able to write what a run step runs"
                    )

    if problems:
        raise TaskValidationError(problems)


def _run_step_problems(
    step: RunStep,
    recipe: Recipe,
    account: EffectivePolicy,
    registry: ToolRegistry,
    home: str | None,
) -> list[str]:
    problems: list[str] = []
    label = f"step {step.name!r}"
    if SHELL not in account.tools:
        problems.append(f"{label} runs a command, and {SHELL!r} is not granted to this account")
    manifest = registry.get(SHELL)
    if manifest is None or manifest.kind != "client":
        problems.append(f"{label} runs a command, and this appliance has no client {SHELL!r}")
    elif manifest.critical:
        problems.append(f"{label} runs a command, and {SHELL!r} is critical here")
    program = step.argv[0] if step.argv else ""
    # `env` reads a leading `NAME=value` as an assignment and a leading `-` as
    # its own option, so either would change what runs rather than be run.
    if not program or program.startswith("-") or "=" in program:
        problems.append(f"{label}'s program {program!r} is not a program name or path")
    cwd = recipe.cwd_of(step)
    if not within_reach(cwd, account, home=home):
        problems.append(f"{label}'s working directory {cwd!r} is outside the account's reach")
    return problems


def _agent_step_problems(
    step: AgentStep,
    recipe: Recipe,
    account: EffectivePolicy,
    registry: ToolRegistry,
    home: str | None,
    *,
    later_run: bool,
) -> list[str]:
    problems: list[str] = []
    label = f"step {step.name!r}"
    # Checked as the run root, which every run directory is inside.
    envelope = step.envelope.resolve(recipe.run_root)

    # The envelope's own reach, not only the account's. A step that grants
    # `web_fetch` but leaves `network` at `none`, or `fs_write` with no write
    # path, would be accepted against the account and then denied on the first
    # dispatch — after the run had already spent tokens discovering it.
    envelope_reach: set[str] = set()
    if envelope.files_read:
        envelope_reach.add("read")
    if envelope.files_write:
        envelope_reach.add("write")
    if envelope.network == "any":
        envelope_reach.add("network")

    for name in envelope.tools:
        if name not in account.tools:
            problems.append(f"{label}: {name!r} is not granted to this account")
            continue
        manifest = registry.get(name)
        if manifest is None:
            problems.append(f"{label}: {name!r} has no manifest on this appliance")
            continue
        if manifest.kind != "client":
            problems.append(
                f"{label}: {name!r} is server-native; a task can only use tools that "
                "run on its client"
            )
            continue
        if manifest.critical:
            problems.append(
                f"{label}: {name!r} is critical; a task has no one to ask, so it cannot "
                "be pre-approved"
            )
            continue
        if later_run and name in UNDECLARED_PATHS:
            problems.append(
                f"{label}: {name!r} declares no paths, and a run step follows; it could "
                "rewrite what that step runs"
            )
            continue
        missing = manifest.needs - envelope_reach
        if missing:
            problems.append(
                f"{label}: {name!r} needs {sorted(missing)}; the envelope does not grant it"
            )

    if envelope.network == "any" and account.network != "any":
        problems.append(f"{label}: the account does not hold network reach")
    if envelope.network not in ("none", "any"):
        problems.append(f"{label}: unknown network value: {envelope.network!r}")

    for name in envelope.models:
        if name not in account.models:
            problems.append(f"{label}: model {name!r} is not granted to this account")
    if step.model not in envelope.models:
        problems.append(f"{label}: model {step.model!r} is not in the step's own model list")
    if step.model not in account.models:
        problems.append(f"{label}: model {step.model!r} is not granted to this account")

    for path in envelope.files_read:
        if not within_reach(path, account, home=home):
            problems.append(f"{label}: read path {path!r} is outside the account's reach")
    for path in envelope.files_write:
        if not within_write(path, account, home=home):
            problems.append(f"{label}: write path {path!r} is outside the account's reach")

    cwd = _concrete(step.cwd, recipe.run_root)
    if not any(_inside(cwd, path) for path in envelope.files_read):
        # The turn would start somewhere its first `fs_read` is denied.
        problems.append(f"{label}: working directory {step.cwd!r} is not inside its read paths")

    if envelope.max_iterations > account.max_iterations:
        problems.append(
            f"{label}: max_iterations {envelope.max_iterations} exceeds the account's "
            f"limit of {account.max_iterations}"
        )
    return problems
