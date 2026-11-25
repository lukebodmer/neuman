"""Ports the harness needs: the model gateway, tool execution, the event log."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

__all__ = [
    "Chunk",
    "DispatchOutcome",
    "EventRecord",
    "EventRepository",
    "ImageRepository",
    "ModelGateway",
    "ModelTarget",
    "ReasoningControl",
    "Reservation",
    "StoredImage",
    "Swept",
    "ToolExecution",
    "UsageRepository",
]


@dataclass(frozen=True, slots=True)
class ReasoningControl:
    """How one model is asked to reason, and at what levels.

    A capability rather than a tuning knob. The provider's model id does not say
    which dialect it speaks — and the dialects are mutually unintelligible, so
    sending the wrong one is a provider error mid-turn rather than a setting
    that quietly does nothing. Stated by the operator, for the same reason
    `vision` and `context_tokens` are.

    Verified against each provider's own documentation rather than recalled:
    llama.cpp's server takes a flat `reasoning_effort` and passes it to the
    model's jinja template, DeepSeek takes that string *alongside* a
    `thinking: {"type": "enabled"}` envelope, and a template that understands
    nothing finer takes a boolean in `chat_template_kwargs`, and Kimi K3.1 Pro
    takes the flat string and wants its `reasoning_content` back.
    """

    # "effort" | "budget" | "preserved" | "toggle". A model with no control declares none at
    # all, so there is no member for it — `ModelTarget.reasoning` is None.
    control: str
    # What the client may offer, in the order it offers it. Declared because
    # the sets differ per provider: llama.cpp documents six levels and a hosted
    # provider may accept three.
    levels: tuple[str, ...] = ()
    # What a turn uses when the client names nothing.
    default: str = ""


@dataclass(frozen=True, slots=True)
class ModelTarget:
    """Where a granted model name actually sends, and as what.

    The name a user selects is a *local* name — an appliance's own label for a
    model, added to the catalog in the administration page (D76). It is
    deliberately not the provider's model string: the whole point of the catalog
    is that policy grants a stable name while an operator re-points it, and that
    two providers can offer the same capability under different strings (D20 —
    routing is configuration, not a bundled service).

    That translation existed in the schema and nowhere in the code. The harness
    passed the local name straight to the provider, which answered
    `"deepseek is not a valid model ID"` — the `provider_model` column was
    written by the admin API and read by nothing (N48).
    """

    name: str
    provider_model: str
    # A per-model endpoint, so one appliance can reach two providers. `None`
    # means the gateway's configured default.
    endpoint: str | None = None
    # The *path* to this provider's key, never the key. Per model because the
    # endpoint is: one `ModelClient` serves every provider, and it held a single
    # key read once at startup, so adding a second provider sent the first one's
    # bearer token to it — a live credential disclosed to an unintended host,
    # and a 401 mid-turn on a model the admin surface listed as healthy.
    #
    # A path rather than a value so the key is read at call time: rotating the
    # file then takes effect on the next turn rather than needing a restart, and
    # a secret never sits in a long-lived object. `None` for a provider that
    # needs none, which is the ordinary case on loopback.
    api_key_file: str | None = None
    # Whether this model accepts image input. Recorded by the operator rather
    # than inferred: the provider's model id does not say, and sending an image
    # part to a text-only model is a provider error mid-turn, after the tool has
    # run and the tokens are spent.
    vision: bool = False
    # How many tokens this model's context window holds; 0 when unknown.
    #
    # The number that decides whether a turn fits, and for three features it was
    # known to nobody: the context manager budgeted against a 96,000-token
    # default while a local model served 16,384, so compaction never fired and
    # the turn died at the provider instead (N86). Carried here because the
    # budget has to follow the *resolved* model — two models a user may pick
    # between can have very different windows.
    context_tokens: int = 0
    # The most this model may emit in one reply; 0 when unrecorded, which falls
    # back to the server's configured default. A property of the model rather
    # than of the appliance — one global cannot be right for a local 27B and a
    # hosted frontier model at once (N93).
    max_tokens: int = 0
    # How this model is asked to reason, declared by the operator. `None` means
    # it is not asked at all, which is right both for a model without the
    # capability and for one whose default is already correct.
    #
    # Carried here rather than resolved at the edge because the *shape* is per
    # model and the three shapes share no field: a flat `reasoning_effort`, that
    # string under a `thinking` envelope, or a boolean inside
    # `chat_template_kwargs`. Normalising them into one would mean lying about
    # two of the three.
    reasoning: ReasoningControl | None = None
    # What a million tokens costs, for pricing a turn the provider does not
    # price itself. Zero on a local model, which is the whole point of showing
    # it: free on my GPU against paid, someone else's.
    #
    # `cached_input_cost_per_m` is None when the provider does not discount
    # cache hits, rather than 0 — a provider without the discount is not one
    # whose cache hits are free.
    input_cost_per_m: float = 0.0
    cached_input_cost_per_m: float | None = None
    output_cost_per_m: float = 0.0


@dataclass(frozen=True, slots=True)
class Chunk:
    """One streamed piece of a model response.

    ``kind`` is ``content`` | ``reasoning`` | ``tool_call`` | ``usage`` |
    ``finish``. Keeping the stream in one shape means the event log records what
    arrived, not a provider-specific structure.
    """

    kind: str
    data: dict[str, Any] = field(default_factory=dict)


class ModelGateway(Protocol):
    """The model, as the harness sees it.

    Neuman owns this rather than delegating to a proxy framework (docs/decisions.md D20), so
    the port is small: stream a request, get chunks.
    """

    def stream(
        self,
        request: dict[str, Any],
        *,
        endpoint: str | None = None,
        api_key_file: str | None = None,
    ) -> AsyncIterator[Chunk]:
        """Stream a chat completion.

        ``endpoint`` overrides the gateway's configured base URL for this call,
        which is how one appliance reaches two providers: the catalog carries a
        per-model endpoint, and routing is configuration rather than a bundled
        service (D20).

        ``api_key_file`` is that provider's credential, and has to travel with
        the endpoint for the same reason. When only the endpoint was per-call,
        every provider got whichever single key the gateway was constructed
        with, so a second provider received the first one's token.
        """


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    """What running one tool produced."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False
    wall_ms: int = 0
    truncated: bool = False
    structured: dict[str, Any] | None = None
    # Paths, on the client, where output too large to stream was written.
    # `None` when the stream fit the preview. The model reads them with its own
    # tools, which is why they are ordinary paths rather than a server-side
    # handle.
    persisted_out: str | None = None
    persisted_err: str | None = None
    # Set when the process outlived its wait and continues as a background job
    # on the client (D98): there is no exit status yet, and `exit_code` means
    # nothing. `lifetime_ms` is when the client will kill it.
    job_id: str | None = None
    lifetime_ms: int | None = None

    @property
    def ok(self) -> bool:
        return (
            self.exit_code == 0
            and not self.timed_out
            and not self.cancelled
            and self.job_id is None
        )


