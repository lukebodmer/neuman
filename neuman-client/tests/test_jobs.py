"""Background jobs: a run that outlives its wait, and what a look at it returns.

Two halves. The executor's: at the wait a run with lifetime left is handed over
still running, its output goes on to the spill file rather than the sink, and
its lifetime still kills it. The registry's: a look shows only output the model
has not been shown, a few lines of it, and finds only the asking session's
jobs — because every byte a look returns is re-sent with every later request.
"""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

from neuman_client.daemon import ClientDaemon
from neuman_client.executor import Command, Executor
from neuman_client.jobs import LOOK_ENDED, SLOTS, JobRegistry, duration, job_kill, job_output
from neuman_client.tools import ToolHost


class Recording:
    def __init__(self) -> None:
        self.stdout = bytearray()
        self.stderr = bytearray()

    async def started(self, pid: int) -> None:
        pass

    async def output(self, stream: str, data: bytes) -> None:
        (self.stdout if stream == "stdout" else self.stderr).extend(data)


def program(source: str) -> tuple[str, ...]:
    return (sys.executable, "-u", "-c", source)


def command(source: str, *, wait_ms: int = 300, lifetime_ms: int = 30_000) -> Command:
    return Command(
        argv=program(source), name="shell-d1", wait_ms=wait_ms, lifetime_ms=lifetime_ms
    )


async def detach(tmp_path: Path, source: str, **kwargs: int):
    sink = Recording()
    outcome = await Executor(spill_dir=tmp_path).run(
        command(source, **kwargs), sink, admit=lambda: True
    )
    assert outcome.background is not None, "the run should have been handed over"
    return sink, outcome


# -- the executor ------------------------------------------------------------


async def test_a_run_outliving_its_wait_is_handed_over_still_running(tmp_path):
    sink, outcome = await detach(
        tmp_path,
        "import time; print('before'); time.sleep(0.6); print('after')",
    )
    background = outcome.background

    assert not background.done.is_set()
    assert not outcome.timed_out
    assert sink.stdout == b"before\n"  # forwarded while the dispatch was open

    await asyncio.wait_for(background.run(), timeout=10)

    assert background.ended == "exited"
    assert background.exit_code == 0
    # Nothing reached the sink after the hand-over: the dispatch was closed.
    assert sink.stdout == b"before\n"
    # The file holds the stream from its first byte, so it is the whole output.
    assert Path(outcome.persisted_out).read_bytes() == b"before\nafter\n"


async def test_a_run_with_no_slot_is_killed_at_its_wait_as_before(tmp_path):
    outcome = await asyncio.wait_for(
        Executor(spill_dir=tmp_path).run(
            command("import time; time.sleep(30)"), Recording(), admit=lambda: False
        ),
        timeout=10,
    )

    assert outcome.background is None
    assert outcome.timed_out
    assert outcome.exit_code == 128 + 9


async def test_a_run_whose_lifetime_is_its_wait_is_never_offered(tmp_path):
    offered = []

    def admit() -> bool:
        offered.append(True)
        return True

    outcome = await asyncio.wait_for(
        Executor(spill_dir=tmp_path).run(
            command("import time; time.sleep(30)", wait_ms=200, lifetime_ms=200),
            Recording(),
            admit=admit,
        ),
        timeout=10,
    )

    assert outcome.timed_out
    assert offered == []


async def test_a_run_with_nowhere_to_spill_cannot_become_a_job():
    """A job's output has to go somewhere once the dispatch is closed."""

    outcome = await asyncio.wait_for(
        Executor().run(command("import time; time.sleep(30)"), Recording(), admit=lambda: True),
        timeout=10,
    )

    assert outcome.background is None
    assert outcome.timed_out


async def test_a_job_is_killed_at_its_lifetime(tmp_path):
    _, outcome = await detach(tmp_path, "import time; time.sleep(30)", lifetime_ms=800)

    await asyncio.wait_for(outcome.background.run(), timeout=10)

    assert outcome.background.ended == "lifetime"
    assert outcome.background.exit_code == 128 + 9


async def test_a_killed_job_says_it_was_killed(tmp_path):
    _, outcome = await detach(tmp_path, "import time; time.sleep(30)")
    runner = asyncio.create_task(outcome.background.run())

    outcome.background.kill()
    await asyncio.wait_for(runner, timeout=10)

    assert outcome.background.ended == "killed"


