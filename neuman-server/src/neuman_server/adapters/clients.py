"""The core's end of the dispatch RPC: connected clients, over the session surface.

A client opens a WebSocket to `/client` and authenticates with the same session
identity the API uses. The connection is then this hub's channel: the core writes
dispatches down it and reads results back. Identity is the session's, checked by
the route before the connection is registered here, so the hub itself is
transport-shaped and holds no authority of its own.

The core is the RPC *client*: it sends dispatches and collects results. It holds
one connection per **(account, machine)** — a machine being the named place a
dispatch runs (D112) — and a dispatch for a machine with no connection is
`unavailable` — reported as an error, never queued and never run somewhere
else, because there is nowhere else this architecture runs a tool.

It used to hold one per account, and a second connection replaced the first.
That was right while a person had one computer and wrong the moment they had
two: opening a laptop disconnected the desktop, killed its jobs and reported
them as ended, and left a turn mid-dispatch with nowhere to go. Keying by
machine is what makes two computers two channels instead of one fight.

**Background jobs are tracked here** (D98). A dispatch that ends `detached` is
a process still running on the client; the connection keeps which session and
branch started it, and hands its end — a `job_settled` frame, or the connection
closing under it — to `on_job_settled`, which records it. The connection is the
right owner because the client's rule is the same: jobs live exactly as long as
the socket.

**Sequence is checked, not ignored** (D26). Every streamed message carries a
monotonic `sequence`, and a gap means a chunk was lost between the tool and here.
Accepting it would hand the model truncated output that looks complete, which is
the one failure a reader of this stream must never pass on, so a gap fails the
dispatch.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol

from ..decision.harness.ports import DispatchOutcome
from ..foundation.logging import get_logger

__all__ = [
    "MIN_CLIENT_RELEASE",
    "MIN_CLIENT_RPC_VERSION",
    "RPC_VERSION",
    "SUPPORTED_RPC_VERSIONS",
    "Channel",
    "ClientConnection",
    "ClientError",
    "ClientHub",
    "ClientUnavailable",
    "Job",
    "JobSettled",
]

_log = get_logger("neuman.server.adapters.clients")

# The dispatch shape this build writes, and every older one it still reads and
# can still produce. A set rather than a number because the two halves update on
# different days: an appliance update must not strand a client that has not been
# updated yet (D111). The handshake settles on one per connection and every
# frame on that socket carries it, so a client one shape behind keeps working
# instead of reconnecting forever against a close it cannot read.
RPC_VERSION = 2
SUPPORTED_RPC_VERSIONS = frozenset({RPC_VERSION})

# The oldest client this build accepts. It moves with the set — dropping a shape
# is what raises the floor — and the release beside it is the same fact in the
# words a person is told to install. Keep the pair in step: a release that
# speaks a shape this build no longer accepts is below the floor whatever its
# number says. It is a literal rather than this build's version because the
# floor names the release that *first* spoke the minimum shape, which outlives
# the build that raised it.
#
# 0.1.0 spoke shape 1, which had no machine. There is no installed base to
# carry, and keeping a shape whose hello cannot name the machine would keep a
# code path that invents one — which is the ambiguity this release removes. So
# the set has one member and the floor is the release that speaks it.
MIN_CLIENT_RPC_VERSION = min(SUPPORTED_RPC_VERSIONS)
MIN_CLIENT_RELEASE = "0.1.1"

# How many unclaimed settlements to hold. One is the race this exists for; a
# handful covers a batch of jobs that all ended as their dispatches landed, and
# a bound keeps a client that names unknown ids from growing a dict.
_MAX_UNCLAIMED = 16


class ClientError(RuntimeError):
    """The client rejected or failed the dispatch."""


class ClientUnavailable(ClientError):
    """No client is connected for that account."""


@dataclass(frozen=True, slots=True)
class Job:
    """A background job on a client, as the core knows it."""

    job_id: str
    dispatch_id: str
    tool: str
    session_id: str
    branch_id: str | None
    # The machine whose client holds the process. Carried so the settlement the
    # core records names the computer the job ran on, and so two machines'
    # jobs of one session can be told apart in the record.
    machine: str = ""
    # Where its stdout goes on the client, for the notice when the connection
    # takes the job with it and `job_output` can no longer reach it.
    persisted_out: str | None = None


# What the core does when a job ends: the account, the job, and the frame's
# fields — or `ended="disconnected"` when the connection went first.
JobSettled = Callable[[str, Job, dict[str, Any]], Awaitable[None]]


class Channel(Protocol):
    """Where a connection's messages go. The WebSocket route implements this."""

    async def send(self, message: dict[str, Any]) -> None: ...

    async def close(self) -> None: ...