class ToolExecution(Protocol):
    """Dispatch to the acting human's client.

    The harness hands over an already-decided dispatch; the client runs it as
    the user and reports what happened. It never decides, and there is nothing
    left for it to narrow: the controls are the capability gate and the approval
    verdict, both settled before this port is called (SPEC section 5).
    """

    async def dispatch(
        self,
        *,
        user: str,
        dispatch_id: str,
        tool: str,
        args: dict[str, Any],
        wait_ms: int,
        machine: str | None = None,
        lifetime_ms: int | None = None,
        working_dir: str | None = None,
        surfaces: dict[str, list[str]] | None = None,
        session_id: str | None = None,
        branch_id: str | None = None,
    ) -> DispatchOutcome:
        """Run one tool. `lifetime_ms` absent means the wait: no job.

        `machine` is the place the dispatch runs (D112), and every caller that
        acts for a conversation passes the branch's. `None` is a caller that
        names none — a task run, or a browse request with no branch — and the
        hub reads it as the account's most recently connected machine, which is
        what the appliance did before machines existed. It disappears as each
        of those callers is given a machine of its own.

        `session_id` goes to the client, which tags a job with it; `branch_id`
        stays here, so a job's end can be told to the branch that started it.
        A dispatch with no session can never become a job.
        """

    async def cancel(self, *, user: str, dispatch_id: str, machine: str | None = None) -> None:
        """Cancel an in-flight dispatch: the client kills the process group."""

    def connected(self, user: str, machine: str | None = None) -> bool:
        """Whether a dispatch for this user — and machine, if named — has anywhere to go."""

    def newest_machine(self, user: str) -> str | None:
        """The machine this account most recently connected from, if any.

        For the two transitional callers that name none: a branch predating
        machines, which adopts it once, and a browse request with no branch. A
        branch that already names a machine never consults it.
        """

    async def register(self, connection: Any) -> None:
        """Take over a machine's channel.

        Called by `/client` once a connection has authenticated and said what it
        is, so the port the harness dispatches through is the same thing the
        transport registers with. One connection per (account, machine); a
        second connection for the same machine replaces the first, and one from
        a different machine is a second channel and is kept (D112).
        """

    def unregister(self, connection: Any) -> None:
        """Drop a connection that closed, if it is still the live one.

        Synchronous, unlike `register`: closing a socket is something the caller
        has to await, and taking a dead channel out of a dict is not.
        """

    def available(self, user: str, machine: str | None = None) -> frozenset[str] | None:
        """The tool names the connected client said it can run.

        The core intersects this with the catalog and the account's grant before
        advertising, so a model is not told about a tool the machine in front of
        it has no program for. `None` means there is no client to ask, which the
        prompt builder reads as unrestricted: the dispatch that follows is where
        a missing client is reported, and a sentence about a machine that is not
        connected would be worse than the granted set.
        """

    def home(self, user: str, machine: str | None = None) -> str | None:
        """Where `~` means on the machine the connected client runs on.

        Policy expands `$HOME` into the account's own directories, and the
        homedir the core can look up is its own host's — a different directory
        as soon as the client is elsewhere, which makes a `$HOME` grant name
        paths that do not exist where the tool runs (N145). `None` means no
        client is connected; the caller falls back.
        """

    def drops(self, user: str, machine: str | None = None) -> str | None:
        """Where the connected client keeps files dropped on its window (D102).

        An attachment inside it is accepted without a policy grant. `None`
        means no client is connected, and such an attachment is refused.
        """