async def test_the_tail_keeps_the_end_of_a_stream_the_file_cut(tmp_path):
    """The file keeps the head up to the cap; the end is what says how it went."""

    sink = Recording()
    outcome = await Executor(spill_dir=tmp_path).run(
        Command(
            argv=program(
                "import time; time.sleep(0.4)\n"
                "for i in range(20000): print(f'line {i}')\n"
                "print('BUILD FAILED')"
            ),
            name="shell-d2",
            wait_ms=200,
            lifetime_ms=30_000,
            max_output_bytes=4096,
        ),
        sink,
        admit=lambda: True,
    )
    background = outcome.background
    await asyncio.wait_for(background.run(), timeout=10)

    assert background.out.truncated
    assert len(Path(outcome.persisted_out).read_bytes()) == 4096
    assert background.out.tail.endswith(b"BUILD FAILED\n")


# -- what a look returns -------------------------------------------------------


async def registered(tmp_path: Path, source: str, *, session: str = "s1", **kwargs: int):
    registry = JobRegistry()
    _, outcome = await detach(tmp_path, source, **kwargs)
    job = registry.add(
        outcome.background,
        dispatch_id="d1c0ffee",
        session_id=session,
        tool="shell",
        args={"command": "make test"},
    )
    return registry, job


async def look(registry: JobRegistry, args: dict, session: str = "s1") -> tuple[int, str]:
    return await job_output(
        registry, args, session_id=session, wait_ms=600_000, cancel=asyncio.Event()
    )


async def test_a_look_waits_for_the_end_and_shows_only_unseen_output(tmp_path):
    registry, job = await registered(
        tmp_path, "import time; print('preview'); time.sleep(0.6); print('the rest')"
    )

    code, text = await asyncio.wait_for(look(registry, {"job_id": job.id}), timeout=10)

    assert code == 0
    # The preview went out with the dispatch and is already in the model's
    # context; repeating it would be paying for it twice.
    assert text == f"job {job.id}: exited 0 after {duration(job.background.wall_ms)}\nthe rest"

    _, again = await look(registry, {"job_id": job.id})
    assert (
        again
        == f"job {job.id}: exited 0 after {duration(job.background.wall_ms)}\n(no new output)"
    )
    await registry.kill_all()


async def test_a_look_that_does_not_wait_reports_progress(tmp_path):
    registry, job = await registered(tmp_path, "import time; time.sleep(30)")

    _, text = await look(registry, {"job_id": job.id, "wait_s": 0})

    assert text.startswith(f"job {job.id}: still running after ")
    assert "(killed at 30s)" in text
    assert text.endswith("(no new output)")
    await registry.kill_all()


async def test_a_long_unseen_stretch_shows_its_end_and_where_the_rest_is(tmp_path):
    registry, job = await registered(
        tmp_path,
        "import time; time.sleep(0.4)\nfor i in range(5000): print(f'line {i}')\nprint('DONE')",
    )

    _, text = await asyncio.wait_for(look(registry, {"job_id": job.id}), timeout=10)

    lines = text.splitlines()
    assert lines[1].startswith("[") and "earlier bytes not shown" in lines[1]
    assert str(job.background.out.persisted) in lines[1]
    assert "fs_search" in lines[1]
    # Starts on a whole line, ends with the end.
    assert lines[2].startswith("line ")
    assert lines[-1] == "DONE"
    assert len(text.encode()) < LOOK_ENDED + 400
    await registry.kill_all()


async def test_stderr_is_labelled_so_the_two_streams_are_not_confused(tmp_path):
    registry, job = await registered(
        tmp_path,
        "import sys, time; time.sleep(0.4); print('out'); sys.stderr.write('warning: x\\n')",
    )

    _, text = await asyncio.wait_for(look(registry, {"job_id": job.id}), timeout=10)

    assert text.splitlines()[1:] == ["out", "stderr:", "warning: x"]
    await registry.kill_all()


async def test_another_sessions_job_is_not_found(tmp_path):
    registry, job = await registered(tmp_path, "import time; time.sleep(30)", session="s1")

    code, text = await look(registry, {"job_id": job.id, "wait_s": 0}, session="s2")
    killed, refusal = await job_kill(registry, {"job_id": job.id}, session_id="s2")

    assert code == 1 and killed == 1
    assert text == refusal == f"no job {job.id!r} in this session, which has none"
    assert job.running
    await registry.kill_all()


async def test_a_look_without_an_id_lists_the_sessions_jobs(tmp_path):
    registry, job = await registered(tmp_path, "import time; time.sleep(30)")

    _, text = await look(registry, {})
    _, elsewhere = await look(registry, {}, session="s2")

    assert text.splitlines()[0] == "jobs in this session:"
    assert text.splitlines()[1].startswith(f"{job.id}  running ")
    assert text.splitlines()[1].endswith("shell: make test")
    assert elsewhere == "no jobs in this session"
    await registry.kill_all()