class _Accumulator:
    """Collects a dispatch's streamed output, up to a backstop."""

    def __init__(self, max_output_bytes: int) -> None:
        self.stdout = bytearray()
        self.stderr = bytearray()
        self._max = max_output_bytes
        self.truncated = False

    def add(self, stream: str, chunk: str) -> None:
        try:
            decoded = base64.b64decode(chunk, validate=True)
        except (ValueError, TypeError):
            # The contract says base64. Losing a chunk must not lose the whole
            # dispatch, but a reader that silently accepted this would be
            # presenting corrupt output as the tool's.
            _log.warning("client sent a non-base64 chunk", fields={"stream": stream})
            return

        target = self.stdout if stream == "stdout" else self.stderr
        room = self._max - len(target)
        if room <= 0:
            self.truncated = True
            return
        if len(decoded) > room:
            target.extend(decoded[:room])
            self.truncated = True
        else:
            target.extend(decoded)


class ClientConnection:
    """One connected client, and the dispatches riding on it.

    ``account`` is the principal the session resolved. The local account that
    runs the tools is not part of this object: it is where the reach comes
    from, not an identity the core can see (D67).
    """

    def __init__(
        self,
        channel: Channel,
        *,
        account: str,
        machine: str,
        tools: frozenset[str],
        home: str,
        drops: str,
        rpc_version: int = RPC_VERSION,
        release: str | None = None,
    ) -> None:
        self._channel = channel
        self.account = account
        # The named place this socket runs dispatches for (D112). Scoped to the
        # account by `ClientHub`'s key, so it is a label rather than an identity
        # claim: the session already named the principal.
        self.machine = machine
        self.tools = tools
        # When this socket said hello. The only caller that needs it is the
        # fallback below, which has to pick one of an account's machines when
        # nobody named one; "the most recent" has to mean it.
        self.connected_at = monotonic()
        # The shape this connection settled on in the handshake, out of
        # `SUPPORTED_RPC_VERSIONS`. Every frame written down this socket carries
        # it, so one build can serve a client one shape behind without guessing
        # what that client can read (D111).
        self.rpc_version = rpc_version
        # The client's release, when it said. None is "too old to say", which is
        # reported as unknown rather than refused: the gate is the shape, and a
        # reporting gap is not a reason to strand a working client.
        self.release = release
        # Where `~` means on the machine at the other end. Reported rather than
        # looked up: the account's home on this host is a different directory as
        # soon as the client is not on this host (N145).
        self.home = home
        # Where that client keeps files dropped on its window: an attachment
        # inside it is accepted without a policy grant (D102).
        self.drops = drops
        self._pending: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._write_lock = asyncio.Lock()
        self._closed = asyncio.Event()
        self.jobs: dict[str, Job] = {}
        # A `job_settled` can be read in the same batch as the `detached` that
        # registers its job. Held by job id until the dispatch that is about to
        # add it lands, because dropping it would lose the only record of how
        # the job ended.
        self._unclaimed: dict[str, dict[str, Any]] = {}
        self.on_job_settled: JobSettled | None = None
        self._recording: set[asyncio.Task[None]] = set()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    async def send(self, message: dict[str, Any]) -> None:
        async with self._write_lock:
            await self._channel.send(message)

    def deliver(self, message: dict[str, Any]) -> None:
        """Route one message from the client to the dispatch waiting for it."""

        if message.get("type") == "job_settled":
            job_id = str(message.get("job_id"))
            job = self.jobs.pop(job_id, None)
            if job is None:
                # Before the dispatch's `detached` has been taken off the
                # queue. Hold it; `run` claims it when it registers the job.
                if len(self._unclaimed) >= _MAX_UNCLAIMED:
                    # The oldest is the least likely to be claimed; the cap
                    # exists so a client naming unknown ids cannot grow this.
                    self._unclaimed.pop(next(iter(self._unclaimed)))
                self._unclaimed[job_id] = message
                return
            self._record(job, message)
            return

        queue = self._pending.get(message.get("dispatch_id"))
        if queue is not None:
            queue.put_nowait(message)
        # A message for no pending dispatch is dropped: it is either a duplicate
        # of something already terminal, or a client bug, and neither is a reason
        # to fail a different dispatch.

    def disconnect(self) -> None:
        """The route saw the socket go. Wake every waiter with a name they can act on."""

        self._closed.set()
        # The client kills its jobs when the socket goes, and can no longer say
        # so; this side knows the rule and records it on the client's behalf.
        for job in list(self.jobs.values()):
            self._record(job, {"ended": "disconnected", "observed": False})
        self.jobs.clear()
        self._unclaimed.clear()
        for queue in self._pending.values():
            queue.put_nowait(
                {
                    "type": "error",
                    "dispatch_id": None,
                    "code": "unavailable",
                    "message": "client disconnected",
                }
            )

    async def close(self) -> None:
        await self._channel.close()

    def _record(self, job: Job, message: dict[str, Any]) -> None:
        if self.on_job_settled is None:
            return
        task = asyncio.create_task(self.on_job_settled(self.account, job, message))
        self._recording.add(task)
        task.add_done_callback(self._recording.discard)

    async def run(
        self,
        request: dict[str, Any],
        *,
        wait_ms: int,
        started_timeout_s: float,
        max_output_bytes: int,
        branch_id: str | None = None,
    ) -> DispatchOutcome:
        dispatch_id = str(request["dispatch_id"])
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._pending[dispatch_id] = queue
        try:
            await self.send(request)
            outcome = await self._collect(
                queue,
                dispatch_id=dispatch_id,
                wait_ms=wait_ms,
                started_timeout_s=started_timeout_s,
                max_output_bytes=max_output_bytes,
            )
        finally:
            self._pending.pop(dispatch_id, None)
        if outcome.job_id is not None:
            self.jobs[outcome.job_id] = Job(
                job_id=outcome.job_id,
                dispatch_id=dispatch_id,
                tool=str(request["tool"]),
                session_id=str(request.get("session_id") or ""),
                branch_id=branch_id,
                machine=self.machine,
                persisted_out=outcome.persisted_out,
            )
            early = self._unclaimed.pop(outcome.job_id, None)
            if early is not None:
                self._record(self.jobs.pop(outcome.job_id), early)
        return outcome

    async def _collect(
        self,
        queue: asyncio.Queue[dict[str, Any]],
        *,
        dispatch_id: str,
        wait_ms: int,
        started_timeout_s: float,
        max_output_bytes: int,
    ) -> DispatchOutcome:
        accumulator = _Accumulator(max_output_bytes)
        started = False
        # Before `started`, a short deadline (D15). After it, the tool's own,
        # plus slack for the result to arrive after the client enforces its
        # deadline.
        deadline = started_timeout_s
        expected_sequence = 0

        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=deadline)
            except TimeoutError as exc:
                if not started:
                    raise ClientUnavailable(
                        f"client did not start dispatch {dispatch_id} within {started_timeout_s}s"
                    ) from exc
                raise ClientError(
                    f"client stopped responding during dispatch {dispatch_id}"
                ) from exc

            kind = message.get("type")

            if kind == "error":
                raise ClientError(
                    f"client error [{message.get('code', 'unknown')}]: {message.get('message', '')}"
                )

            if kind in ("started", "stdout", "stderr", "result", "detached"):
                # A gap means output was lost in transit. Failing the dispatch is
                # the only safe reading (D26).
                sequence = message.get("sequence")
                if not isinstance(sequence, int) or isinstance(sequence, bool):
                    raise ClientError(
                        f"client sent a {kind} message without a sequence number "
                        f"during {dispatch_id}"
                    )
                if sequence != expected_sequence:
                    raise ClientError(
                        f"client output gap during {dispatch_id}: expected sequence "
                        f"{expected_sequence}, got {sequence}"
                    )
                expected_sequence += 1

            if kind == "started":
                started = True
                deadline = (wait_ms / 1000) + 30

            elif kind in ("stdout", "stderr"):
                accumulator.add(kind, str(message.get("chunk", "")))

            elif kind == "result":
                return DispatchOutcome(
                    exit_code=int(message.get("exit_code", -1)),
                    stdout=accumulator.stdout.decode("utf-8", errors="replace"),
                    stderr=accumulator.stderr.decode("utf-8", errors="replace"),
                    timed_out=bool(message.get("timed_out")),
                    cancelled=bool(message.get("cancelled")),
                    wall_ms=int(message.get("wall_ms", 0)),
                    truncated=bool(message.get("truncated")) or accumulator.truncated,
                    structured=message.get("structured"),
                    persisted_out=message.get("persisted_out"),
                    persisted_err=message.get("persisted_err"),
                )

            elif kind == "detached":
                return DispatchOutcome(
                    exit_code=0,
                    stdout=accumulator.stdout.decode("utf-8", errors="replace"),
                    stderr=accumulator.stderr.decode("utf-8", errors="replace"),
                    wall_ms=int(message.get("wall_ms", 0)),
                    truncated=accumulator.truncated,
                    persisted_out=message.get("persisted_out"),
                    persisted_err=message.get("persisted_err"),
                    job_id=str(message.get("job_id")),
                    lifetime_ms=int(message.get("lifetime_ms", 0)) or None,
                )

            # Any other message is ignored: the contract is append-only within a
            # version, so an unknown kind is a newer client rather than a bug.

    async def cancel(self, dispatch_id: str) -> None:
        await self.send(
            {"type": "cancel", "rpc_version": self.rpc_version, "dispatch_id": dispatch_id}
        )


class ClientHub:
    """Implements `decision.harness.ports.ToolExecution` over connected clients."""

    def __init__(
        self,
        *,
        started_timeout_s: float = 5.0,
        # A backstop, not the cap: the client enforces the manifest's
        # `max_output_bytes` and reports whether it bit. This only stops a client
        # that never stops.
        max_output_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        self._started_timeout = started_timeout_s
        self._max_output_bytes = max_output_bytes
        # (account, machine) -> the connection for that pair. The machine is part
        # of the key because a person may be at more than one computer at once,
        # and a channel per account meant the second one evicted the first.
        self._clients: dict[tuple[str, str], ClientConnection] = {}
        # Set by the composition, which owns the stores a job's end is
        # recorded in. Unset, a job's end goes unrecorded — tests that do not
        # care, and nothing else.
        self.on_job_settled: JobSettled | None = None

    async def register(self, connection: ClientConnection) -> None:
        """Take over a machine's channel. One client per (account, machine).

        A second connection *for the same machine* still replaces the first:
        that is a reconnect whose old socket the network has not noticed yet,
        and leaving dispatches to race between two sockets of one computer is
        how a result arrives on the wrong one. A connection from a *different*
        machine is a second channel and is kept.
        """

        key = (connection.account, connection.machine)
        previous = self._clients.get(key)
        connection.on_job_settled = self.on_job_settled
        self._clients[key] = connection
        if previous is not None:
            previous.disconnect()
            await previous.close()
        _log.info(
            "client connected",
            fields={
                "account": connection.account,
                "machine": connection.machine,
                "tools": len(connection.tools),
            },
        )

    def unregister(self, connection: ClientConnection) -> None:
        """Drop a connection that closed, if it is still the live one."""

        key = (connection.account, connection.machine)
        if self._clients.get(key) is connection:
            del self._clients[key]

    async def drop(self, user: str) -> None:
        """Close every channel this account holds, on every machine.

        Called when the account is disabled or its password is reset: a
        long-lived socket authenticated before the change must not keep
        receiving dispatches after it. Every machine, because the revocation is
        the account's and leaving one computer's socket open would be the hole
        the revocation was made against.
        """

        for key in [key for key in self._clients if key[0] == user]:
            connection = self._clients.pop(key, None)
            if connection is not None:
                connection.disconnect()
                await connection.close()

    def connected_users(self) -> list[str]:
        """Every account with a client connected now, by name.

        Kept beside `connected_clients` because the administration page's shape
        shipped: a client older than this build renders this list, and a server
        that replaced it with objects would show that page `[object Object]`
        exactly when someone was looking for the versions (D111).
        """

        return sorted({connection.account for connection in self._live()})

    def connected_clients(self) -> list[dict[str, Any]]:
        """Every machine connected now, and what that machine is.

        One row per (account, machine) rather than one per account, because the
        question an operator asks of a shared box — "who is on the dgx" — is
        unanswerable from a list of names once a person may be on two computers
        at once. The versions travel for D111's reason: the question before an
        update whose floor moved is which machines are behind.
        """

        return [
            {
                "account": connection.account,
                "machine": connection.machine,
                "release": connection.release,
                "rpc": connection.rpc_version,
            }
            for connection in sorted(self._live(), key=lambda c: (c.account, c.machine))
        ]

    def _live(self) -> list[ClientConnection]:
        return [c for c in self._clients.values() if not c.closed]

    def _connection(self, user: str, machine: str) -> ClientConnection | None:
        """The channel for one machine, or None.

        There is no "whichever client is connected" fallback, and its removal is
        the point of D112. That fallback was the bug: a conversation created on
        one computer ran on another because the caller had not named a machine,
        and the hub answered with whatever had connected last.
        """

        connection = self._clients.get((user, machine))
        return connection if connection is not None and not connection.closed else None

    def connected(self, user: str, machine: str) -> bool:
        """Whether a dispatch for this user on this machine has anywhere to go."""

        return self._connection(user, machine) is not None

    def available(self, user: str, machine: str) -> frozenset[str] | None:
        """The tools the connected client advertised, or None with no client.

        Used to narrow the advertisement, so the other direction of the
        handshake is closed too: the client already tells the core what it can
        run, and a model should not be offered a tool the machine in front of it
        has no program for. A headless machine is the ordinary case: it runs
        `shell` and `fs_read` and has no browser, and this is what says so
        without a policy of its own.
        """

        connection = self._connection(user, machine)
        return None if connection is None else connection.tools

    def home(self, user: str, machine: str) -> str | None:
        """Where `~` means on that machine, or None with no client.

        None is not an error: the HTTP surfaces stay reachable while a daemon is
        reconnecting. There is no fallback, because an account is not a host
        account (D67) and this process has no home of its own to offer; a
        `$HOME` grant reaches nothing until a client says where it is.
        """

        connection = self._connection(user, machine)
        return None if connection is None else connection.home

    def drops(self, user: str, machine: str) -> str | None:
        """That machine's drops directory, or None with no client (D102)."""

        connection = self._connection(user, machine)
        return None if connection is None else connection.drops

    async def dispatch(
        self,
        *,
        user: str,
        dispatch_id: str,
        tool: str,
        args: dict[str, Any],
        wait_ms: int,
        machine: str,
        lifetime_ms: int | None = None,
        working_dir: str | None = None,
        surfaces: dict[str, list[str]] | None = None,
        session_id: str | None = None,
        branch_id: str | None = None,
    ) -> DispatchOutcome:
        connection = self._connection(user, machine)
        if connection is None:
            raise ClientUnavailable(f"{user} has no client connected on {machine!r}")
        if tool not in connection.tools:
            # The handshake's whole purpose: asking would be a round trip that
            # ends in the same answer, and the core is the one that should have
            # known better.
            raise ClientUnavailable(f"the client for {user} does not run {tool}")
        if surfaces is None:
            # The client checks a path-taking tool against these, and the only
            # thing worse than refusing is sending a dispatch with nothing to
            # check against.
            raise ClientError("a dispatch without surfaces has nothing to check paths against")

        request: dict[str, Any] = {
            "type": "dispatch",
            "rpc_version": connection.rpc_version,
            "dispatch_id": dispatch_id,
            # The machine the core believes this socket is. The client refuses
            # a dispatch naming another one, so a routing bug here is a refusal
            # at the far end rather than a command run on the wrong computer.
            "machine": connection.machine,
            "tool": tool,
            "args": args,
            "wait_ms": wait_ms,
            "lifetime_ms": lifetime_ms or wait_ms,
            "surfaces": surfaces,
        }
        if working_dir:
            request["working_dir"] = working_dir
        if session_id:
            request["session_id"] = session_id

        return await connection.run(
            request,
            wait_ms=wait_ms,
            started_timeout_s=self._started_timeout,
            max_output_bytes=self._max_output_bytes,
            branch_id=branch_id,
        )

    async def cancel(self, *, user: str, dispatch_id: str, machine: str) -> None:
        connection = self._connection(user, machine)
        if connection is not None:
            await connection.cancel(dispatch_id)

    async def end_session_jobs(self, *, user: str, session_id: str) -> list[str]:
        """Kill every job a session started, on every machine, returning their ids.

        For a session that is over and cannot follow them — a task's agent
        step. The client kills each by the dispatch it began as, and each end
        is recorded as it arrives, as any other job's is. Every machine because
        one conversation can have left work running on more than one computer,
        and a job that outlives its session is an effect with nobody watching.
        """

        ended: list[str] = []
        for connection in self._live():
            if connection.account != user:
                continue
            jobs = [job for job in connection.jobs.values() if job.session_id == session_id]
            for job in jobs:
                await connection.cancel(job.dispatch_id)
                ended.append(job.job_id)
        return ended
