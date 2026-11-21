"""The agent loop: model call → gate → dispatch → observe.

Every boundary is logged, and model history is re-derived from the log each step
(`projection.py`), so live policy changes and compaction are always honored
without the loop holding state that could drift.

Structure, and why:

* **Policy is re-read every step**, not once per turn and never per session. A
  revocation lands on the next tool call (docs/security-model.md).
* **The inbox is drained at every safe point** — between steps, between chunks,
  and while awaiting a dispatch (docs/decisions.md D12). Steering folds into the
  conversation; an interrupt cancels the in-flight dispatch through the
  transport's `cancel` — v1 never sent it).
* **Dispatch is chosen by manifest `kind`** (docs/decisions.md D13), not by a hardcoded name
  list: `server-native` is intercepted here, everything else goes to the client.
  v1 special-cased tool names inline (gotchas N3).
* **Everything fails closed.** No approver, no verdict, no budget, an unknown
  tool, a malformed argument object — each ends as a denial with a logged
  reason, never as an unguarded dispatch.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ...foundation.logging import get_logger
from ..audit import chain
from ..audit.ports import AuditRepository
from ..policy import gate
from ..policy.compose import EffectivePolicy
from ..policy.paths import DEFAULT_HOME_DATA_DIRS, expand_paths
from ..policy.ports import ToolRegistry
from . import events, projection
from .context import ContextManager, estimate_tokens, measured_tokens
from .inbox import Inbox
from .jobs import JOB_KILL, JOB_OUTPUT, detached_note, refused_note
from .ports import (
    Chunk,
    EventRecord,
    EventRepository,
    ImageRepository,
    ModelGateway,
    ModelTarget,
    ToolExecution,
    UsageRepository,
)
from .pricing import TurnCost, turn_cost
from .prompt import DEFAULT_NAME, build_request, selected, wrap_untrusted
from .reasoning import UnknownLevel, reasoning_fields, resolve_level
from .spill import persisted_note

__all__ = ["Harness", "HarnessConfig", "TurnResult"]

_log = get_logger("neuman.server.harness")

DEFAULT_MAX_TOKENS = 4096

# A project's standing instructions, in the two names already in use. Checked
# in order; the first that exists is the one read.
PROJECT_CONTEXT_FILES = ("AGENTS.md", "CLAUDE.md")

# A ceiling rather than the whole file. These are read into every request of
# the turn, so an unbounded one spends the window the conversation needs — and
# a file this long has stopped being conventions and become documentation,
# which the model can read with `fs_read` when it is relevant.
PROJECT_CONTEXT_MAX_CHARS = 8192

# The same ceiling expressed as what `fs_read` takes, so the bound is applied on
# the machine holding the file rather than after the whole of it has crossed the
# socket. Lines, not bytes, because that is the tool's unit; 400 of them at the
# ~20-character average of a conventions file lands near the character ceiling,
# which still trims the result as the authority.
PROJECT_CONTEXT_MAX_LINES = 400

# The tool that does the reading. Named here rather than taken from anywhere
# dynamic, for `/api/browse`'s reason: this dispatch skips the gate, so the one
# thing it must not become is a way to run an arbitrary tool without a verdict.
_CONTEXT_TOOL = "fs_read"

# `fs_read` ends a partial page with this, which is a message to a *model* about
# how to continue paging. Here there is no paging — the ceiling is the whole
# policy — so the marker is replaced with a sentence saying the file was cut.
# Left in place it would invite the model to call `fs_read` with an offset to
# retrieve conventions the prompt deliberately bounded.
_CONTEXT_TRUNCATION = "[showing lines "

# Turn end reasons. Stable strings: they reach the client and the audit chain.
STOP = "stop"
MAX_ITERATIONS = "max_iterations"
BUDGET = "budget_exhausted"
INTERRUPTED = "interrupted"
MODEL_ERROR = "model_error"
# A scheduled task runs under a standing approval rather than a per-call one.
# These two are the only places that approval is named in the log: a call the
# envelope admitted, and a critical call the envelope may never admit.
TASK_ENVELOPE = "task_envelope"
CRITICAL_NOT_PRE_APPROVED = "critical_not_pre_approved"
# A reasoning level the model does not declare. Its own code rather than a
# generic failure: the remedy is for the *client* to offer a level the
# catalog lists, which a message naming the model cannot convey on its own.
REASONING_ERROR = "reasoning_error"
# A tool the user switched off for this conversation (D50). Distinct from the
# gate's `tool_not_granted`, which names a policy fact: this one is a UI
# checkbox and the remedy belongs to the user, not an operator. Stable — it
# reaches the client and the audit chain.
NOT_SELECTED = "tool_not_selected"
TRUNCATED = "truncated"
OVERFLOW = "context_overflow"

# How many times one turn may compact and retry after the provider refuses the
# prompt. More than one means the first compaction did not free enough, and a
# second rarely will — the honest answer at that point is that the window is too
# small for the conversation, said plainly rather than looped on.
MAX_OVERFLOW_RETRIES = 2

# How many times one turn may retry a continuation that came back empty.
#
# One. A continuation answering a truncation with no content, no tool call and
# `stop` has not finished the thought — it has abandoned it — and the turn that
# found this ended on a two-token reply after 16,384 tokens of reasoning,
# reporting `stop` with nothing rendered (N139). A single retry covers the
# model that needed its thought handed back properly; a second would be asking
# the same question twice and calling the silence an answer.
MAX_EMPTY_CONTINUATIONS = 1

# Held back from the reply when sizing it against the window.
#
# The history figure the clamp subtracts is partly estimated — the tail since
# the last measurement — so the reply must not be sized to the last token of
# what is left. `pi` reserves the same 4096 for the same reason
# (`packages/ai/src/api/simple-options.ts`).
CONTEXT_SAFETY_TOKENS = 4096

# The least a reply may be clamped to before the window is simply too full.
#
# A floor rather than zero: a one-token reply is not a reply, and a request for
# it would come back as a length stop immediately. Reaching this means
# compaction has not freed enough, which the overflow path then reports.
MIN_REPLY_TOKENS = 512

# Provider messages that mean "the prompt did not fit", across the endpoints an
# OpenAI-compatible client meets. Taken from `pi`
# (`packages/ai/src/utils/overflow.ts`), which maintains this list against ~25
# providers; the llama.cpp entry is the one this appliance produces.
#
# Matched on text because there is no structured signal: the OpenAI error shape
# has a `code`, and overflow arrives as a 400 or 500 with a human sentence.
# Narrow patterns rather than a bare "context" substring, because misreading a
# rate limit as overflow would compact history for no reason.
_OVERFLOW_PATTERNS = (
    re.compile(r"exceeds the available context size", re.I),  # llama.cpp
    re.compile(r"prompt (?:is )?too long", re.I),  # Anthropic, z.ai
    re.compile(r"exceeds the context window", re.I),  # OpenAI
    re.compile(r"maximum context length", re.I),  # OpenAI-compatible, OpenRouter
    re.compile(r"input token count.*exceeds the maximum", re.I),  # Gemini
    re.compile(r"maximum prompt length is \d+", re.I),  # xAI
    re.compile(r"reduce the length of the messages", re.I),  # Groq
    re.compile(r"greater than the context length", re.I),  # LM Studio
    re.compile(r"context[_ ]length[_ ]exceeded", re.I),
    re.compile(r"too many tokens", re.I),
    re.compile(r"token limit exceeded", re.I),
)

# Checked first: a throttling message can contain the words an overflow pattern
# looks for, and compacting in response to a rate limit would discard history to
# fix a problem that waiting solves.
_NOT_OVERFLOW_PATTERNS = (
    re.compile(r"rate limit", re.I),
    re.compile(r"too many requests", re.I),
    re.compile(r"throttl", re.I),
)


def is_context_overflow(error: str) -> bool:
    """Whether a provider error means the prompt was too large to accept."""

    if any(pattern.search(error) for pattern in _NOT_OVERFLOW_PATTERNS):
        return False
    return any(pattern.search(error) for pattern in _OVERFLOW_PATTERNS)


def _describe(exc: BaseException) -> str:
    """The exception named as well as messaged, for the strings a person reads.

    `str(exc)` is empty for the transport failures a model call actually fails
    with: httpx wraps a bare `anyio.EndOfStream` or `TimeoutError`, neither of
    which carries text, so a `model_error` reported from the message alone said
    only that something, somewhere, went wrong (N162). The class name is the
    one fact that survives every such wrapper.
    """

    message = str(exc)
    name = type(exc).__name__
    return f"{name}: {message}" if message else name


# The provider's word for "I ran out of room", in the OpenAI-compatible shape
# every endpoint here speaks. Confirmed against this appliance's own llama.cpp:
# a capped generation returns `finish_reason: "length"` and stops mid-token.
FINISH_LENGTH = "length"

# When to tell the model its turn is nearly over, as a count of steps left.
#
# `max_iterations` ends a turn silently: the model is mid-thought, the loop
# stops, and the user gets `max_iterations` with no account of what was learned
# (N87). The limit is right — it is the only bound on a runaway turn when
# `tokens_daily` is unset — but a hard cut wastes everything the turn found.
#
# So a few steps out, the model is told. Three because it needs enough room to
# actually write the summary: one step is not a handoff, it is a cut with a
# warning. A turn whose whole budget is smaller than this gets the note at its
# halfway point instead, so a tight limit still produces a handoff rather than
# nothing.
HANDOFF_STEPS_REMAINING = 3

# What the model is told. Phrased as an instruction rather than a status line,
# because a model that reads "you have 3 steps left" and keeps debugging has
# learned nothing useful. It names the two things that make the next turn
# cheaper: what is known, and what to try.
HANDOFF_NOTE = (
    "You have {remaining} step{plural} left in this turn before it is cut off. "
    "Stop investigating now and reply with: what you have established so far, "
    "and the single next thing you would try. Do not start new work — this "
    "summary is what the next turn will continue from, so anything you leave "
    "out is lost."
)


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    # What an entry naming `$HOME` expands to, for the reach the model is told
    # about and for the working-directory check. Not a grant: the client runs
    # as the human and there is no profile to build. The live value is the
    # appliance's row, passed in when a turn is built (D104).
    home_data_dirs: tuple[str, ...] = DEFAULT_HOME_DATA_DIRS
    # How long a model may stay silent before the client is told it is waiting.
    # Long enough that a responsive provider never triggers it, short enough
    # that a cold VRAM load does not look like a hang.
    waiting_after_s: float = 2.0
    tool_wait_ms: int = 600_000
    approval_timeout_s: float = 300.0
    max_tokens: int = DEFAULT_MAX_TOKENS
    # The operator's own system-prompt text, from the settings row. Held as a
    # string rather than read per turn because the loop has no database handle
    # and composition has already loaded it at startup; a write from the
    # administration page replaces it in place. Empty means the model sees the
    # named identity and then the computed reach, with nothing between them.
    operator_prompt: str = ""
    # What the appliance calls itself in the identity sentence (D82). Read from
    # the settings row at startup and replaced in memory on a rename, for the
    # same reason as `operator_prompt`: the loop builds a prompt per turn and
    # must not touch the database to do it.
    agent_name: str = DEFAULT_NAME
    # Which build is serving. Recorded once per turn so a later diagnosis can
    # tell a behaviour change from a mistaken earlier report. Empty means the
    # stamp is not written at all, which is the pre-existing behaviour.
    build_id: str = ""


# The tools that can describe a write before making it. Both accept `preview`,
# and both resolve it through the same `edit_plan.plan()` that performs the
# write — which is the only reason the diff in an approval frame is worth
# trusting (N35, N38: a preview derived separately is a second opinion, not a
# description).
PREVIEW_TOOLS = ("fs_write", "fs_edit")

# The key those tools put on the last line of stdout. Lifted out here so the
# model's `tool` message carries one sentence instead of a patch it already
# knows the contents of.
DIFF_SENTINEL = "neuman_diff"


def _parse_image(stdout: str) -> dict[str, str] | None:
    """Split an image tool's `media_type base64data` line.

    Returns None rather than raising on anything unexpected: a tool that produced
    garbage is a tool failure, reported as such, not a reason to end the turn with
    a traceback.
    """

    line = (stdout or "").strip()
    media, _, data = line.partition(" ")
    if not data or not media.startswith("image/"):
        return None
    # Whitespace would have to be stripped by the provider anyway, and a data:
    # URI cannot contain a newline.
    return {"media_type": media, "data": "".join(data.split())}


def _decode_image(image: dict[str, str] | None) -> bytes | None:
    """The bytes behind `_parse_image`'s base64, or None when it is not base64.

    The tool wrote the bytes and encoded them once for the transport; the store
    keeps them decoded, because base64 is a wire encoding and not the picture.
    None rather than raising, for `_parse_image`'s reason — a tool that produced
    garbage is a tool failure to report, not a reason to end the turn with a
    traceback.
    """

    if image is None:
        return None
    try:
        return base64.b64decode(image["data"], validate=True)
    except (KeyError, ValueError, TypeError):
        return None


def _split_diff(stdout: str) -> tuple[str, dict[str, Any] | None]:
    """Lift a trailing `{"neuman_diff": {...}}` line out of a tool's stdout.

    Returns the text the model should see and the structured half for the human.
    A tool whose last line is not the sentinel is left alone, so this is safe to
    run over any dispatch's output: the sentinel is the signal, not the tool's
    name alone.

    Failing soft on malformed JSON rather than raising, for `_parse_image`'s
    reason — a tool that produced garbage is a tool failure to report, not a
    reason to end the turn with a traceback.
    """

    text = stdout or ""
    lines = text.rstrip("\n").split("\n")
    if not lines:
        return text, None

    try:
        candidate = json.loads(lines[-1])
    except (json.JSONDecodeError, ValueError):
        return text, None
    if not isinstance(candidate, dict) or DIFF_SENTINEL not in candidate:
        return text, None

    payload = candidate[DIFF_SENTINEL]
    if not isinstance(payload, dict):
        return text, None
    return "\n".join(lines[:-1]).strip(), payload


async def _read_project_context(
    execution: ToolExecution,
    registry: ToolRegistry,
    *,
    user: str,
    working_dir: str | None,
    surfaces: dict[str, list[str]],
    wait_ms: int,
) -> str:
    """The working directory's standing instructions, if it states any.

    `AGENTS.md` and `CLAUDE.md` both, because a user with either should not
    have to write a third. The first that exists wins — a repository carrying
    both means them to say the same thing, and concatenating would send a model
    two copies of one convention.

    **A dispatch, not an `open()`** (N152). This read used to be
    `Path(working_dir).read_text()`, which is a read on the *server's*
    filesystem — and the file is on the acting human's machine. On a one-box
    appliance the two coincide and it worked; with `programs.neuman-client` on a
    separate workstation the path does not exist where the read happened, so
    every project's conventions were silently absent from every prompt and
    nothing said so. A home directory is `0700` and this process is `neuman`, so
    even co-located it only ever worked because the appliance happened to be the
    workstation.

    Not a grant: `working_dir` is already checked against policy, and the
    surfaces passed here are the account's own expanded read paths — the same
    ones the environment probe below is given. A directory with no such file is
    the ordinary case and produces nothing.

    Outside the harness proper, like `/api/browse` and the probe: nothing was
    proposed by a model, so there is no verdict to seek. A card per turn asking
    to read the conventions file the user already chose to work beside would be
    the chooser's mistake in a new place.
    """

    if not working_dir:
        return ""
    if registry.get(_CONTEXT_TOOL) is None:
        return ""
    available = execution.available(user)
    if available is not None and _CONTEXT_TOOL not in available:
        # A client that cannot run the reader. Dispatching anyway is a round
        # trip that ends in `unknown_tool`, which the handshake exists to avoid.
        return ""

    manifest = registry.get(_CONTEXT_TOOL)
    assert manifest is not None

    for name in PROJECT_CONTEXT_FILES:
        path = str(Path(working_dir) / name)
        try:
            outcome = await execution.dispatch(
                user=user,
                dispatch_id=uuid.uuid4().hex,
                tool=_CONTEXT_TOOL,
                args={"path": path, "limit": PROJECT_CONTEXT_MAX_LINES},
                wait_ms=min(manifest.wait_ms, wait_ms),
                surfaces=surfaces,
            )
        except asyncio.CancelledError:
            # The turn was stopped from outside; `run_turn`'s wrapper owes the
            # client a terminal frame and eating this would leave a session that
            # looks in-flight forever (N104).
            raise
        except Exception:  # noqa: BLE001 - conventions are additive to a turn
            # Logged rather than swallowed, which is the half N152 was missing.
            # The old bug was not the wrong path alone — it was that a project's
            # conventions could vanish from every prompt with nothing anywhere
            # saying so. A fix that fails silently in a new way keeps the
            # symptom that made the original expensive to notice.
            _log.warning(
                "could not read project context",
                fields={"user": user, "path": path},
            )
            return ""

        if not outcome.ok:
            # The ordinary case: no such file. `fs_read` exits non-zero for a
            # missing path, a directory, a non-UTF-8 file — none of which is
            # worth a line in the log, because most directories have neither
            # file and that is not a failure.
            continue

        text = (outcome.stdout or "").strip()
        if text:
            return _bound_project_context(text)

    return ""


def _bound_project_context(text: str) -> str:
    """Trim to the ceiling and say so, rather than ending mid-sentence.

    The line limit is applied by the tool, on the machine with the file, so this
    is the backstop for a file whose 400 lines are still long — and the place
    `fs_read`'s paging notice is turned into a statement. That notice tells a
    model how to fetch the rest; here there is no rest to fetch, because the
    ceiling is a decision about the window rather than a page boundary.
    """

    marker = text.rfind(_CONTEXT_TRUNCATION)
    cut = marker != -1
    if cut:
        text = text[:marker].rstrip()

    if len(text) > PROJECT_CONTEXT_MAX_CHARS:
        text = text[:PROJECT_CONTEXT_MAX_CHARS].rstrip()
        cut = True

    if cut:
        # Said in the prompt, because a model following half a conventions file
        # should know it is half. Without this the text simply stops, which
        # reads as a complete document that happens to end abruptly.
        text += "\n\n[This file was truncated; read it with `fs_read` if you need the rest.]"
    return text


# The probe's name, and the states worth a sentence in the prompt. `ok` and
# `no_environment` are the two ordinary answers and neither needs advice: one
# resolved, and most directories are not projects (N85). The rest are a project
# whose environment a human has to rebuild, which is the case where the model
# would otherwise discover the problem as a `ModuleNotFoundError` twenty lines
# into a script.
_ENV_TOOL = "env_probe"

_ENV_ADVICE = {
    "env_unrealised": (
        "declares a development environment that has not been built, so the "
        "project's own packages are not available"
    ),
    "env_stale": (
        "has a development environment older than the flake defining it, so its "
        "packages may not be the ones the project declares"
    ),
    "env_collected": (
        "has a development environment whose store paths have been "
        "garbage-collected, so its packages are not on disk"
    ),
    "env_untrusted": (
        "has a development-environment cache that nix did not write, which Neuman will not use"
    ),
}


async def _probe_environment(
    execution: ToolExecution,
    registry: ToolRegistry,
    *,
    user: str,
    working_dir: str | None,
    surfaces: dict[str, list[str]],
    wait_ms: int,
) -> str:
    """One sentence about the working directory's development environment.

    Why this is dispatched rather than resolved here: the `.direnv` cache is in
    the acting user's home, and this process is not that user. A server-side
    read would also answer about the wrong machine whenever the client runs on
    another host — which is exactly what `_read_project_context` above did until
    N152, and the reason both reads are dispatches now.

    Why it is told to the model at all, given `python(action="describe")` exists:
    N84's lesson. `working_dir` reached the system prompt and stopped, and the
    model was told where it was while every dispatch started somewhere else. The
    chat window is about to colour its working-directory control from this same
    probe, so a model that had to *ask* would be the one party in the room who
    could not see what the human was looking at.

    Every failure here is silence. This is a sentence in a prompt, and a turn
    that cannot run because a probe timed out would trade a whole conversation
    for a nicety.
    """

    if not working_dir:
        return ""
    if registry.get(_ENV_TOOL) is None:
        return ""
    available = execution.available(user)
    if available is None or _ENV_TOOL not in available:
        return ""

    manifest = registry.get(_ENV_TOOL)
    assert manifest is not None
    try:
        outcome = await execution.dispatch(
            user=user,
            dispatch_id=uuid.uuid4().hex,
            tool=_ENV_TOOL,
            args={"cwd": working_dir},
            wait_ms=min(manifest.wait_ms, wait_ms),
            surfaces=surfaces,
        )
    except asyncio.CancelledError:
        # Not swallowed. The turn was stopped from outside and the wrapper in
        # `run_turn` owes the client a terminal frame; eating this here would
        # leave a session that looks in-flight forever (N104).
        raise
    except Exception:  # noqa: BLE001 - a status line must never end a turn
        # Deliberately broad. The probe is additive, and every reason this can
        # raise — no client, a closed channel, a timeout, malformed output — is
        # a reason to say nothing about the environment rather than to lose the
        # conversation over a sentence in the prompt.
        return ""

    if not outcome.ok:
        return ""

    try:
        report = json.loads(outcome.stdout)
    except (ValueError, TypeError):
        return ""
    if not isinstance(report, dict):
        return ""

    reason = report.get("reason")
    if reason == "ok":
        interpreter = report.get("interpreter")
        if isinstance(interpreter, str) and interpreter:
            # Naming the interpreter is what makes a later import error
            # answerable: the reader can see *which* Python the directory means.
            return (
                f"It has a realised development environment; its Python is {interpreter}. "
                "`python` and `shell` run inside it."
            )
        return (
            "It has a realised development environment, which `python` and `shell` run inside."
        )

    advice = _ENV_ADVICE.get(reason if isinstance(reason, str) else "")
    if advice:
        return (
            f"Note: the working directory {advice}. Ask the user to re-enter the "
            "directory so direnv can rebuild it, rather than working around it."
        )

    # `no_environment` and anything unrecognised. Saying "this is not a project"
    # would be noise in the overwhelmingly common case, and N85 is the record of
    # what treating it as a problem costs.
    return ""


def _tokens_of(usage: dict[str, Any] | None) -> int:
    """One request's tokens, as the provider counted them."""

    if not usage:
        return 0
    total = usage.get("total_tokens")
    if isinstance(total, int):
        return total
    return sum(
        value
        for value in (usage.get("prompt_tokens"), usage.get("completion_tokens"))
        if isinstance(value, int)
    )


@dataclass(frozen=True, slots=True)
class TurnResult:
    branch_id: str
    steps: int
    reason: str
    # What this turn spent, for a caller that records it per turn rather than
    # per session: a task step (D97). `cost_usd` is None when no step was
    # priced, for the reason `TurnCost.usd` is.
    cost_usd: float | None = None
    tokens: int = 0


@dataclass(frozen=True, slots=True)
class Actor:
    """Who the turn runs for."""

    account_id: str
    name: str
    # The connected client's home, which a `$HOME` grant expands under; None
    # when no client is connected, and then such a grant reaches nothing.
    home: str | None


@dataclass(slots=True)
class _SessionCost:
    """A session's running spend.

    `estimated` latches: once any step of the conversation has been priced by
    arithmetic rather than billed, the total is an estimate and saying
    otherwise would overstate the whole figure on the strength of its exact
    parts.
    """

    usd: float = 0.0
    estimated: bool = False

    def add(self, cost: TurnCost) -> None:
        if cost.usd is None:
            return
        self.usd += cost.usd
        self.estimated = self.estimated or cost.estimated


@dataclass(slots=True)
class _StreamOutcome:
    """The assembled result of one streamed model call."""

    message: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""
    failure: str | None = None


# A server-native handler: given the actor and args, produce content for the
# model. Registered by composition, so the harness needs no knowledge of what
# reading "your own sessions" means.
ServerNativeHandler = Callable[[Actor, dict[str, Any], str], Awaitable[str]]


@dataclass
class Harness:
    """One agent loop, shared by every session."""

    gateway: ModelGateway
    execution: ToolExecution
    registry: ToolRegistry
    audit_repository: AuditRepository
    event_repository: EventRepository
    usage: UsageRepository
    # The one copy of every image's bytes, keyed by content. A `tool_result`
    # records the hash and the request builder fetches it, so the base64 is
    # neither stored per snapshot nor measured per turn. Required rather than
    # optional: a harness with nowhere to put a picture should fail to build,
    # not silently drop one at the moment an image tool runs.
    images: ImageRepository
    policy_for: Callable[[str], Awaitable[EffectivePolicy]]
    # Resolves a *granted local name* to what the provider is actually sent.
    # Injected for the same reason `policy_for` is: the catalog is a table, and
    # the decision layer does not touch tables (D7). Returning None means the
    # name is not in the catalog, which is a refusal rather than a pass-through
    # — a name a user may select but that resolves to nothing is a
    # misconfiguration, and forwarding it produces a provider error blaming the
    # user's choice (N48).
    resolve_model: Callable[[str], Awaitable[ModelTarget | None]] | None = None
    config: HarnessConfig = field(default_factory=HarnessConfig)
    context: ContextManager = field(default_factory=ContextManager)
    server_native: dict[str, ServerNativeHandler] = field(default_factory=dict)
    emit: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    audit_mirror: Any = None
    secrets: dict[str, str] = field(default_factory=dict)

    # What a session has spent so far, in USD, and whether any part of that
    # total was computed by us rather than billed by a provider. Keyed by
    # session because one `Harness` serves every session — a bare float here
    # would pool two users' spend into one figure.
    #
    # Per session rather than per day: it sits beside the context bar, which
    # describes this conversation, and a daily figure next to a per-session bar
    # reads as describing the same scope when it does not. The daily token
    # budget in `UsageRepository` is the per-day accounting and stays separate.
    #
    # In memory, so it resets when the server restarts. The alternative is a
    # cost column on the usage ledger, which is the right home for a figure
    # that must survive a restart — not built, because the number is a running
    # read-out of a live conversation rather than a record anything bills on.
    _branch_cost: dict[str, _SessionCost] = field(default_factory=dict, init=False, repr=False)

    # Whether this turn is a task run: every call the gate admits is
    # pre-approved by the task's envelope, and a critical call is refused
    # rather than put to an inbox nobody is watching. Set per turn in
    # `_run_turn`; a Harness is built per turn, so there is no cross-turn leak.
    _pre_approved: bool = field(default=False, init=False, repr=False)
    # This turn's own spend, beside the session's running total above. Reset in
    # `_run_turn`, read by `_finish` into the `TurnResult`.
    _turn_cost: _SessionCost | None = field(default=None, init=False, repr=False)
    _turn_tokens: int = field(default=0, init=False, repr=False)

    async def run_turn(
        self,
        *,
        branch_id: str,
        actor: Actor,
        message: str,
        model: str,
        inbox: Inbox,
        session_id: str | None = None,
        working_dir: str | None = None,
        attachments: list[str] | None = None,
        account_prompt: str = "",
        reasoning: str | None = None,
        approval: str | None = None,
        tool_selection: tuple[str, ...] | None = None,
        pre_approved: bool = False,
    ) -> TurnResult:
        """Run one user turn to completion, or until it is stopped.

        `tool_selection` is the user's per-session narrowing of which granted
        tools to advertise, or None for all of them. It changes advertisement
        only — the gate below still re-checks every proposal against live
        policy, so this cannot widen anything and a deselected tool is denied
        exactly like an ungranted one (D50).

        `reasoning` is the level the user picked for this turn, or None for the
        model's declared default. Per-turn rather than per-model because it is
        a user's choice about one question — the same question asked twice in
        one session can deserve different answers — which is the same category
        as `auto_accept` and a different one from the window or the costs.

        `account_prompt` is the signed-in user's own standing instructions, read
        from their account row at turn start (D65). Passed in rather than read
        here because the harness does not touch tables (D7), and read per turn
        rather than held on the session because it belongs to the account: an
        edit in Settings has to reach the next message, the way a policy change
        does.

        The body is wrapped so that *every* exit emits a terminal frame. The
        client renders a turn as in-flight until it sees `done`, so an
        exception escaping the loop does not surface as an error — it surfaces
        as an app that never finishes (N104). The wrapper is the guarantee the
        client's own state machine depends on; the handlers inside the loop are
        what make the common cases legible.
        """

        try:
            return await self._run_turn(
                branch_id=branch_id,
                actor=actor,
                message=message,
                model=model,
                inbox=inbox,
                session_id=session_id,
                working_dir=working_dir,
                attachments=attachments,
                account_prompt=account_prompt,
                reasoning=reasoning,
                approval=approval,
                tool_selection=tool_selection,
                pre_approved=pre_approved,
            )
        except asyncio.CancelledError:
            # The turn was stopped from outside. It still has to *look* stopped.
            await self._finish(branch_id, 0, INTERRUPTED)
            raise
        except BaseException as exc:  # noqa: BLE001 - the terminal frame is the point
            _log.exception(
                "turn failed",
                fields={"branch": branch_id, "error": _describe(exc)},
            )
            await self._log(
                branch_id,
                "harness_event",
                {
                    "type": MODEL_ERROR,
                    # The message verbatim, and the class beside it: one is the
                    # provider's words when it had any, the other is always
                    # there.
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            # `code` is required by the error frame's schema; without it the
            # client renders `undefined: …` for the failures this catch-all is
            # the only report of.
            await self._emit(
                {
                    "type": "error",
                    "code": MODEL_ERROR,
                    "message": f"the turn failed: {_describe(exc)}",
                }
            )
            # `MODEL_ERROR` rather than a new reason: the strings here reach the
            # client and the audit chain, and a reason neither has seen would be
            # rendered as a bare unknown word. This is the generic-failure code
            # the client already explains.
            return await self._finish(branch_id, 0, MODEL_ERROR)

    async def _run_turn(
        self,
        *,
        branch_id: str,
        actor: Actor,
        message: str,
        model: str,
        inbox: Inbox,
        session_id: str | None = None,
        working_dir: str | None = None,
        attachments: list[str] | None = None,
        account_prompt: str = "",
        reasoning: str | None = None,
        approval: str | None = None,
        tool_selection: tuple[str, ...] | None = None,
        pre_approved: bool = False,
    ) -> TurnResult:
        # A task run's standing approval. Set before the first event so a model
        # call that fails immediately still reports under the right regime.
        self._pre_approved = pre_approved

        # Which build served this turn, recorded before anything else happens.
        #
        # Added because a diagnosis spanning a rebuild has no way to know it. Asked
        # to explain why an earlier session could not read event payloads, Neuman
        # read the *current* `session_query` handler, saw that it returns them,
        # and concluded the earlier report had been "confidently wrong" — when in
        # fact the handler had been changed between the two sessions and that
        # report was accurate when written. The log recorded every event and
        # nothing about the code that produced them, so the one fact that would
        # have settled it was the one fact absent.
        #
        # Per turn rather than per session: a session can outlive a restart, and
        # the useful question is "what was running when this turn happened".
        # `harness_event` rather than a new kind — `projection.py` already skips
        # these, so it costs no model context and needs no schema change.
        if self.config.build_id:
            await self._log(
                branch_id,
                "harness_event",
                {"type": "build", "build_id": self.config.build_id},
            )

        logged = await self._log(branch_id, "user_message", {"content": message})
        await self._emit({"type": "user_message", "content": message, "seq": logged.seq})

        steps = 0
        self._turn_cost = None
        self._turn_tokens = 0
        policy = await self.policy_for(actor.name)

        # What both of the turn's opening reads are allowed to touch: the
        # account's own expanded read paths, and nothing writable. Built once
        # because two surfaces derived separately from the same policy are two
        # things that can disagree, and `expand_paths` is the step that makes a
        # `$HOME` entry mean the client's home rather than this host's (N145).
        opening_surfaces: dict[str, list[str]] = {
            "read": expand_paths(
                policy.files_read,
                home=actor.home,
                data_dirs=self.config.home_data_dirs,
            ),
            "write": [],
        }

        # The project's own standing instructions, read once per turn.
        #
        # Per turn rather than per step because it is a file read, and per turn
        # rather than per session because a user editing it mid-session means
        # the next turn to use it. After `policy_for` rather than before,
        # because the read is now a dispatch and a dispatch needs surfaces to be
        # checked against (N152).
        project_context = await _read_project_context(
            self.execution,
            self.registry,
            user=actor.name,
            working_dir=working_dir,
            surfaces=opening_surfaces,
            wait_ms=self.config.tool_wait_ms,
        )

        # The working directory's development environment, stated rather than
        # discovered. Once per turn, beside the project context and for the same
        # reason: it is a fact about the user's disk that can change between
        # turns, and a cached one would eventually be a prompt that lies.
        environment_note = await _probe_environment(
            self.execution,
            self.registry,
            user=actor.name,
            working_dir=working_dir,
            surfaces=opening_surfaces,
            wait_ms=self.config.tool_wait_ms,
        )
        warned = False
        # Whether the most recent step ended at the token cap. Read only when
        # the step budget runs out: a turn whose last act was a truncated reply
        # did not finish its thought, and reporting that as `max_iterations`
        # would name the bound that happened to be hit second.
        truncated = False
        # The provider's own token count for the prompt it was last sent, and
        # how many leading messages that number covered. Anchors the context
        # estimate to a measurement instead of to characters/4, whose error is
        # content-dependent and reaches 4x on identifiers (N91).
        prompt_tokens: int | None = None
        measured_upto = 0
        overflow_retries = 0
        force_compaction = False
        # Whether the previous step ended at the token cap, and how many empty
        # continuations have been retried since. Tracked apart from `truncated`,
        # which describes the *last* step for the turn's end reason and is reset
        # by any step that completes — this has to survive the continuation it
        # is judging.
        continuing_after_truncation = False
        empty_continuations = 0

        while steps < policy.max_iterations:
            steps += 1

            if inbox.interrupted():
                return await self._finish(branch_id, steps, INTERRUPTED)

            # Re-read policy every step: revocation must land mid-turn.
            policy = await self.policy_for(actor.name)

            # The handoff note, once, a few steps from the end.
            #
            # Logged as a `user_message` rather than carried in the request,
            # because the projection turns that into a `{"role": "user"}` entry
            # (projection.py) — so it reaches the model through the same path a
            # steer does, survives a mid-turn compaction, and is in the
            # transcript afterwards. A field on the request would be invisible
            # to both the log and the next turn.
            #
            # `warned` rather than a step comparison, because policy is re-read
            # every step: an operator raising `max_iterations` mid-turn would
            # otherwise re-arm the threshold and send the note twice.
            # A note promising zero further steps is not a handoff: the model
            # reads it, and the loop ends before it can answer. So the threshold
            # must leave at least one step, and a single-step budget gets no
            # note at all rather than a misleading one.
            remaining = policy.max_iterations - steps
            threshold = self._handoff_at(policy.max_iterations)
            if not warned and threshold >= 1 and remaining <= threshold:
                warned = True
                note = HANDOFF_NOTE.format(
                    remaining=remaining, plural="" if remaining == 1 else "s"
                )
                await self._log(branch_id, "user_message", {"content": note})
                # Not emitted to the client. It is addressed to the model, and
                # putting it in the transcript would read as the user having
                # said it — which is exactly the confusion `wrap_untrusted`
                # exists to avoid elsewhere.
                _log.info(
                    "handoff note sent",
                    fields={"branch": branch_id, "step": steps, "remaining": remaining},
                )

            for steer in inbox.drain_steers():
                if steer.content:
                    logged = await self._log(
                        branch_id, "user_message", {"content": steer.content}
                    )
                    await self._emit(
                        {"type": "user_message", "content": steer.content, "seq": logged.seq}
                    )

            # The model is resolved *before* context is managed, because the
            # budget depends on which model this turn goes to. Managing first —
            # which is what this did — meant trimming history against a default
            # while the real window was six times smaller, so the manager
            # returned "it fits" and the provider disagreed (N86).
            target = await self._resolve_model(model)
            if target is None:
                await self._log(
                    branch_id, "harness_event", {"type": MODEL_ERROR, "model": model}
                )
                await self._emit(
                    {
                        "type": "error",
                        "code": MODEL_ERROR,
                        "message": (
                            f"{model!r} is granted by policy but not in the model "
                            "catalog. An operator adds it under Models in the "
                            "administration page."
                        ),
                    }
                )
                return await self._finish(branch_id, steps, MODEL_ERROR)

            # Resolved once per turn, before any prompt is built. A level the
            # model does not declare is refused here rather than forwarded:
            # the provider's own error for an unknown effort string arrives
            # mid-turn, in the provider's dialect, after the tokens are spent.
            try:
                reasoning_level = resolve_level(target.reasoning, reasoning)
            except UnknownLevel as exc:
                await self._log(
                    branch_id,
                    "harness_event",
                    {"type": REASONING_ERROR, "model": model, "level": reasoning},
                )
                await self._emit(
                    {"type": "error", "code": REASONING_ERROR, "message": str(exc)}
                )
                return await self._finish(branch_id, steps, REASONING_ERROR)

            session_events = await self.event_repository.read(
                branch_id, kinds=projection.PROJECTION_KINDS
            )
            history = projection.project(
                session_events,
                reasoning=target.reasoning,
                # Older images become a placeholder, so the base64 is not resent
                # on every turn. Derived from the window like the token tail.
                keep_images=self._keep_images(target),
            )
            history, actions = await self.context.manage(
                history,
                self.gateway,
                # What is left for the prompt once the reply has its room, less
                # the reserve compaction wants to act inside. Both the prompt
                # and the reply share one window, so budgeting the prompt
                # against the whole of it leaves no space to answer in — and
                # budgeting it against the whole *remainder* leaves no space to
                # compact in, which is the same mistake one level down (N116).
                budget_tokens=self._compaction_budget(target),
                keep_tokens=self._keep_tokens(target),
                prompt_tokens=prompt_tokens,
                measured_upto=measured_upto,
                force=force_compaction,
                # The turn's own model and endpoint. Their absence was the bug:
                # the summarisation request named no model, so the gateway fell
                # back to the configured default endpoint and a hosted provider
                # refused it — compaction had never once succeeded (N94).
                model=target.provider_model,
                endpoint=target.endpoint,
                api_key_file=target.api_key_file,
                # The turn's own reasoning dialect, so the summariser asks for
                # thinking off in the shape this model understands rather than
                # a hardcoded one (reasoning.summariser_fields).
                reasoning=target.reasoning,
            )
            # Consumed: the retry it was set for is the request about to be
            # sent, and leaving it set would compact on every later step.
            # A summarisation that produced nothing. Retrying the same request
            # would fail the same way, so the turn ends with a cause rather than
            # looping — the previous version logged nothing at all, and the only
            # trace of a failed compaction was an unrelated 400 in the HTTP
            # client's own output (N94).
            #
            # Reported from the manager's own action rather than inferred from
            # an absent `compact`: a history short enough to need no summary
            # also produces no action, and reading that as failure would end
            # healthy turns.
            if any(a.kind == "compact_failed" for a in actions):
                await self._log(
                    branch_id,
                    "harness_event",
                    {"type": MODEL_ERROR, "error": "compaction_failed"},
                )
                await self._emit(
                    {
                        "type": "error",
                        "code": MODEL_ERROR,
                        "message": (
                            "the conversation no longer fits the model's context "
                            "window and could not be summarised; start a new "
                            "session or use a model with a larger window"
                        ),
                    }
                )
                await self._record_context_actions(branch_id, actions)
                return await self._finish(branch_id, steps, MODEL_ERROR)

            force_compaction = False
            await self._record_context_actions(branch_id, actions)

            # How large the history actually is, after whatever the manager did
            # to it. Measured where there is a measurement, which is what makes
            # the clamp below trustworthy rather than another characters/4 guess
            # (N91). Re-derived here rather than returned by `manage`, because
            # the clamp is about the request that is about to be sent and the
            # manager may have compacted since the count was taken.
            history_tokens = (
                measured_tokens(
                    history, prompt_tokens=prompt_tokens, measured_upto=measured_upto
                )
                if not actions
                else estimate_tokens(history)
            )

            reservation = await self.usage.reserve(
                account_id=actor.account_id,
                tokens_daily=policy.tokens_daily,
                # The model's own ceiling where it states one: reserving more
                # than a reply can ever use would charge a budget for tokens
                # the provider will not emit.
                requested=self._reply_ceiling(target),
            )
            if reservation is None:
                await self._log(branch_id, "harness_event", {"type": BUDGET})
                await self._emit({"type": "error", "code": BUDGET})
                return await self._finish(branch_id, steps, BUDGET)

            # Clamped to what the window can still hold. The reservation is the
            # budget's ceiling and this is the window's; a reply respects both.
            # `window_squeezed` is kept beside the number, because a length stop
            # *at* the figure means the provider ran out of window rather than
            # the model running out of cap — a different problem with a
            # different remedy (N92, N148).
            requested_reply, window_squeezed = self._reply_budget_and_window(
                target, history_tokens, reservation.tokens
            )

            request = build_request(
                model=target.provider_model,
                user=actor.name,
                policy=policy,
                registry=self.registry,
                messages=history,
                max_tokens=requested_reply,
                # A hint, not a grant: the profile below is still built from
                # policy, so this tells the model where to look and cannot change
                # where it may look.
                working_dir=working_dir,
                attachments=attachments,
                name=self.config.agent_name,
                operator_prompt=self.config.operator_prompt,
                account_prompt=account_prompt,
                project_context=project_context,
                environment_note=environment_note,
                # The reach the model is told about must be the reach a dispatch
                # will actually get, which means expanded the same way the
                # working-directory check expands it. Telling it the raw entry
                # sent it to a home directory that is never named wholesale.
                expanded_read=expand_paths(
                    policy.files_read,
                    home=actor.home,
                    data_dirs=self.config.home_data_dirs,
                ),
                expanded_write=expand_paths(
                    policy.files_write,
                    home=actor.home,
                    data_dirs=self.config.home_data_dirs,
                ),
                # Narrows the advertisement, never the authority. Passed on
                # every step rather than resolved once per turn, for the reason
                # the whole loop re-reads policy: the selection is read from the
                # session row at turn start, so a tool the user deselects takes
                # effect on their next message the way a revocation does (D50).
                tool_selection=tool_selection,
                # The other half of the handshake. The client said which tools it
                # can run when it connected; intersecting that here means the
                # model is not told about a tool the machine in front of it has
                # no program for. `None` when no client is connected, which
                # advertises the granted set and lets the dispatch report what is
                # actually wrong.
                available=self.execution.available(actor.name),
            )
            # Asked for in this provider's dialect, which is the only thing
            # `reasoning_fields` knows how to decide. Merged after the request
            # is built rather than threaded through `build_request`: the fields
            # differ per provider, and none of them is a concept the prompt
            # builder has an opinion about.
            request.update(reasoning_fields(target.reasoning, reasoning_level))
            # The log keeps the *derived* request — image references, not bytes.
            # `history_tokens` above was taken on the same small form, which is
            # the point: the estimate is not the size of the base64 (N148). The
            # wire copy puts the bytes back from the content-addressed store,
            # only for the provider, so a picture is neither measured nor stored
            # once per turn it appears in.
            await self._log(branch_id, "request", request)
            wire = {
                **request,
                "messages": await projection.resolve_images(request["messages"], self.images),
            }

            outcome = await self._stream(
                branch_id,
                wire,
                reservation,
                inbox,
                endpoint=target.endpoint,
                api_key_file=target.api_key_file,
            )
            if outcome.failure == OVERFLOW:
                # Compact and retry, once per step. The measurement that should
                # have prevented this is now known to be wrong — the provider
                # just said so — so the next pass forces compaction rather than
                # trusting the same budget arithmetic that let it through.
                if overflow_retries >= MAX_OVERFLOW_RETRIES:
                    await self._emit(
                        {
                            "type": "error",
                            "code": MODEL_ERROR,
                            "message": (
                                "the prompt still exceeds the model's context window "
                                "after compacting; the window may be too small for "
                                "this conversation"
                            ),
                        }
                    )
                    return await self._finish(branch_id, steps, MODEL_ERROR)
                overflow_retries += 1
                await self._emit({"type": "truncated", "step": steps})
                # Not counted as a step: the model never ran, and spending the
                # step budget on a failed request would turn a recoverable
                # overflow into a shorter turn.
                steps -= 1
                force_compaction = True
                continue

            if outcome.failure is not None:
                return await self._finish(branch_id, steps, outcome.failure)

            # What this step cost. Computed before the event is written so the
            # event carries it: the overview's spend by model is read back from
            # these rows, and a figure recomputed later from today's prices
            # would rewrite yesterday's bill.
            step_cost = turn_cost(
                outcome.usage,
                input_cost_per_m=target.input_cost_per_m,
                cached_input_cost_per_m=target.cached_input_cost_per_m,
                output_cost_per_m=target.output_cost_per_m,
            )
            await self._log(
                branch_id,
                "response_final",
                {
                    "message": outcome.message,
                    "finish_reason": outcome.finish_reason,
                    "usage": outcome.usage,
                    "model": target.name,
                    # Null for a model with no rates, which is a local one:
                    # unpriced, not free of cost to someone.
                    "cost_usd": step_cost.usd,
                    "cost_estimated": step_cost.estimated,
                },
            )
            # The window goes out beside the counts, because a token total with
            # no denominator cannot be read: 12,000 is comfortable on one model
            # and fatal on another. Omitted when unrecorded, so the client draws
            # no capacity bar rather than one against a guessed ceiling.
            usage_frame: dict[str, Any] = {"type": "usage", "usage": outcome.usage}
            if target.context_tokens > 0:
                usage_frame["context_tokens"] = target.context_tokens
                # What a capacity bar should measure against. The window reserves
                # the reply ceiling, a margin and the compaction reserve before
                # the conversation gets any, so on a 65,536-token model
                # compaction fires at 30,311 — and a bar against 65,536 sat a
                # little under half full at the moment history was summarized,
                # which is the one number a user cannot act on. The window is
                # still sent beside it: it is the fact models are compared on,
                # and it belongs in the detail rather than in the gauge.
                compacts_above = self._compaction_budget(target)
                if compacts_above is not None and compacts_above > 0:
                    usage_frame["compacts_above"] = compacts_above

            # Folded into the session's running total. Per step rather than per
            # turn because a turn with three tool calls is three billed
            # requests, and the one the user watches is the total.
            self._turn_tokens += _tokens_of(outcome.usage)
            if step_cost.usd is not None:
                running = self._branch_cost.setdefault(branch_id, _SessionCost())
                running.add(step_cost)
                if self._turn_cost is None:
                    self._turn_cost = _SessionCost()
                self._turn_cost.add(step_cost)
                usage_frame["session_cost_usd"] = round(running.usd, 6)
                usage_frame["session_cost_estimated"] = running.estimated
            await self._emit(usage_frame)

            # Set once per step, before anything branches on it: whether *this*
            # step ended at the cap. Assigning it inside the no-tool-calls
            # branch instead left it untouched by a step that dispatched tools,
            # so a turn that truncated once and then spent its remaining steps
            # on tools would still have reported `truncated` at the end.
            #
            # Found by deleting the assignment and watching the test that was
            # supposed to guard it pass anyway — it was dead code, immediately
            # followed by a `return`.
            # What the provider counted, for the next step's budget. Recorded
            # with the length of the history it describes, because a count
            # without that is unusable: the tail has to be estimated separately.
            #
            # **Only when the turn did not compact.** On a compacting step the
            # count describes a prompt this harness built specially; the next
            # step re-projects from the log and estimates the small tail since,
            # which is the conservative reading and costs one step of precision
            # rather than anchoring to a figure whose meaning is easy to get
            # wrong.
            #
            # Dropping the anchor entirely is not the alternative (N95): the
            # estimator's 4.6x error on identifiers is why it exists. Skipping a
            # single step, and keeping the previous anchor when there was one, is
            # because the history it described is still a prefix of what is
            # projected now.
            counted = outcome.usage.get("prompt_tokens")
            if isinstance(counted, int) and counted > 0 and not actions:
                prompt_tokens = counted
                measured_upto = len(history)

            truncated = outcome.finish_reason == FINISH_LENGTH

            tool_calls = outcome.message.get("tool_calls") or []
            if not tool_calls:
                # Why the model stopped, which was recorded and never read.
                #
                # `finish_reason` has always been captured from the provider and
                # written to the event log, and the loop asked only whether
                # there were tool calls — so a generation cut off at
                # `max_tokens` ended the turn as a clean `stop`. The symptom was
                # Neuman reasoning for a minute and then going silent, and
                # "continue" working, because the next turn re-reads the
                # truncated reasoning from history and carries on (N90).
                #
                # Measured on this appliance: five generations in six hours
                # stopped at exactly 4096 tokens, which is `max_tokens`.
                if outcome.finish_reason == FINISH_LENGTH:
                    # Two different situations wear the same `finish_reason`.
                    #
                    # The model used its whole allowance: it had the room asked
                    # for and wanted more. Continuing is right — its partial
                    # output is in history and the next step resumes from it,
                    # which is what typing "continue" was doing by hand (N90).
                    #
                    # Or the window, not the cap, was the binding constraint. Two
                    # ways that shows: the reply came back *shorter* than asked
                    # (the provider clamped a number the model could not have
                    # chosen), or the number itself was already the floor because
                    # the window had nothing left — and then the model can use
                    # every token of it, so `produced == requested` and the
                    # smaller-than-asked test misses it entirely. The second is
                    # the shape that killed a session: history estimated at 323k
                    # against a 65k window clamped the ask to 512, the model
                    # emitted 512, and `clamped` read false so 70 steps of
                    # fragments were each taken for "it wanted more" (N148).
                    #
                    # More steps cannot help in either case — each one truncates
                    # again — and the remedy is to free history. `pi` draws the
                    # same distinction in `isRecoverableLength`, and compacts.
                    produced = outcome.usage.get("completion_tokens")
                    clamped = window_squeezed or (
                        isinstance(produced, int)
                        and produced > 0
                        and produced < requested_reply
                    )

                    await self._log(
                        branch_id,
                        "harness_event",
                        {
                            "type": TRUNCATED,
                            "step": steps,
                            "clamped": clamped,
                            "produced": produced,
                            "requested": requested_reply,
                        },
                    )
                    await self._emit({"type": "truncated", "step": steps})

                    if clamped:
                        _log.info(
                            "reply clamped by the context window; compacting",
                            fields={
                                "branch": branch_id,
                                "step": steps,
                                "produced": produced,
                                "requested": requested_reply,
                            },
                        )
                        # The same recovery as an overflow, and bounded the same
                        # way: compaction that does not help must not loop.
                        if overflow_retries < MAX_OVERFLOW_RETRIES:
                            overflow_retries += 1
                            force_compaction = True
                            steps -= 1
                        continue

                    _log.info(
                        "reply truncated at the token cap; continuing",
                        fields={"branch": branch_id, "step": steps},
                    )
                    continuing_after_truncation = True
                    continue

                # A continuation that said nothing at all.
                #
                # `stop` with no content and no tool call is a legitimate end to
                # an ordinary turn — a model answering a steer it has nothing to
                # add to. Directly after a truncation it is not: the thought was
                # cut mid-sentence, so there was by construction something left
                # to say, and a turn that ends here renders nothing after the
                # "continuing" note and reads as a hang (N139).
                #
                # Retried once rather than failed outright, because the first
                # cause found was a *harness* bug — the thought was being
                # dropped before the model ever saw it — and a retry on a fixed
                # projection succeeds. Beyond that the model is declining to
                # continue, which is a failure worth naming rather than
                # reporting as a clean stop.
                if (
                    continuing_after_truncation
                    and not (outcome.message.get("content") or "").strip()
                ):
                    if empty_continuations < MAX_EMPTY_CONTINUATIONS:
                        empty_continuations += 1
                        _log.warning(
                            "the continuation of a truncated reply was empty; retrying",
                            fields={
                                "branch": branch_id,
                                "step": steps,
                                "produced": outcome.usage.get("completion_tokens"),
                            },
                        )
                        continue

                    _log.warning(
                        "the continuation of a truncated reply was empty; ending the turn",
                        fields={
                            "branch": branch_id,
                            "step": steps,
                            "produced": outcome.usage.get("completion_tokens"),
                        },
                    )
                    await self._log(
                        branch_id,
                        "harness_event",
                        {"type": MODEL_ERROR, "error": "empty_continuation"},
                    )
                    await self._emit(
                        {
                            "type": "error",
                            "code": MODEL_ERROR,
                            "message": (
                                "the model stopped without finishing a reply that had "
                                "been cut off at the token limit"
                            ),
                        }
                    )
                    return await self._finish(branch_id, steps, MODEL_ERROR)

                return await self._finish(branch_id, steps, STOP)

            # The model asked for a tool, so the thought that was cut off has
            # resumed and reached an action. Any later bare `stop` is an
            # ordinary end rather than an abandoned continuation.
            continuing_after_truncation = False

            if truncated:
                # A reply cut off *while emitting a tool call*. Every branch
                # above is in the `not tool_calls` case, so until now this one
                # fell straight through to execution: the cap was never
                # announced, and the fragment went back into history on the next
                # request, which llama.cpp refused with a 500 (N140).
                #
                # Announced for the same reason the other truncation is — the
                # step's shape is otherwise invisible — and the turn continues,
                # because the model needs to re-issue the call it did not finish
                # saying. `_run_tool` declines the unparseable one on its own and
                # answers it with a result, so the pairing holds either way.
                await self._log(
                    branch_id,
                    "harness_event",
                    {
                        "type": TRUNCATED,
                        "step": steps,
                        "in_tool_call": True,
                        "produced": outcome.usage.get("completion_tokens"),
                        "requested": requested_reply,
                    },
                )
                await self._emit({"type": "truncated", "step": steps})
                _log.info(
                    "reply truncated mid tool call; the call will not run",
                    fields={
                        "branch": branch_id,
                        "step": steps,
                        "calls": len(tool_calls),
                    },
                )

            for call in tool_calls:
                await self._run_tool(
                    branch_id,
                    actor,
                    policy,
                    call,
                    inbox,
                    target,
                    working_dir,
                    tool_selection,
                    truncated=truncated,
                    approval=approval,
                    session_id=session_id,
                )
                if inbox.interrupted():
                    return await self._finish(branch_id, steps, INTERRUPTED)

        return await self._finish(branch_id, steps, TRUNCATED if truncated else MAX_ITERATIONS)

    # -- model ---------------------------------------------------------------

    async def _stream(
        self,
        branch_id: str,
        request: dict[str, Any],
        reservation: Any,
        inbox: Inbox,
        endpoint: str | None = None,
        api_key_file: str | None = None,
    ) -> _StreamOutcome:
        content: list[str] = []
        reasoning: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        finish_reason = ""

        # A locally hosted model may have to be loaded into VRAM on first use,
        # which takes tens of seconds. Without a frame saying so the client
        # shows nothing between send and the first token, which looks exactly
        # like a hung appliance.
        #
        # Triggered by *observed* silence rather than by knowing the endpoint is
        # local: the harness cannot tell a cold load from a slow provider, and
        # does not need to — "nothing yet" is the true statement either way.
        waiting = asyncio.ensure_future(self._say_waiting())

        try:
            async for chunk in self.gateway.stream(
                request, endpoint=endpoint, api_key_file=api_key_file
            ):
                # The first chunk means generation started; anything later is
                # ordinary streaming latency and not worth a frame.
                if not waiting.done():
                    waiting.cancel()
                if inbox.interrupted():
                    waiting.cancel()
                    await self.usage.release(reservation)
                    return _StreamOutcome(failure=INTERRUPTED)

                await self._log(
                    branch_id, "response_chunk", {"kind": chunk.kind, "data": chunk.data}
                )
                self._accumulate(chunk, content, reasoning, tool_calls, usage)
                if chunk.kind == "content":
                    await self._emit({"type": "token", "text": chunk.data.get("text", "")})
                elif chunk.kind == "reasoning":
                    await self._emit({"type": "reasoning", "text": chunk.data.get("text", "")})
                elif chunk.kind == "finish":
                    finish_reason = str(chunk.data.get("reason", ""))

        except Exception as exc:  # noqa: BLE001 - a retry taxonomy is future work
            # Before anything else: a timer outliving its stream would fire into
            # a turn that has already ended, putting `waiting` after `done`.
            waiting.cancel()
            await self.usage.release(reservation)
            _log.warning(
                "model call failed",
                fields={"branch": branch_id, "error": _describe(exc)},
            )
            await self._log(
                branch_id,
                "harness_event",
                {
                    "type": MODEL_ERROR,
                    # The message verbatim, and the class beside it (N162).
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            # Overflow is recoverable, so it is reported as its own failure and
            # the caller compacts and retries rather than ending the turn. Not
            # emitted to the client as an error for the same reason: a recovered
            # overflow is a pause, and a red line in the transcript for
            # something the harness handled reads as a fault (N91).
            if is_context_overflow(str(exc)):
                await self._log(
                    branch_id, "harness_event", {"type": OVERFLOW, "error": str(exc)}
                )
                _log.info(
                    "prompt overflowed the window; compacting",
                    fields={"branch": branch_id},
                )
                return _StreamOutcome(failure=OVERFLOW)

            await self._emit({"type": "error", "code": MODEL_ERROR, "message": _describe(exc)})
            return _StreamOutcome(failure=MODEL_ERROR)

        waiting.cancel()
        await self.usage.settle(reservation, int(usage.get("total_tokens") or 0))

        message: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
        # Blank-stripped: whitespace-only reasoning is a model that thought
        # nothing, and recording it as thinking would make the two cases
        # indistinguishable to `replay_fields`.
        thought = "".join(reasoning)
        if thought.strip():
            message["reasoning_content"] = thought
        if tool_calls:
            message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]

        return _StreamOutcome(message=message, usage=usage, finish_reason=finish_reason)

    @staticmethod
    def _accumulate(
        chunk: Chunk,
        content: list[str],
        reasoning: list[str],
        tool_calls: dict[int, dict[str, Any]],
        usage: dict[str, Any],
    ) -> None:
        """Fold one streamed chunk into the assembling response."""

        if chunk.kind == "content":
            content.append(str(chunk.data.get("text", "")))
        elif chunk.kind == "reasoning":
            # Kept, not just shown. Every reasoning token was already logged as
            # a `response_chunk` and streamed to the client, and the assembled
            # message dropped all of it — so a continued reply went back to the
            # provider with its thinking missing (N115).
            reasoning.append(str(chunk.data.get("text", "")))
        elif chunk.kind == "usage":
            usage.update(chunk.data)
        elif chunk.kind == "tool_call":
            index = int(chunk.data.get("index", 0))
            call = tool_calls.setdefault(
                index,
                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
            )
            if chunk.data.get("id"):
                call["id"] = str(chunk.data["id"])
            function = chunk.data.get("function") or {}
            if function.get("name"):
                call["function"]["name"] = str(function["name"])
            if function.get("arguments"):
                # Arguments stream as JSON fragments; concatenate verbatim.
                call["function"]["arguments"] += str(function["arguments"])

    async def _say_waiting(self) -> None:
        """Emit one `waiting` frame if the model stays silent past the grace.

        Cancelled the moment a chunk arrives, so a responsive endpoint sends
        nothing. Advisory: a client that ignores the frame is correct, just less
        informative.
        """

        try:
            await asyncio.sleep(self.config.waiting_after_s)
        except asyncio.CancelledError:
            return
        await self._emit(
            {
                "type": "waiting",
                "on": "model",
                "detail": "the model has not started generating yet",
            }
        )

    def _reply_budget(self, target: ModelTarget, history_tokens: int, requested: int) -> int:
        """How large a reply this window can still hold. See
        `_reply_budget_and_window` for the reasoning; this is its number alone,
        for the callers that only need the size.
        """

        return self._reply_budget_and_window(target, history_tokens, requested)[0]

    def _reply_budget_and_window(
        self, target: ModelTarget, history_tokens: int, requested: int
    ) -> tuple[int, bool]:
        """How large a reply this window can still hold, at most `requested`.

        `max_tokens` is a *ceiling*, not a fixed ask. Sending the configured
        value regardless of how full the window is means that as history grows
        the prompt plus the reply eventually do not fit — and the failure lands
        as a provider error rather than as a smaller reply (N92). So the reply
        is sized against what is actually left.

        This is what makes a generous `maxTokens` correct rather than risky: the
        number is what a reply may reach when there is room, and it shrinks
        itself when there is not. Without the clamp, raising it makes a late-
        session overflow *more* likely, which is the opposite of the intent.

        Taken from `pi`'s `clampMaxTokensToContext`, including its 4096-token
        safety reserve.

        Returns `(requested, False)` unchanged when the model's window is
        unknown: there is nothing to clamp against, and inventing a number is
        the bug that `context_tokens` exists to avoid. The boolean is whether
        the *window*, not the model's ceiling, is what bound the reply — the
        same condition that is logged below, returned rather than only
        recorded, so the loop can read a length stop as a window problem
        instead of mistaking it for the model using its whole allowance (N148).
        """

        # The model's own ceiling where it has one, the configured default
        # otherwise. A per-model fact beats an appliance-wide guess: a local 27B
        # and a hosted frontier model have different output limits, and the
        # global can only be right for one of them (N93).
        #
        # `requested` is the usage reservation, already sized by
        # `_reply_ceiling`, so this is a second clamp against the same figure
        # rather than a different opinion about it — and it still has to happen,
        # because a reservation can come back smaller when the daily budget is
        # nearly spent.
        requested = min(requested, self._reply_ceiling(target))

        if target.context_tokens <= 0:
            return requested, False

        available = target.context_tokens - history_tokens - CONTEXT_SAFETY_TOKENS

        # A reply the window has squeezed is worth saying out loud.
        #
        # Not only the floor: by the time `available` is under
        # `MIN_REPLY_TOKENS` the window has 512 tokens left and the turn is
        # already over. The condition that mattered in the session that died was
        # softer and lasted far longer — history at 97.5% of the window left
        # 2,501 tokens of room, so every reply came back a fragment and seventy
        # steps of them read as "the reply hit the length limit; continuing"
        # rather than as a window with nothing left in it (N116).
        #
        # With compaction triggered by a reserve (`_compaction_budget`) this
        # should now be unreachable, which is exactly why it is logged: if it
        # appears, the reserve is wrong and the record says so in the model's
        # own numbers.
        squeezed = available < requested
        if squeezed:
            _log.warning(
                "the window squeezed the reply",
                fields={
                    "model": target.name,
                    "history_tokens": history_tokens,
                    "context_tokens": target.context_tokens,
                    "requested": requested,
                    "available": max(MIN_REPLY_TOKENS, available),
                },
            )

        return min(requested, max(MIN_REPLY_TOKENS, available)), squeezed

    @staticmethod
    def _handoff_at(max_iterations: int) -> int:
        """How many steps from the end to warn, for a turn of this length.

        `HANDOFF_STEPS_REMAINING` normally, but never more than half the budget:
        a limit of 4 would otherwise warn on the first step, which turns every
        turn into a summary and does no work at all. Below that the note arrives
        at the halfway point, which is the most warning a short turn can carry
        while still having been a turn.

        Returns 0 for a single-step budget — there is no room to hand off from a
        turn that gets one model call, and the caller sends no note rather than
        one promising zero further steps. Found by running it: a limit of 1
        produced "you have 0 steps left", which the model could read and never
        act on.
        """

        return min(HANDOFF_STEPS_REMAINING, max_iterations // 2)

    def _reply_ceiling(self, target: ModelTarget) -> int:
        """The most a reply may reach on this model, before the window clamp.

        **The model's own figure where it states one; the configured default
        only as a fallback.** That is what the option documents — "0 defers to
        `services.neuman.maxTokens`" — and the code used to take the *minimum*
        of the two instead, so a catalog preset declaring 131,072 on a model
        that serves it was silently held to whatever appliance-wide number
        happened to be set, while appearing to have taken effect.

        One global cannot be right in either direction across a catalog: a 65k
        local model and a 1M hosted one differ by a factor of sixteen in the
        window a reply is carved from, so the per-model value is the authority
        (N93) and the global is what an unlisted model falls back to.

        Resolved here rather than at each site that needs it, because three
        sites needed it and one of them disagreed: `_prompt_budget` reserved
        the configured value outright, so a 65,536-token local model capping
        itself at 16,384 gave up 131,072 of its window — because some *other*
        model in the catalog could emit that much — and its history budget fell
        to the `context_tokens // 4` floor.

        Nothing here lets a reply exceed the window: `_reply_budget` clamps this
        to what is actually left (N92).
        """

        if target.max_tokens > 0:
            return target.max_tokens
        return self.config.max_tokens

    def _prompt_budget(self, target: ModelTarget) -> int | None:
        """Tokens available for the prompt on this model, or None if unknown.

        Prompt and completion share one window, so the prompt may not have all
        of it: the reply's ceiling has to fit alongside. The reserve is that
        plus a margin, because `estimate_tokens` is characters/4 — close enough
        to trim against, not close enough to sit on the edge of.

        None when the model's window was never recorded. The manager then keeps
        its own default, which is the pre-existing behaviour; what it must not
        do is assume a number, because assuming one is the bug (N86).
        """

        if target.context_tokens <= 0:
            return None

        # Three things share the window and only one of them is history.
        #
        #   * the reply, which needs this model's own ceiling — not the
        #     appliance-wide default, which is only a fallback (N93 one layer
        #     up: a 65k local model should not give up a 1M model's output
        #     allowance);
        #   * the system prompt, which carries every granted tool's full
        #     description and parameter schema — a few thousand tokens that the
        #     history estimate never sees, because it is built per request
        #     rather than projected from the log;
        #   * a margin, because the tail of history is still estimated.
        #
        # The prompt's cost was unaccounted for, so the budget over-promised by
        # exactly the amount nobody was counting (N91). Reserved as a
        # proportion with a floor, like the margin: a 16k window cannot spare
        # 4,000 tokens of overhead, and a 128k one should not reserve only 512.
        margin = max(512, target.context_tokens // 10)
        overhead = max(2048, target.context_tokens // 16)
        budget = target.context_tokens - self._reply_ceiling(target) - margin - overhead

        # A window too small to hold a reply plus any history at all. Floor it
        # rather than going negative: a non-positive budget would make the
        # manager compact every turn to nothing, and the honest failure is the
        # provider refusing an over-long prompt with its own message.
        return max(budget, target.context_tokens // 4)

    def _compaction_reserve(self, target: ModelTarget) -> int:
        """How much headroom to compact *before* reaching, on this model.

        Compaction has to be triggered by a reserve rather than by a refusal.
        Without one the only thing standing between a growing history and the
        provider is `_reply_budget`, which does not compact — it shrinks the
        reply. So pressure was absorbed by starving the answer: at 255,547
        tokens of a 262,144 window the clamp had 2,501 tokens to work with, and
        the turn spent seventy steps emitting fragments that never finished a
        tool call before compaction ran at all (N116).

        A proportion of the window with a floor, like `_prompt_budget`'s margin
        and overhead above, and for the identical reason (D40). `pi` reserves a
        flat 16,384 (`shouldCompact`), which is a quarter of this appliance's
        local qwen and 1.6% of DeepSeek's 1M window — one literal cannot be
        both "ample" and "too late".
        """

        return max(2048, target.context_tokens // 8)

    def _keep_tokens(self, target: ModelTarget) -> int:
        """How much recent history survives a compaction, on this model.

        The configured `keep_recent_tokens` is a floor; this is the operative
        value, derived from the window for the same reason as the reserve. A
        tail sized for a 1M window would be the whole of a 65k one.

        Bounded well under the window because the summary has to fit beside it,
        and because a tail that large would leave compaction nothing to free.
        """

        if target.context_tokens <= 0:
            return self.context.config.keep_recent_tokens
        return max(self.context.config.keep_recent_tokens, target.context_tokens // 16)

    def _keep_images(self, target: ModelTarget) -> int:
        """How many of the newest images stay in the request, on this model.

        The configured `keep_recent_images` is a floor; this is the operative
        value, derived from the window like `_keep_tokens` and for the same
        reason (D40). A window that holds more history can afford to keep more
        visual history; a 65k local model keeps the floor.

        The dividend is scaled far more slowly than the token tail, because an
        image's token cost is bounded and small (N148) while its *payload* is
        neither. The number that matters is how many recent frames a model is
        still reasoning about — not how many fit in the window — so a large
        window does not need a large multiple of the floor.
        """

        if target.context_tokens <= 0:
            return self.context.config.keep_recent_images
        return max(self.context.config.keep_recent_images, target.context_tokens // 65_536)

    def _compaction_budget(self, target: ModelTarget) -> int | None:
        """The size history is actually managed against: the prompt budget less
        the reserve.

        One number rather than a separate trigger, because the manager already
        compacts whatever exceeds the budget it is given. Lowering that budget
        by the reserve is the whole of the change: compaction now happens while
        there is still room to perform it, instead of once the arithmetic has
        already failed.

        None when the window is unknown, which `manage` reads as "keep the
        configured default" — assuming a number is the N86 bug.
        """

        budget = self._prompt_budget(target)
        if budget is None:
            return None
        # Never below the floor `_prompt_budget` already defends: a reserve that
        # ate the whole budget would compact every turn to nothing.
        return max(budget - self._compaction_reserve(target), target.context_tokens // 4)

    def _needs_compaction(self, target: ModelTarget, history_tokens: int) -> bool:
        """Whether history has reached the point of compacting on this model.

        The same comparison `manage` makes against `_compaction_budget`, named
        so a test can assert the threshold directly rather than inferring it
        from whether a fake summariser was called. Unknown windows never trigger
        — there is nothing to measure against, which is the N86 rule.
        """

        budget = self._compaction_budget(target)
        if budget is None:
            return False
        return history_tokens > budget

    async def _resolve_model(self, name: str) -> ModelTarget | None:
        """The local name, translated to what the provider is sent.

        With no resolver injected the name passes through unchanged. That is for
        tests and for an appliance with a single hard-coded model — not a
        default worth relying on, since it is exactly the behaviour that sent a
        local name to a provider and got `"deepseek is not a valid model ID"`.
        """

        if self.resolve_model is None:
            return ModelTarget(name=name, provider_model=name)
        return await self.resolve_model(name)

    # -- tools ---------------------------------------------------------------

    async def _run_tool(
        self,
        branch_id: str,
        actor: Actor,
        policy: EffectivePolicy,
        call: dict[str, Any],
        inbox: Inbox,
        target: ModelTarget,
        working_dir: str | None = None,
        tool_selection: tuple[str, ...] | None = None,
        truncated: bool = False,
        approval: str | None = None,
        session_id: str | None = None,
    ) -> None:
        call_id = str(call.get("id") or uuid.uuid4().hex)
        name = str((call.get("function") or {}).get("name") or "")
        raw_args = (call.get("function") or {}).get("arguments") or "{}"

        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            if not isinstance(args, dict):
                raise TypeError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            # Malformed arguments are a model error, not a denial. Tell it so it
            # can correct itself rather than retrying identically.
            #
            # Which error it is matters. A reply cut off at the token cap leaves
            # `arguments` a JSON fragment, and reporting that as a syntax
            # problem names the symptom: the model reads "missing closing quote"
            # and re-sends the same oversized call, because nothing told it the
            # call was too long to finish saying. `pi` says the cause outright
            # and asks for a re-issue (N140), and the length it asks for is the
            # part Neuman can add — the cap is the constraint, so a shorter call
            # is the remedy rather than a better-quoted one.
            detail = (
                "the reply reached the output token limit before these arguments "
                "were finished, so the call did not run. Re-issue it with complete "
                "arguments, and make them shorter — the last attempt did not fit "
                "in one reply."
                if truncated
                else f"invalid tool arguments: {exc}"
            )
            await self._tool_result(branch_id, call_id, name, detail, exit_code=-1)
            return

        await self._log(
            branch_id,
            "tool_proposal",
            {"tool_call_id": call_id, "name": name, "arguments": args},
        )
        await self._emit(
            {
                "type": "tool_proposal",
                "tool_call_id": call_id,
                "name": name,
                "arguments": args,
            }
        )

        # Deselected, which is not the same fact as ungranted.
        #
        # Checked before the gate so the denial says what is actually true. A
        # deselected tool is absent from the advertisement, so reaching here
        # means the model named one from earlier in the conversation — the
        # schemas are rebuilt per step, and a tool switched off mid-session is
        # still sitting in history where the model can read it. The gate would
        # refuse it anyway (it is in `policy.tools`, so the refusal would come
        # from reach or not at all), but it would have no way to say why, and
        # `tool_not_granted` in the audit chain would send an operator looking
        # for a policy fault that does not exist (D50).
        #
        # Its own reason string rather than a shared one, because the remedy is
        # the user's and nobody else's: no grant needs changing, a checkbox does.
        if tool_selection is not None and name not in tool_selection:
            await chain.append(
                self.audit_repository,
                actor=actor.name,
                action="tool.denied",
                payload={"tool": name, "reason": NOT_SELECTED, "branch": branch_id},
                mirror=self.audit_mirror,
            )
            await self._decide(branch_id, call_id, False, NOT_SELECTED, consent=False)
            await self._tool_result(
                branch_id,
                call_id,
                name,
                # Addressed to the model, which can act on it: it should say so
                # rather than silently working around a tool the user switched
                # off, and it must not keep proposing a call it has no schema
                # for.
                f"denied: {name!r} is switched off for this conversation. Tell the "
                "user it is needed and let them turn it back on; do not try "
                "another tool to get the same effect.",
                exit_code=-1,
            )
            return

        decision = gate.check(name, policy, self.registry)
        if not decision.allowed:
            await chain.append(
                self.audit_repository,
                actor=actor.name,
                action="tool.denied",
                payload={"tool": name, "reason": decision.reason, "branch": branch_id},
                mirror=self.audit_mirror,
            )
            await self._decide(branch_id, call_id, False, decision.reason, consent=False)
            await self._tool_result(
                branch_id, call_id, name, f"denied: {decision.detail}", exit_code=-1
            )
            return

        manifest = self.registry.get(name)
        assert manifest is not None  # the gate rejects tools without one

        # Looking at an image needs a model that can see. Refused here rather than
        # attempted, because an `image_url` part sent to a text-only model is a
        # provider error *after* the tool has run and the tokens are spent — and
        # the error text comes back in provider dialect, which the model cannot
        # act on. Saying which model is selected and that it is text-only is
        # something it can: it will fall back to `fs_read` or ask the user to
        # switch models.
        if manifest.returns_image and not target.vision:
            # In the record for the same reason a gate denial is: this is a
            # governed decision that refused a tool, and the chain is what
            # "every governed decision" means. It was omitted here while the
            # gate's own denial above recorded one, so an operator asking the
            # audit trail why a model never saw an image found nothing —
            # `tool.denied` was documented in `docs/database.md` and written
            # by one of the two paths that deny.
            await chain.append(
                self.audit_repository,
                actor=actor.name,
                action="tool.denied",
                payload={
                    "tool": name,
                    "reason": "model_has_no_vision",
                    "branch": branch_id,
                    "model": target.name,
                },
                mirror=self.audit_mirror,
            )
            await self._decide(branch_id, call_id, False, "model_has_no_vision", consent=False)
            await self._tool_result(
                branch_id,
                call_id,
                name,
                (
                    f"denied: the selected model ({target.name}) does not accept "
                    f"image input, and {name} returns a picture. Ask the user to switch "
                    "to a model registered with vision."
                ),
                exit_code=-1,
            )
            return

        if self._pre_approved and manifest.critical:
            # A task has no one to ask, and a critical action is the one thing
            # that must never be pre-approved. Refuse before the wait rather
            # than block for the approval timeout and then deny: the run should
            # learn immediately that the task's envelope cannot do this.
            await chain.append(
                self.audit_repository,
                actor=actor.name,
                action="tool.denied",
                payload={
                    "tool": manifest.name,
                    "reason": CRITICAL_NOT_PRE_APPROVED,
                    "branch": branch_id,
                },
                mirror=self.audit_mirror,
            )
            await self._decide(
                branch_id, call_id, False, CRITICAL_NOT_PRE_APPROVED, consent=False
            )
            await self._tool_result(
                branch_id,
                call_id,
                manifest.name,
                (
                    "denied: this task has no one to ask, and a critical action is "
                    "never pre-approved. Run it interactively if it is needed."
                ),
                exit_code=-1,
            )
            return

        if not self._pre_approved and self._needs_consent(policy, manifest, approval):
            # What the write would do, before anyone is asked to allow it.
            #
            # The core cannot compute this itself: the file is on the user's
            # machine, so a diff is something it asks the client for like
            # anything else about a filesystem. The preview is a real dispatch
            # through the same tool as the write it describes, which is also
            # what stops it being a second implementation of the edit (N35,
            # N38).
            #
            # Best-effort: a preview that fails must not block the approval it
            # was meant to inform. The frame then carries no diff and the client
            # falls back to showing the arguments, which is what it did before.
            preview = None
            if manifest.name in PREVIEW_TOOLS:
                preview = await self._preview_write(
                    actor=actor,
                    policy=policy,
                    manifest=manifest,
                    args=args,
                    branch_id=branch_id,
                    working_dir=working_dir,
                )

            # Say so *before* blocking. The client draws its approve/deny
            # controls on this frame rather than on the proposal, because a
            # proposal is emitted before the gate runs and cannot know whether
            # anyone will be asked (N89).
            frame: dict[str, Any] = {
                "type": "tool_approval_required",
                "tool_call_id": call_id,
            }
            if preview is not None:
                frame["preview"] = preview
            await self._emit(frame)
            verdict = await inbox.await_approval(
                call_id,
                timeout_s=self.config.approval_timeout_s,
                tool=name,
                # The card, carried with the wait: a window that did not start
                # this turn draws the same prompt from these, and the preview is
                # the one computed below rather than a second dispatch (D63).
                arguments=args,
                preview=preview,
            )
            await self._decide(
                branch_id, call_id, verdict.approved, verdict.reason, consent=True
            )
            await self._emit(
                {
                    "type": "tool_decision",
                    "tool_call_id": call_id,
                    "accepted": verdict.approved,
                    "consent": True,
                }
            )
            if not verdict.approved:
                await self._tool_result(
                    branch_id,
                    call_id,
                    name,
                    f"denied by the user ({verdict.reason or 'no reason given'})",
                    exit_code=-1,
                )
                return
        else:
            await self._decide(
                branch_id,
                call_id,
                True,
                TASK_ENVELOPE if self._pre_approved else gate.ALLOWED,
                consent=False,
            )
            # Emitted for an auto-approved call too, which it was not.
            #
            # The client builds every tool card with an Approve/Deny row and
            # removes it when the verdict arrives. This branch recorded the
            # decision and never sent one, so a call nobody was asked about kept
            # its buttons for the life of the session — offering a choice that
            # had already been made, and pressing either did nothing because no
            # inbox was waiting (N89).
            #
            # `consent: false` is what distinguishes it, and the contract already
            # said so: "consent=false means the gate decided alone". The frame
            # was specified and never sent.
            await self._emit(
                {
                    "type": "tool_decision",
                    "tool_call_id": call_id,
                    "accepted": True,
                    "consent": False,
                }
            )

        if manifest.kind == "server-native":
            await self._run_server_native(branch_id, actor, call_id, manifest, args)
            return

        # Which of the job tools the model has this step. A call becomes a job
        # only when the model can follow it: a job it cannot look at is a
        # process it has lost, which is the `setsid … &` this exists to replace.
        offered = (
            selected(
                policy, self.registry, tool_selection, self.execution.available(actor.name)
            )
            if manifest.detaches and session_id
            else []
        )
        await self._dispatch(
            branch_id,
            actor,
            policy,
            call_id,
            manifest,
            args,
            inbox,
            working_dir,
            session_id=session_id,
            followable=JOB_OUTPUT in offered,
            killable=JOB_KILL in offered,
        )

    async def _run_server_native(
        self,
        branch_id: str,
        actor: Actor,
        call_id: str,
        manifest: Any,
        args: dict[str, Any],
    ) -> None:
        """Run a tool the core answers itself, never dispatching (docs/decisions.md D13).

        These read Neuman's own state with the caller's identity and a scope
        the handler enforces. They never touch the filesystem, so they never
        leave the core.
        """

        handler = self.server_native.get(manifest.name)
        if handler is None:
            # A manifest says server-native but nothing is registered: fail
            # closed rather than falling through to a dispatch (docs/decisions.md D5).
            await self._tool_result(
                branch_id,
                call_id,
                manifest.name,
                f"{manifest.name!r} is declared server-native but has no handler",
                exit_code=-1,
            )
            return

        try:
            content = await handler(actor, args, branch_id)
        except Exception as exc:  # noqa: BLE001 - one tool must not end the turn
            _log.warning(
                "server-native tool failed",
                fields={"tool": manifest.name, "error": str(exc)},
            )
            await self._tool_result(
                branch_id, call_id, manifest.name, f"error: {exc}", exit_code=-1
            )
            return

        await self._tool_result(branch_id, call_id, manifest.name, content, exit_code=0)

    def _surfaces(self, policy: EffectivePolicy, actor: Actor) -> dict[str, list[str]]:
        """The account's declared surfaces, expanded, for the client to check against.

        Sent with every dispatch rather than negotiated once at connect: policy
        is re-read every step, so a revocation has to reach the client on the
        next call rather than the next reconnect.
        """

        return {
            "read": expand_paths(
                policy.files_read,
                home=actor.home,
                data_dirs=self.config.home_data_dirs,
            ),
            "write": expand_paths(
                policy.files_write,
                home=actor.home,
                data_dirs=self.config.home_data_dirs,
            ),
        }

    async def _dispatch(
        self,
        branch_id: str,
        actor: Actor,
        policy: EffectivePolicy,
        call_id: str,
        manifest: Any,
        args: dict[str, Any],
        inbox: Inbox,
        working_dir: str | None = None,
        session_id: str | None = None,
        followable: bool = False,
        killable: bool = False,
    ) -> None:
        """Hand an approved dispatch to the effect plane.

        The wait is the manifest's, under the operator's ceiling. The lifetime
        is the manifest's when the call may become a job (D98) — the tool
        declares one longer than its wait and the model holds `job_output` —
        and the wait otherwise, which tells the client to kill at the wait.
        """

        dispatch_id = uuid.uuid4().hex
        wait_ms = min(manifest.wait_ms, self.config.tool_wait_ms)
        detachable = followable and manifest.detaches
        lifetime_ms = max(manifest.lifetime_ms, wait_ms) if detachable else wait_ms

        await chain.append(
            self.audit_repository,
            actor=actor.name,
            action="tool.dispatch",
            payload={
                "tool": manifest.name,
                "branch": branch_id,
                "dispatch_id": dispatch_id,
                "mutating": manifest.mutating,
                "critical": manifest.critical,
            },
            mirror=self.audit_mirror,
        )

        try:
            outcome = await self.execution.dispatch(
                user=actor.name,
                dispatch_id=dispatch_id,
                tool=manifest.name,
                args=args,
                wait_ms=wait_ms,
                lifetime_ms=lifetime_ms,
                surfaces=self._surfaces(policy, actor),
                session_id=session_id if detachable else None,
                branch_id=branch_id,
                # Where the session says it is. A hint, not a grant:
                # `within_reach` checked this path when it was set, and the
                # client runs it as the user. Before this field every dispatch
                # started in the server's own cwd — `/` — so a tool looking for
                # a `.envrc` beside itself looked in the wrong place (N84).
                working_dir=working_dir,
            )
        # `BaseException`, not `Exception`: an interrupt or a dropped client
        # raises `CancelledError`, which is not an `Exception` and so used to
        # leave the turn with a `tool.dispatch` in the chain and no result after
        # it. Every subsequent model call needs a `tool` message per
        # `tool_call_id`, so one missing result does not lose a line — it makes
        # the conversation unsendable, and the turn stops without a terminal
        # frame. The client, waiting on `done`, presents that as a frozen app
        # (N104).
        except BaseException as exc:
            _log.warning(
                "dispatch failed",
                fields={"tool": manifest.name, "dispatch_id": dispatch_id, "error": str(exc)},
            )
            # Written before the re-raise below, so the record exists even for a
            # cancellation that is about to unwind the turn.
            await self._tool_result(
                branch_id, call_id, manifest.name, f"dispatch error: {exc}", exit_code=-1
            )
            # A cancellation is not a tool failure to be swallowed: the caller
            # decided this turn is over, and `asyncio` requires it propagate.
            if isinstance(exc, asyncio.CancelledError):
                raise
            return
        finally:
            # An interrupt that arrived while the tool ran must kill it, not
            # merely be noticed afterwards.
            #
            # Guarded because this runs on the *success* path too, and `cancel`
            # opens its own connection: a `RunnerUnavailable` raised here would
            # replace the outcome of a dispatch that already succeeded, and
            # would do it past the handler above rather than through it.
            if inbox.interrupted():
                try:
                    await self.execution.cancel(user=actor.name, dispatch_id=dispatch_id)
                except Exception as exc:  # noqa: BLE001 - best effort
                    _log.warning(
                        "cancel failed", fields={"dispatch_id": dispatch_id, "error": str(exc)}
                    )

        # The diff rides out of stdout the way `fs_image`'s bytes do, and for
        # the same reason: it is for a human reading a transcript, and the model
        # asked for this change so reading it back is tokens spent to tell it
        # what it just said.
        diff_details: dict[str, Any] | None = None
        if manifest.name in PREVIEW_TOOLS and outcome.exit_code == 0:
            lifted, diff_details = _split_diff(outcome.stdout)
            if diff_details is not None:
                outcome = replace(outcome, stdout=lifted)

        if outcome.job_id is not None:
            # Recorded before the result that tells the model, so the chain
            # holds the job before anything can act on it. The verdict that let
            # this run covered the words, not the duration (D98); this entry is
            # where the duration starts being on the record.
            await chain.append(
                self.audit_repository,
                actor=actor.name,
                action="job.detached",
                payload={
                    "tool": manifest.name,
                    "branch": branch_id,
                    "session": session_id,
                    "dispatch_id": dispatch_id,
                    "job_id": outcome.job_id,
                    "lifetime_ms": outcome.lifetime_ms,
                },
                mirror=self.audit_mirror,
            )
            # The browser's job row reads the daemon's registry, not this
            # frame: the frame only says the set changed, so the row re-reads
            # the one state both ends already share (D98). A job that ends
            # between turns sends nothing — there is no turn's socket to send it
            # on — so the notice in the transcript is what the person reads, and
            # the row is re-read when the view is next opened.
            await self._emit({"type": "jobs"})
            # The preview the dispatch forwarded, both streams: the client
            # counts both as shown, so a look will not repeat either. Then one
            # sentence. No spill path: a look names it when it leaves output
            # out, and before then it is tokens the model has no use for.
            content = "\n".join(
                part
                for part in (
                    outcome.stdout.rstrip("\n"),
                    f"stderr:\n{outcome.stderr.rstrip()}" if outcome.stderr.strip() else "",
                    detached_note(
                        outcome.job_id,
                        wall_ms=outcome.wall_ms,
                        lifetime_ms=outcome.lifetime_ms,
                        can_kill=killable,
                    ),
                )
                if part
            )
        else:
            content = outcome.stdout or outcome.stderr
        if outcome.timed_out:
            note = (
                # It could have become a job and the client had no room.
                refused_note(wait_ms)
                if lifetime_ms > wait_ms
                else f"[timed out after {outcome.wall_ms} ms]"
            )
            content = f"{content}\n{note}".strip()
        # Output that outgrew the preview is a file the model reads with its own
        # tools, named where it lies. This is the whole shape: the harness does
        # not hold a document in a message and does not elide one later, it hands
        # over a path to the file the client already wrote. A job's files are
        # still being written and a look names them when it matters.
        notes = []
        if outcome.job_id is None and outcome.persisted_out:
            notes.append(persisted_note(outcome.persisted_out, outcome.truncated))
        if outcome.job_id is None and outcome.persisted_err:
            notes.append(persisted_note(outcome.persisted_err, outcome.truncated))
        if notes:
            content = f"{content}\n{' '.join(notes)}".strip()
        elif outcome.truncated and outcome.job_id is None:
            content = f"{content}\n[output truncated]".strip()

        # An image tool returns bytes for the *provider*, not text for the model. Its
        # stdout is lifted into an `image` payload here and replaced with a short
        # line, so the transcript, the event log and the model's `tool` message all
        # carry a sentence rather than megabytes of base64.
        image = None
        if manifest.returns_image and outcome.exit_code == 0:
            parsed = _parse_image(outcome.stdout)
            raw = _decode_image(parsed)
            if parsed is None or raw is None:
                content = (
                    f"{manifest.name} returned output this harness could not parse as an image"
                )
            else:
                stored = await self.images.put(
                    session_id=session_id or branch_id,
                    media_type=parsed["media_type"],
                    data=raw,
                )
                content = f"Looked at the image ({stored.media_type}). It follows."
                # The hash, so the request builder fetches the one copy of the
                # bytes; and the path, so the projection can name the file if
                # the image ages out and is replaced by a placeholder.
                image = {
                    "sha256": stored.sha256,
                    "path": str(args.get("path") or ""),
                }

        await self._tool_result(
            branch_id,
            call_id,
            manifest.name,
            content,
            exit_code=outcome.exit_code,
            wall_ms=outcome.wall_ms,
            timed_out=outcome.timed_out,
            cancelled=outcome.cancelled,
            structured=diff_details
            or outcome.structured
            or ({"job_id": outcome.job_id} if outcome.job_id is not None else None),
            persisted={
                stream: path
                for stream, path in (
                    ("stdout", outcome.persisted_out),
                    ("stderr", outcome.persisted_err),
                )
                if path
            }
            or None,
            image=image,
        )

    async def _preview_write(
        self,
        *,
        actor: Actor,
        policy: EffectivePolicy,
        manifest: Any,
        args: dict[str, Any],
        branch_id: str,
        working_dir: str | None,
    ) -> dict[str, Any] | None:
        """Resolve a write without performing it, for the approval frame.

        A second dispatch, deliberately. The alternative — deriving the diff in
        the core — is impossible rather than merely undesirable: the server
        cannot read a file on the user's machine, so only the tool that would
        write can say what the write would do.

        **The boundary here is the tool, not the kernel.** `preview = true` is
        the tool agreeing not to write, and the tool is one of Neuman's own
        binaries, which the model cannot replace. There is no read-only profile
        underneath it — the client runs as the human and reaches what the human
        reaches — so the check is honest about being the tool's own agreement.

        Never audited as a `tool.dispatch` and never shown to the model. It
        performs no effect, so an entry for it would make the record claim two
        writes where one happened. Returns None on any failure: an approval must
        still be askable when the preview is not.
        """

        dispatch_id = uuid.uuid4().hex

        try:
            outcome = await self.execution.dispatch(
                user=actor.name,
                dispatch_id=dispatch_id,
                tool=manifest.name,
                args={**args, "preview": True},
                wait_ms=min(manifest.wait_ms, self.config.tool_wait_ms),
                surfaces=self._surfaces(policy, actor),
                working_dir=working_dir,
            )
        except Exception as exc:  # noqa: BLE001 - a failed preview is not a failed turn
            # `warning`, not `debug`. This path degrades silently by design — the
            # approval still goes out, just without its diff — and that is
            # exactly what let every preview fail unnoticed for a release (N97).
            # A feature that is entirely broken must not look merely absent.
            _log.warning(
                "preview failed",
                fields={"tool": manifest.name, "branch": branch_id, "error": str(exc)},
            )
            return None

        if outcome.exit_code != 0:
            # The write would fail too, and the refusal text is already on its
            # way to the model through the real dispatch. Showing a human a
            # diff-shaped error is worse than showing them nothing — but say so
            # in the log, because this returning None is indistinguishable from
            # the feature not existing (N97).
            _log.warning(
                "preview produced no diff",
                fields={
                    "tool": manifest.name,
                    "branch": branch_id,
                    "exit_code": outcome.exit_code,
                    "stderr": (outcome.stderr or "")[:400],
                },
            )
            return None

        _, details = _split_diff(outcome.stdout)
        return details

    def _needs_consent(
        self, policy: EffectivePolicy, manifest: Any, approval: str | None = None
    ) -> bool:
        """Whether this call needs a human verdict before it runs."""

        # The clamped value, never the raw field: `auto_accept` is a *choice* and
        # `auto_accept_max` is the grant it sits inside, and scalars are
        # last-wins — so a user overlay naming "yolo" would otherwise beat a role
        # that capped them at "safe". Enforced here as well as validated at the
        # route, because a check that exists in only one place is one bug from
        # existing nowhere.
        mode = policy.clamped_auto_accept(approval)
        if mode == "never":
            return True
        if mode == "yolo":
            # Even here, a critical action asks. "Yolo" is a statement about
            # convenience, not about consenting to unrecoverable loss.
            return manifest.critical
        return manifest.mutating or manifest.critical

    # -- logging helpers -----------------------------------------------------

    async def _record_context_actions(self, branch_id: str, actions: list[Any]) -> None:
        for action in actions:
            if action.kind == "compact":
                await self._log(
                    branch_id,
                    "harness_event",
                    {"type": "compaction", **action.detail},
                )
                # Said out loud, because the capacity bar is about to fall by
                # most of its width and an unexplained drop reads as a bug. An
                # earlier version edited history without recording the edit, so
                # the bar alternated between two sizes and nothing on screen
                # accounted for either (N119). Now the only thing that moves it
                # downward is this, so this is what has to say so.
                await self._emit(
                    {
                        "type": "compacted",
                        "summarized_messages": action.detail.get("summarized_messages", 0),
                    }
                )
            else:
                await self._log(
                    branch_id,
                    "harness_event",
                    {"type": "context", "action": action.kind, **action.detail},
                )

    async def _decide(
        self, branch_id: str, call_id: str, accepted: bool, reason: str, *, consent: bool
    ) -> None:
        await self._log(
            branch_id,
            "tool_decision",
            {
                "tool_call_id": call_id,
                "accepted": accepted,
                "reason": reason,
                "consent": consent,
            },
        )

    async def _tool_result(
        self,
        branch_id: str,
        call_id: str,
        name: str,
        content: str,
        *,
        exit_code: int,
        wall_ms: int = 0,
        timed_out: bool = False,
        cancelled: bool = False,
        structured: dict[str, Any] | None = None,
        persisted: dict[str, str] | None = None,
        image: dict[str, str] | None = None,
    ) -> None:
        payload = {
            "tool_call_id": call_id,
            "name": name,
            # Model-visible content is untrusted data, delimited.
            "content": wrap_untrusted(content),
            "exit_code": exit_code,
            "wall_ms": wall_ms,
            "timed_out": timed_out,
            "cancelled": cancelled,
            "structured": structured,
        }
        # Scratch paths as data, alongside the sentence in `content` that names
        # them for the model. A reader reopening the session needs the path keyed
        # to the call that produced it, to title a later `fs_read` of it.
        if persisted is not None:
            payload["persisted"] = persisted
        if image is not None:
            payload["image"] = image

        await self._log(branch_id, "tool_result", payload)

        # The image bytes are deliberately not emitted. The browser frame is for a
        # human reading a transcript, and megabytes of base64 over the WebSocket
        # would stall the UI to deliver them. The client is told a picture exists
        # and fetches it from `/api/branches/{id}/tools/{call_id}/image`, which
        # reads the event log — so the bytes are one request away rather than in
        # every frame and every reload's projection. The event log keeps them,
        # because that is what `project()` rebuilds history from.
        emitted = {k: v for k, v in payload.items() if k != "image"}
        if image is not None:
            emitted["has_image"] = True
        await self._emit({"type": "tool_result", **emitted})

    async def _log(self, branch_id: str, kind: str, payload: dict[str, Any]) -> EventRecord:
        return await events.append(
            self.event_repository,
            branch_id=branch_id,
            kind=kind,
            payload=payload,
            secrets=self.secrets,
        )

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.emit is not None:
            await self.emit(event)

    async def _finish(self, branch_id: str, steps: int, reason: str) -> TurnResult:
        logged = await self._log(
            branch_id, "harness_event", {"type": "turn_complete", "reason": reason}
        )
        # The seq is what lets a live view fork with `after` on this turn
        # without re-reading the branch. It is the `turn_complete` row, the same
        # boundary a later fork resolves to.
        await self._emit({"type": "done", "reason": reason, "seq": logged.seq})
        _log.info(
            "turn complete",
            fields={"branch": branch_id, "steps": steps, "reason": reason},
        )
        return TurnResult(
            branch_id=branch_id,
            steps=steps,
            reason=reason,
            cost_usd=round(self._turn_cost.usd, 6) if self._turn_cost is not None else None,
            tokens=self._turn_tokens,
        )