async def test_a_kill_reports_how_it_ended_and_counts_as_observed(tmp_path):
    registry, job = await registered(tmp_path, "import time; time.sleep(30)")
    settled = []

    async def on_settled(ended) -> None:
        settled.append((ended.id, ended.observed))

    registry.on_settled = on_settled
    code, text = await asyncio.wait_for(
        job_kill(registry, {"job_id": job.id}, session_id="s1"), timeout=10
    )
    await asyncio.sleep(0)

    assert code == 0
    assert text.startswith(f"job {job.id}: killed after ")
    # The kill told the model; the core should not tell it again.
    assert settled == [(job.id, True)]


async def test_a_job_that_ends_unwatched_is_not_observed(tmp_path):
    registry, _job = await registered(tmp_path, "import time; time.sleep(0.6)")
    settled = asyncio.Event()
    seen = []

    async def on_settled(ended) -> None:
        seen.append(ended.observed)
        settled.set()

    registry.on_settled = on_settled
    await asyncio.wait_for(settled.wait(), timeout=10)

    assert seen == [False]


async def test_the_slots_are_counted_over_running_jobs(tmp_path):
    registry = JobRegistry()
    for index in range(SLOTS):
        _, outcome = await detach(tmp_path / str(index), "import time; time.sleep(30)")
        registry.add(
            outcome.background, dispatch_id=f"d{index}", session_id="s1", tool="shell", args={}
        )

    assert not registry.admit()
    await registry.kill_all()
    assert registry.admit()


# -- through the daemon ----------------------------------------------------------


class FakeConnection:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[dict] = asyncio.Queue()
        self.sent: list[dict] = []

    async def send(self, message: dict) -> None:
        self.sent.append(message)

    async def recv(self) -> dict:
        return await self.incoming.get()

    async def close(self) -> None:
        pass


class FakeTransport:
    def __init__(self) -> None:
        self.connection = FakeConnection()

    async def connect(self) -> FakeConnection:
        return self.connection