class EventRecord(Protocol):
    @property
    def seq(self) -> int: ...

    @property
    def ts(self) -> datetime: ...

    @property
    def kind(self) -> str: ...

    @property
    def message_id(self) -> str | None: ...

    @property
    def payload(self) -> dict[str, Any]: ...


class EventRepository(Protocol):
    """The append-only log of one branch."""

    async def append(
        self,
        *,
        branch_id: str,
        kind: str,
        payload: dict[str, Any],
        message_id: str | None = None,
        redactions: list[dict[str, Any]] | None = None,
    ) -> EventRecord:
        """Append one event, assigning the next sequence number atomically."""

    async def read(
        self, branch_id: str, *, kinds: Sequence[str] | None = None
    ) -> Sequence[EventRecord]:
        """Every event, or only those whose `kind` is named. Oldest first."""

    async def last_of_kind(self, branch_id: str, kind: str) -> EventRecord | None: ...


@dataclass(frozen=True, slots=True)
class Swept:
    """What one image collection removed: rows, and the bytes behind them.

    `bytes` is decoded picture size, not the base64 that travelled; the point of
    reporting it is to say how much the store shrank, and the wire encoding is
    not what was stored.
    """

    images: int
    bytes: int


@dataclass(frozen=True, slots=True)
class StoredImage:
    """An image, content-addressed by the SHA-256 of its bytes.

    The bytes live here and nowhere else. A `tool_result` event names the hash,
    the derived history names the hash, and both the request builder and the
    image route fetch the one copy by hash. That is what keeps a screenshot out
    of every event that references it — the session-growth bug (#34268) in
    miniature, where one picture was re-stored in every compaction snapshot and
    every request it appeared in.
    """

    sha256: str
    media_type: str
    data: bytes


class ImageRepository(Protocol):
    """Content-addressed image bytes.

    `put` is idempotent by content: the same picture written twice is one row,
    so re-reading a file costs nothing after the first time. It also records
    that `session_id` references the image, in the same transaction as the
    bytes — the store's roots (`session_image_refs`), so collection can tell a
    shared image from an orphaned one. `get` returns None for a hash that is
    not there, which a caller reports as a dropped image rather than a crash.
    """

    async def put(self, *, session_id: str, media_type: str, data: bytes) -> StoredImage: ...

    async def get(self, sha256: str) -> StoredImage | None: ...


@dataclass(frozen=True, slots=True)
class Reservation:
    """Tokens held for an in-flight model call."""

    id: str
    account_id: str
    tokens: int


class UsageRepository(Protocol):
    """Daily token accounting (docs/decisions.md D20: our tables, our enforcement)."""

    async def reserve(
        self, *, account_id: str, tokens_daily: int | None, requested: int
    ) -> Reservation | None:
        """Reserve up to ``requested`` tokens, or None when the budget is spent."""

    async def settle(self, reservation: Reservation, used: int) -> None: ...

    async def release(self, reservation: Reservation) -> None: ...

    async def sweep_stale(self, *, older_than_s: float) -> int:
        """Release reservations orphaned by a crash. Returns how many."""
