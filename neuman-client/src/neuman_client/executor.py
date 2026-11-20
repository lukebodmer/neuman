"""Run one tool as the user, on the user's own machine.

Start a process, forward its output, enforce the deadline, and kill what is
still running when asked — or, when the dispatch allows it, hand a process that
outlives its wait to the caller as a background job (D98). The process runs as the human and reaches exactly
what the human reaches: there is no confinement below them (SPEC section 5).

Three rules, each of which was learned expensively and none of which changes
with the machine:

* **Drain both pipes, always.** Reading stdout to EOF before reading stderr
  deadlocks a tool that writes more than a pipe buffer to each (gotchas N14).
* **Enforce the deadline before waiting for the pipes.** Joining the readers
  first waits out the tool instead of the deadline (gotchas N15).
* **Kill the process group, not the process.** A child that calls `setsid()`
  leaves the group, and killing the leader alone orphans it. On the client there
  is no cgroup to fall back on, so a descendant that calls `setsid()` itself
  escapes the group and outlives the kill — out of reach, and said so in
  `security-model.md`'s "Not an OS sandbox" rather than hidden here.

Two deadlines, not one (D98). `wait_ms` is how long the dispatch blocks;
`lifetime_ms` is how long the process may live. At the wait, a process with
lifetime left becomes a `Background` the caller owns, and its output goes on to
the spill files rather than the wire. Where the two are equal it is killed at
the wait, which is what every tool that should not outlive its caller declares.

Output is bounded in one of two ways, chosen by the manifest's `persist_output`
(D42). A persisting tool forwards a small preview of each stream; once a stream
outgrows it, the whole stream goes to a file the model reads back, and
`max_output_bytes` is the ceiling of that file. An inline tool bounds its own
output by the caller's arguments, so it forwards up to `max_output_bytes` and
writes no file. Both numbers are properties of the manifest, so the client is
handed them with the dispatch rather than deciding them.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

__all__ = ["Background", "Command", "Executor", "Outcome", "Sink"]

# One read from a pipe. Larger than a pipe buffer (64 KiB) so a full buffer
# drains in a single pass rather than a syscall per 4 KiB.
_CHUNK = 65536

# How long to keep draining after the process is gone. A descendant that escaped
# the process group can hold the pipe open past its parent's death; waiting
# forever would turn a kill into a hang, which is the failure the deadline exists
# to prevent.
_DRAIN_GRACE_S = 5.0

# What a persisting tool forwards before the rest goes to a file. It used to be
# the manifest's cap, which for `web_fetch` was 1 MiB: the tool stopped itself
# just under it, so a page never spilled and every one arrived inline whole
# (N180). A build log or a test run fits in this; a web page does not, and the
# model searches the file for what it wanted.
PREVIEW_BYTES = 16 * 1024

# How much of each stream's newest output is kept in memory, whatever the file
# holds. A background job's spill file keeps the *head* up to the manifest's
# ceiling, and a build that outgrows it would otherwise leave nothing of the end
# — the part that says whether it worked — anywhere the model can see.
TAIL_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class Command:
    """One tool invocation, already gated and approved.

    `name` is used to spell the spill files, so the caller makes it unique
    (``tool-dispatch_id``) — the model is handed that path and reads it back.
    `env` replaces the environment entirely when given; `None` inherits the
    client daemon's, which is the user's own.

    `lifetime_ms` greater than `wait_ms` is what lets the run become a job; the
    caller still decides at the wait whether there is room for one.
    """

    argv: tuple[str, ...]
    name: str = "tool"
    cwd: str | None = None
    env: dict[str, str] | None = None
    wait_ms: int = 120_000
    lifetime_ms: int = 120_000
    max_output_bytes: int = 1024 * 1024
    persist_output: bool = True


@dataclass(slots=True)
class Outcome:
    """How a run ended — or, with `background` set, that it has not.

    A detached run has no exit status yet: `exit_code` is meaningless and the
    caller reads `background` instead.
    """

    exit_code: int
    wall_ms: int
    timed_out: bool
    cancelled: bool
    truncated: bool
    persisted_out: str | None = None
    persisted_err: str | None = None
    background: Background | None = None


class Sink(Protocol):
    """Where a running dispatch's events go. The RPC layer implements this."""

    async def started(self, pid: int) -> None: ...

    async def output(self, stream: str, data: bytes) -> None: ...


class _Stream:
    """Forward up to a preview; once it is crossed, spill the whole stream.

    Deliberately not a tee: a dispatch that fits in the preview writes no file at
    all, which is the common case. Once it does not fit, the file has to hold the
    stream from the beginning rather than from the overflow, because the model
    reads it as the output. With no spill path the preview is the whole bound and
    the rest is dropped. `truncated` means bytes were lost, not that a file was
    written: the model is told a spilled file was cut only when it was.

    `detach` stops forwarding for good and spills from then on, whatever the
    size: a background job's output belongs to no dispatch's stream. `lock` is
    held around a feed and its send, so a detach waits for a chunk already on
    its way to the wire rather than letting it follow the frame that closed the
    dispatch. `seen` and `tail` count and keep the newest bytes regardless,
    which is what a look at a job reads.
    """

    def __init__(self, preview: int, ceiling: int, spill: Path | None) -> None:
        self._preview = preview
        self._ceiling = ceiling
        self._spill = spill
        self._head = bytearray()
        self._handle = None
        self._written = 0
        self._detached = False
        self.truncated = False
        self.persisted: Path | None = None
        self.lock = asyncio.Lock()
        self.seen = 0
        self.tail = bytearray()

    @property
    def forwarded(self) -> int:
        """How many bytes went to the wire — what the model has been shown."""

        return len(self._head)

    @property
    def can_detach(self) -> bool:
        return self._spill is not None

    def feed(self, data: bytes) -> bytes:
        """Record `data`, returning the part of it to forward now."""

        self.seen += len(data)
        self.tail += data
        if len(self.tail) > TAIL_BYTES:
            del self.tail[: len(self.tail) - TAIL_BYTES]

        if self._handle is not None:
            self._write(data)
            return b""
        if self._detached:
            # Only reachable without a spill path, which `can_detach` rules
            # out; kept so a detached stream can never forward.
            return b""

        room = self._preview - len(self._head)
        if len(data) <= room:
            self._head += data
            return data

        forward = data[:room]
        self._head += forward
        if self._spill is None:
            self.truncated = True
            return forward
        self._open()
        self._write(data[room:])
        return forward

    def detach(self) -> None:
        """Stop forwarding; the file holds the stream from its first byte."""

        self._detached = True
        if self._handle is None:
            self._open()

    def _write(self, data: bytes) -> None:
        room = self._ceiling - self._written
        if len(data) > room:
            data = data[:room]
            self.truncated = True
        if data and self._handle is not None:
            self._handle.write(data)
            self._written += len(data)

    def _open(self) -> None:
        if self._spill is None:
            return
        self._spill.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Unbuffered writes: the file must be complete when the dispatch ends,
        # and it is written from a reader task that may be cancelled mid-stream.
        self._handle = self._spill.open("wb", buffering=0)
        self.persisted = self._spill
        self._write(bytes(self._head))

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def _kill(process: asyncio.subprocess.Process) -> None:
    """Kill the process's whole group, falling back to the process alone."""

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill()
        except ProcessLookupError:
            pass


async def _drain(readers: list[asyncio.Task[None]]) -> None:
    """Wait for the pipe readers, but not forever."""

    try:
        await asyncio.wait_for(asyncio.gather(*readers), timeout=_DRAIN_GRACE_S)
    except TimeoutError:
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)


def _status(process: asyncio.subprocess.Process) -> int:
    exit_code = process.returncode if process.returncode is not None else 0
    if exit_code < 0:
        # Signal deaths report the conventional 128+signal, because the wire
        # contract carries 0..255 and a bare negative is meaningless.
        exit_code = 128 - exit_code
    return exit_code


class Background:
    """A process that outlived its dispatch's wait and is still running.

    Owned by whoever took it (the daemon's job registry), which calls `run` to
    see it through: it waits for the process to exit or for its lifetime to
    end, kills it at the latter, and settles. `kill` is the only other way it
    ends. The pump tasks keep draining both pipes into the spill files the whole
    time, so a job never blocks on a full pipe because nobody is looking.
    """

    def __init__(
        self,
        *,
        process: asyncio.subprocess.Process,
        wait: asyncio.Task[int],
        readers: list[asyncio.Task[None]],
        out: _Stream,
        err: _Stream,
        started_at: float,
        lifetime_ms: int,
    ) -> None:
        self.pid = process.pid
        self._process = process
        self._wait = wait
        self._readers = readers
        self.out = out
        self.err = err
        self.started_at = started_at
        self.lifetime_ms = lifetime_ms
        self.ended: str | None = None
        self.exit_code: int | None = None
        self.wall_ms: int | None = None
        self.done = asyncio.Event()
        self._killed = False

    @property
    def running_ms(self) -> int:
        if self.wall_ms is not None:
            return self.wall_ms
        return int((time.monotonic() - self.started_at) * 1000)

    def kill(self) -> None:
        """Kill the process group; `run` settles it as `killed`."""

        if self.done.is_set() or self._killed:
            return
        self._killed = True
        _kill(self._process)

    async def run(self) -> None:
        """See the process through to its end. Returns once it has settled."""

        remaining = self.lifetime_ms / 1000 - (time.monotonic() - self.started_at)
        lifetime = False
        try:
            await asyncio.wait_for(asyncio.shield(self._wait), timeout=max(remaining, 0))
        except TimeoutError:
            lifetime = not self._killed
            _kill(self._process)
        finally:
            # Also reached when the owner is cancelled: the process must not
            # outlive the thing that was responsible for it.
            if self._process.returncode is None:
                _kill(self._process)
            await self._wait
            await _drain(self._readers)
            self.out.close()
            self.err.close()
            self.exit_code = _status(self._process)
            self.wall_ms = int((time.monotonic() - self.started_at) * 1000)
            self.ended = "lifetime" if lifetime else "killed" if self._killed else "exited"
            self.done.set()


class Executor:
    """Runs commands. One instance per client daemon is enough."""

    def __init__(self, *, spill_dir: str | Path | None = None) -> None:
        self._spill_dir = Path(spill_dir) if spill_dir is not None else None

    async def run(
        self,
        command: Command,
        sink: Sink,
        *,
        cancel: asyncio.Event | None = None,
        admit: Callable[[], bool] | None = None,
    ) -> Outcome:
        """Run `command` to completion, reporting to `sink`.

        At the wait, a command whose lifetime runs longer is offered to `admit`;
        a yes returns an `Outcome` carrying the still-running `Background`, a no
        (or no `admit`) kills it as a timeout. Asked only then, so a slot is
        taken by a process that needs one rather than reserved by every call.

        Raises `OSError` if the command cannot be started at all (missing
        executable, missing working directory); the RPC layer turns that into an
        error message, because a dispatch that never ran has no exit status.
        """

        started_at = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *command.argv,
            cwd=command.cwd,
            env=command.env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Its own session, so the whole tree shares a process group that can
            # be killed as one.
            start_new_session=True,
        )

        out = self._stream(command, "out")
        err = self._stream(command, "err")

        async def pump(
            reader: asyncio.StreamReader | None,
            stream: str,
            capture: _Stream,
        ) -> None:
            if reader is None:  # pragma: no cover - PIPE always gives a reader
                return
            while True:
                data = await reader.read(_CHUNK)
                if not data:
                    return
                async with capture.lock:
                    forward = capture.feed(data)
                    if forward:
                        await sink.output(stream, forward)

        readers = [
            asyncio.create_task(pump(process.stdout, "stdout", out)),
            asyncio.create_task(pump(process.stderr, "stderr", err)),
        ]

        # Before the readers have run, so `started` is the first thing the
        # server sees and its D15 deadline is measured from the spawn.
        await sink.started(process.pid)

        timed_out = False
        cancelled = False
        wait = asyncio.create_task(process.wait())
        cancellation = asyncio.create_task(cancel.wait()) if cancel is not None else None
        try:
            watched: set[asyncio.Future[Any]] = {wait}
            if cancellation is not None:
                watched.add(cancellation)
            done, _ = await asyncio.wait(
                watched,
                timeout=min(command.wait_ms, command.lifetime_ms) / 1000,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if wait in done:
                pass
            elif cancellation is not None and cancellation in done:
                cancelled = True
                _kill(process)
            elif (
                command.lifetime_ms > command.wait_ms
                and out.can_detach
                and err.can_detach
                and admit is not None
                and admit()
            ):
                for capture in (out, err):
                    async with capture.lock:
                        capture.detach()
                return Outcome(
                    exit_code=0,
                    wall_ms=int((time.monotonic() - started_at) * 1000),
                    timed_out=False,
                    cancelled=False,
                    truncated=out.truncated or err.truncated,
                    persisted_out=str(out.persisted) if out.persisted is not None else None,
                    persisted_err=str(err.persisted) if err.persisted is not None else None,
                    background=Background(
                        process=process,
                        wait=wait,
                        readers=readers,
                        out=out,
                        err=err,
                        started_at=started_at,
                        lifetime_ms=command.lifetime_ms,
                    ),
                )
            else:
                timed_out = True
                _kill(process)
        finally:
            if cancellation is not None:
                cancellation.cancel()
                await asyncio.gather(cancellation, return_exceptions=True)

        # Reap before draining: the readers see EOF when the last writer closes,
        # and that happens when the process is gone.
        await wait
        await _drain(readers)
        out.close()
        err.close()

        return Outcome(
            exit_code=_status(process),
            wall_ms=int((time.monotonic() - started_at) * 1000),
            timed_out=timed_out,
            cancelled=cancelled,
            truncated=out.truncated or err.truncated,
            persisted_out=str(out.persisted) if out.persisted is not None else None,
            persisted_err=str(err.persisted) if err.persisted is not None else None,
        )

    def _stream(self, command: Command, stream: str) -> _Stream:
        if not command.persist_output or self._spill_dir is None:
            return _Stream(command.max_output_bytes, command.max_output_bytes, None)
        safe = "".join(
            character for character in command.name if character.isalnum() or character in "-_"
        )
        return _Stream(
            min(PREVIEW_BYTES, command.max_output_bytes),
            command.max_output_bytes,
            self._spill_dir / f"{safe or 'tool'}.{stream}",
        )