def _host(tmp_path: Path) -> ToolHost:
    manifests = tmp_path / "manifests"
    bin_dir = tmp_path / "bin"
    for name, body in (
        ("job_output", None),
        ("job_kill", None),
        ("slow", "echo begun; sleep 30"),
    ):
        (manifests / name).mkdir(parents=True)
        if body is None:
            text = (
                f'[tool]\nname = "{name}"\nkind = "client"\ndaemon = true\nwait_ms = 600000\n'
            )
        else:
            text = f'[tool]\nname = "{name}"\nexec = "{name}"\nwait_ms = 300\nlifetime_ms = 60000\n'
            bin_dir.mkdir(parents=True, exist_ok=True)
            (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
            (bin_dir / name).chmod(0o755)
        (manifests / name / "manifest.toml").write_text(text, encoding="utf-8")
    return ToolHost(manifests_dir=manifests, bin_dir=bin_dir, spill_dir=tmp_path / "spill")


def _dispatch(dispatch_id: str, tool: str, args: dict, **extra) -> dict:
    return {
        "type": "dispatch",
        "rpc_version": 2,
        "dispatch_id": dispatch_id,
        "tool": tool,
        "args": args,
        "wait_ms": 600_000 if tool.startswith("job_") else 300,
        "lifetime_ms": 600_000 if tool.startswith("job_") else 60_000,
        "surfaces": {"read": [], "write": []},
        **extra,
    }


async def _until(predicate, *, timeout: float = 10.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


def _of(connection: FakeConnection, dispatch_id: str, kind: str) -> list[dict]:
    return [
        message
        for message in connection.sent
        if message.get("dispatch_id") == dispatch_id and message["type"] == kind
    ]


async def _running(tmp_path: Path):
    transport = FakeTransport()
    daemon = ClientDaemon(
        transport=transport,
        machine="ernest",
        home="/home/alice",
        drops="/home/alice/.local/state/neuman/drops",
        host=_host(tmp_path),
        executor=Executor(spill_dir=tmp_path / "spill"),
    )
    task = asyncio.create_task(daemon.run())
    await _until(lambda: transport.connection.sent)
    transport.connection.incoming.put_nowait(
        {
            "type": "welcome",
            "rpc_version": 2,
            "user": "alice",
            "machine": {"label": "ernest", "kind": "client"},
        }
    )
    return transport.connection, task


async def test_the_daemon_detaches_reports_and_settles_a_job(tmp_path):
    connection, task = await _running(tmp_path)
    try:
        connection.incoming.put_nowait(_dispatch("d1c0ffee01", "slow", {}, session_id="s1"))
        await _until(lambda: _of(connection, "d1c0ffee01", "detached"))
        [detached] = _of(connection, "d1c0ffee01", "detached")
        job_id = detached["job_id"]

        # The dispatch's stream closed at `detached`, in sequence.
        frames = [m for m in connection.sent if m.get("dispatch_id") == "d1c0ffee01"]
        assert [m["sequence"] for m in frames] == list(range(len(frames)))
        assert detached["lifetime_ms"] == 60_000

        connection.incoming.put_nowait(
            _dispatch("d1c0ffee02", "job_kill", {"job_id": job_id}, session_id="s1")
        )
        await _until(lambda: _of(connection, "d1c0ffee02", "result"))
        [chunk] = _of(connection, "d1c0ffee02", "stdout")
        assert (
            base64.b64decode(chunk["chunk"]).decode().startswith(f"job {job_id}: killed after ")
        )

        await _until(lambda: [m for m in connection.sent if m["type"] == "job_settled"])
        [settled] = [m for m in connection.sent if m["type"] == "job_settled"]
        assert settled["job_id"] == job_id
        assert settled["dispatch_id"] == "d1c0ffee01"
        assert settled["ended"] == "killed"
        assert settled["observed"] is True
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_a_dispatch_with_no_session_is_killed_at_its_wait(tmp_path):
    connection, task = await _running(tmp_path)
    try:
        connection.incoming.put_nowait(_dispatch("d1c0ffee03", "slow", {}))
        await _until(lambda: _of(connection, "d1c0ffee03", "result"))
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    [result] = _of(connection, "d1c0ffee03", "result")
    assert result["timed_out"] is True
    assert not _of(connection, "d1c0ffee03", "detached")


async def test_a_cancel_naming_the_jobs_dispatch_kills_the_job(tmp_path):
    """How the core stops a job it has to: the dispatch id it already holds."""

    connection, task = await _running(tmp_path)
    try:
        connection.incoming.put_nowait(_dispatch("d1c0ffee04", "slow", {}, session_id="s1"))
        await _until(lambda: _of(connection, "d1c0ffee04", "detached"))
        connection.incoming.put_nowait(
            {"type": "cancel", "rpc_version": 2, "dispatch_id": "d1c0ffee04"}
        )
        await _until(lambda: [m for m in connection.sent if m["type"] == "job_settled"])
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    [settled] = [m for m in connection.sent if m["type"] == "job_settled"]
    assert settled["ended"] == "killed"
    assert settled["observed"] is False


async def test_a_look_that_waits_is_started_before_it_waits(tmp_path):
    """The core's D15 clock runs until `started`, and a look can run for minutes.

    A daemon tool has no process, but it still has to say `started` before it
    does the work: the core gives up five seconds after the dispatch, so a look
    that waited first would be failed for a job it was still watching — and its
    settlement would then be suppressed as already reported.
    """

    connection, task = await _running(tmp_path)
    try:
        connection.incoming.put_nowait(_dispatch("d1c0ffee06", "slow", {}, session_id="s1"))
        await _until(lambda: _of(connection, "d1c0ffee06", "detached"))
        [detached] = _of(connection, "d1c0ffee06", "detached")

        connection.incoming.put_nowait(
            _dispatch(
                "d1c0ffee07",
                "job_output",
                {"job_id": detached["job_id"], "wait_s": 1},
                session_id="s1",
            )
        )
        # While the look is waiting, its `started` is already on the wire and
        # its result is not.
        await _until(lambda: _of(connection, "d1c0ffee07", "started"), timeout=0.5)
        [started] = _of(connection, "d1c0ffee07", "started")
        assert started["pid"] is None
        assert not _of(connection, "d1c0ffee07", "result")

        await _until(lambda: _of(connection, "d1c0ffee07", "result"))
        [chunk] = _of(connection, "d1c0ffee07", "stdout")
        assert (
            base64.b64decode(chunk["chunk"])
            .decode()
            .startswith(f"job {detached['job_id']}: still running after ")
        )
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_a_dropped_connection_kills_its_jobs(tmp_path):
    connection, task = await _running(tmp_path)
    connection.incoming.put_nowait(_dispatch("d1c0ffee05", "slow", {}, session_id="s1"))
    await _until(lambda: _of(connection, "d1c0ffee05", "detached"))
    [started] = _of(connection, "d1c0ffee05", "started")

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    # The process group is gone: signal 0 probes without signalling.
    import os

    try:
        os.killpg(started["pid"], 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive
