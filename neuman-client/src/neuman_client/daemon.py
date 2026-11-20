"""Connect to a Neuman core and run the dispatches it sends.

The client opens one authenticated WebSocket and holds it. It says hello with
the home directory on this machine and the tool names it can run; the core
answers with the account the session resolved and then pushes dispatches down
the same socket. Each dispatch runs through the executor as the local account
that runs the client, and its output streams back in order.

The connection is the client's whole job. It holds no policy and makes no
decision: a dispatch that arrives has already been gated and approved, and the
only questions the client answers are "can I run this name" and "what happened".

A dispatch still running at its wait may become a background job (D98,
`jobs.py`): the dispatch ends with `detached`, the process goes on, and how it
ends is reported later as `job_settled`. `job_output` and `job_kill` are
answered here rather than run, because the jobs they read are held here.

A dropped connection cancels what is running, jobs included. A tool that kept
executing after the core lost sight of it would be an effect with no record and
no approver waiting on it, so every in-flight dispatch and every job is killed
when the socket closes and the process is reaped before the daemon reconnects.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Iterable
from typing import Any

from .executor import Executor, Outcome
from .hosts import HostManager
from .jobs import Job, JobRegistry, job_kill, job_output
from .tools import MissingProgram, PathOutsideSurface, ToolHost
from .transport import Connection, Transport

__all__ = ["RPC_VERSION", "ClientDaemon", "hello_payload"]

_log = logging.getLogger("neuman.client.daemon")

RPC_VERSION = 1

_RECONNECT_MIN_S = 1.0
_RECONNECT_MAX_S = 30.0


class _Out:
    """One dispatch's messages, with the sequence the core checks for gaps."""

    def __init__(self, connection: Connection, lock: asyncio.Lock, dispatch_id: str) -> None:
        self._connection = connection
        self._lock = lock
        self._dispatch_id = dispatch_id
        self._sequence = 0

    async def _stream(self, message: dict[str, Any]) -> None:
        # The sequence is assigned and the message sent under one lock, so two
        # pump tasks cannot interleave an assignment with a send and produce a
        # gap that never happened.
        async with self._lock:
            envelope = {
                "rpc_version": RPC_VERSION,
                "dispatch_id": self._dispatch_id,
                "sequence": self._sequence,
                **message,
            }
            self._sequence += 1
            await self._connection.send(envelope)

    async def started(self, pid: int | None) -> None:
        await self._stream({"type": "started", "pid": pid})

    async def chunk(self, stream: str, data: bytes) -> None:
        await self._stream({"type": stream, "chunk": base64.b64encode(data).decode("ascii")})

    async def result(self, outcome: Outcome) -> None:
        await self._stream(
            {
                "type": "result",
                "exit_code": outcome.exit_code,
                "wall_ms": outcome.wall_ms,
                "timed_out": outcome.timed_out,
                "cancelled": outcome.cancelled,
                "truncated": outcome.truncated,
                "persisted_out": outcome.persisted_out,
                "persisted_err": outcome.persisted_err,
            }
        )

    async def detached(self, job: Job, outcome: Outcome) -> None:
        await self._stream(
            {
                "type": "detached",
                "job_id": job.id,
                "wall_ms": outcome.wall_ms,
                "lifetime_ms": job.background.lifetime_ms,
                "persisted_out": outcome.persisted_out,
                "persisted_err": outcome.persisted_err,
            }
        )

    async def answer(self, exit_code: int, text: str) -> None:
        """A daemon tool's whole reply: no process, so no pid, and one chunk.

        `started` is already on the wire by the time this runs (see
        `_run_dispatch`): the work it answers can take minutes, and the core's
        D15 deadline is measured until that frame.
        """

        if text:
            await self.chunk("stdout", text.encode("utf-8"))
        await self._stream(
            {
                "type": "result",
                "exit_code": exit_code,
                "wall_ms": 0,
                "timed_out": False,
                "cancelled": False,
                "truncated": False,
                "persisted_out": None,
                "persisted_err": None,
            }
        )

    async def error(self, code: str, message: str) -> None:
        async with self._lock:
            await self._connection.send(
                {
                    "type": "error",
                    "rpc_version": RPC_VERSION,
                    "dispatch_id": self._dispatch_id,
                    "code": code,
                    "message": message,
                }
            )


class _Sink:
    """Adapts the executor's sink protocol onto one dispatch's messages."""

    def __init__(self, out: _Out) -> None:
        self._out = out

    async def started(self, pid: int) -> None:
        await self._out.started(pid)

    async def output(self, stream: str, data: bytes) -> None:
        await self._out.chunk(stream, data)


def hello_payload(*, home: str, drops: str, tools: Iterable[str]) -> dict[str, Any]:
    """The handshake frame, built in one place.

    The daemon sends it, and the VM probe that drives this same path sends it —
    a second hand-constructed copy is how the core came to refuse a probe the
    client had already moved past (N145).

    `home` is where `~` means on the machine this process runs on. It is reported
    rather than looked up by the core, which can only see its own host's answer
    (N145). No account name is sent: the session names the principal, and the
    local account behind the process is the executor, not an identity claim
    (D67).

    `drops` is where this client keeps files dropped on its window. The core
    accepts an attachment inside it although no policy names it, and only
    while this client is connected (D102).
    """

    return {
        "type": "hello",
        "rpc_version": RPC_VERSION,
        "home": home,
        "drops": drops,
        "tools": sorted(tools),
    }


class ClientDaemon:
    """Runs dispatches for the session's account against one core."""

    def __init__(
        self,
        *,
        transport: Transport,
        home: str,
        drops: str,
        host: ToolHost,
        executor: Executor,
        jobs: JobRegistry | None = None,
        hosts: HostManager | None = None,
    ) -> None:
        self._transport = transport
        self._home = home
        self._drops = drops
        self._host = host
        # The tool package hosts this client starts on demand (D101). None in a
        # daemon that serves no package tool, which then refuses one.
        self._hosts = hosts
        self._executor = executor
        # Shared with the UI server when there is one: the browser's job row
        # and the model's `job_output` read the same registry, so the human can
        # stop what the model started (D98).
        self._jobs = jobs if jobs is not None else JobRegistry()

    async def run(self) -> None:
        """Serve until cancelled, reconnecting with capped backoff."""

        backoff = _RECONNECT_MIN_S
        while True:
            try:
                await self._serve()
                backoff = _RECONNECT_MIN_S
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - any failure reconnects
                _log.warning("client connection lost: %s", error)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_MAX_S)

    async def _serve(self) -> None:
        connection = await self._transport.connect()
        lock = asyncio.Lock()
        inflight: dict[str, asyncio.Event] = {}
        tasks: set[asyncio.Task[None]] = set()

        async def settled(job: Job) -> None:
            background = job.background
            try:
                async with lock:
                    await connection.send(
                        {
                            "type": "job_settled",
                            "rpc_version": RPC_VERSION,
                            "job_id": job.id,
                            "dispatch_id": job.dispatch_id,
                            "exit_code": background.exit_code or 0,
                            "wall_ms": background.wall_ms or 0,
                            "ended": background.ended or "exited",
                            "observed": job.observed,
                        }
                    )
            except Exception as error:  # noqa: BLE001 - the socket may be gone
                _log.warning("could not report job %s: %s", job.id, error)

        self._jobs.on_settled = settled
        try:
            await connection.send(
                hello_payload(
                    home=self._home,
                    drops=self._drops,
                    tools=self._host.names(),
                )
            )
            welcome = await connection.recv()
            if welcome.get("type") != "welcome":
                _log.warning("core refused the connection: %s", welcome.get("message", ""))
                return

            while True:
                message = await connection.recv()
                kind = message.get("type")
                if kind == "dispatch":
                    task = asyncio.create_task(
                        self._run_dispatch(message, connection, lock, inflight)
                    )
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                elif kind == "cancel":
                    dispatch_id = str(message.get("dispatch_id"))
                    cancel = inflight.get(dispatch_id)
                    if cancel is not None:
                        cancel.set()
                    job = self._jobs.by_dispatch(dispatch_id)
                    if job is not None:
                        job.background.kill()
                elif kind == "error":
                    _log.warning("core error: %s", message.get("message", ""))
                    return
        finally:
            # A connection the core has lost is a dispatch nobody is waiting
            # for. Kill what is running rather than leaving it as an effect with
            # no record.
            for cancel in inflight.values():
                cancel.set()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            # After the dispatches, so none can detach into a registry that has
            # already been emptied. The core records these as ended with the
            # connection; there is no socket left to tell it on.
            self._jobs.on_settled = None
            await self._jobs.kill_all()
            await connection.close()

    async def _run_dispatch(
        self,
        message: dict[str, Any],
        connection: Connection,
        lock: asyncio.Lock,
        inflight: dict[str, asyncio.Event],
    ) -> None:
        dispatch_id = str(message.get("dispatch_id", ""))
        tool = str(message.get("tool", ""))
        args = message.get("args")
        out = _Out(connection, lock, dispatch_id)
        cancel = asyncio.Event()
        inflight[dispatch_id] = cancel
        arguments = args if isinstance(args, dict) else {}
        session = message.get("session_id")
        session_id = session if isinstance(session, str) and session else None
        try:
            daemon_tool = self._host.daemon_tool(tool)
            if daemon_tool is not None:
                # Before the work, not after. A look can wait ten minutes, and
                # the core gives up at five seconds without a `started` frame
                # (D15) — which would fail the dispatch and leave the model
                # believing it had lost a job it can still see.
                await out.started(None)
                wait_ms = min(
                    _int(message.get("wait_ms"), daemon_tool.wait_ms), daemon_tool.lifetime_ms
                )
                if tool == "job_output":
                    exit_code, text = await job_output(
                        self._jobs,
                        arguments,
                        session_id=session_id,
                        wait_ms=wait_ms,
                        cancel=cancel,
                    )
                elif tool == "job_kill":
                    exit_code, text = await job_kill(
                        self._jobs, arguments, session_id=session_id
                    )
                else:
                    await out.error("unknown_tool", f"this client does not run {tool}")
                    return
                await out.answer(exit_code, text)
                return

            # A package tool: check the program it needs, and have its host
            # starting before the tool dials it. Before `command`, so a missing
            # program is refused rather than spawned to fail (D101).
            host = self._host.host_for(tool)
            if host is not None:
                if self._hosts is None:
                    await out.error("spawn_failed", f"this client starts no host for {tool}")
                    return
                self._hosts.ensure(host)
            command = self._host.command(
                tool,
                arguments,
                dispatch_id=dispatch_id,
                cwd=message.get("working_dir"),
                wait_ms=message.get("wait_ms"),
                lifetime_ms=message.get("lifetime_ms"),
                surfaces=message.get("surfaces"),
            )
            if command is None:
                await out.error("unknown_tool", f"this client does not run {tool}")
                return
            outcome = await self._executor.run(
                command,
                _Sink(out),
                cancel=cancel,
                # Only a dispatch a session owns can become a job: a job is
                # found by its session, and one with none could never be
                # looked at or stopped.
                admit=self._jobs.admit if session_id is not None else None,
            )
            if outcome.background is not None:
                job = self._jobs.add(
                    outcome.background,
                    dispatch_id=dispatch_id,
                    session_id=session_id or "",
                    tool=tool,
                    args=arguments,
                )
                await out.detached(job, outcome)
                return
            await out.result(outcome)
        except MissingProgram as missing:
            # Named, with the program and where to get it, so the model can tell
            # the person what to install instead of guessing at a failure.
            await out.error("missing_program", str(missing))
        except PathOutsideSurface as refused:
            # The core decided the account may reach somewhere else. Reporting
            # it as a named refusal is what lets the model ask for the right
            # path instead of retrying the same one.
            await out.error("outside_surface", str(refused))
        except OSError as error:
            # The process never ran, which is a different thing from running and
            # failing: there is no exit status to report.
            await out.error("spawn_failed", str(error))
        except Exception as error:  # noqa: BLE001 - report, never die on one dispatch
            _log.exception("dispatch %s failed", dispatch_id)
            await out.error("internal", str(error))
        finally:
            inflight.pop(dispatch_id, None)


def _int(value: object, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default
