"""The client executor: run a command, forward it, bound it, kill it.

The tests that matter here are the ones pinning a rule that was learned on the
server and carried across (N14, N15) plus the client-only facts: there is no
cgroup, so the process group is the only kill that reaches a descendant, and the
spill file is the client's to write, past a preview the manifest does not set.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from neuman_client.executor import PREVIEW_BYTES, Command, Executor


class Recording:
    """A sink that keeps everything, for assertions."""

    def __init__(self) -> None:
        self.pid: int | None = None
        self.stdout = bytearray()
        self.stderr = bytearray()

    async def started(self, pid: int) -> None:
        self.pid = pid

    async def output(self, stream: str, data: bytes) -> None:
        (self.stdout if stream == "stdout" else self.stderr).extend(data)


def program(source: str) -> tuple[str, ...]:
    return (sys.executable, "-c", source)


async def test_a_normal_run_reports_pid_exit_and_both_streams():
    sink = Recording()
    outcome = await Executor().run(
        Command(argv=program("import sys; print('hi'); sys.stderr.write('bye'); sys.exit(3)")),
        sink,
    )

    assert sink.pid is not None and sink.pid > 0
    assert sink.stdout == b"hi\n"
    assert sink.stderr == b"bye"
    assert outcome.exit_code == 3
    assert not outcome.timed_out
    assert not outcome.cancelled
    assert outcome.wall_ms >= 0


async def test_both_pipes_drain_without_deadlock():
    """N14, restated for the client.

    The child fills stderr *first* and only then writes stdout. A reader that
    finished stdout before touching stderr waits for an EOF the child cannot
    reach, because the child is blocked writing stderr — the classic deadlock.
    Concurrent draining is the whole fix.
    """

    sink = Recording()
    source = (
        "import sys\n"
        "sys.stderr.write('e' * 300000); sys.stderr.flush()\n"
        "sys.stdout.write('o' * 300000); sys.stdout.flush()\n"
    )
    outcome = await asyncio.wait_for(
        Executor().run(Command(argv=program(source)), sink),
        timeout=15,
    )

    assert outcome.exit_code == 0
    assert len(sink.stderr) == 300000
    assert len(sink.stdout) == 300000


async def test_the_deadline_kills_before_the_pipes_are_waited_out():
    """N15: enforce the deadline first, then drain."""

    sink = Recording()
    outcome = await asyncio.wait_for(
        Executor().run(
            Command(argv=program("import time; time.sleep(30)"), wait_ms=200, lifetime_ms=200),
            sink,
        ),
        timeout=15,
    )

    assert outcome.timed_out
    assert not outcome.cancelled
    # 128 + SIGKILL, the conventional report for a signalled process.
    assert outcome.exit_code == 128 + 9


async def test_cancellation_kills_the_process():
    cancel = asyncio.Event()

    async def fire() -> None:
        await asyncio.sleep(0.2)
        cancel.set()

    trigger = asyncio.create_task(fire())
    sink = Recording()
    try:
        outcome = await asyncio.wait_for(
            Executor().run(
                Command(
                    argv=program("import time; time.sleep(30)"),
                    wait_ms=30000,
                    lifetime_ms=30000,
                ),
                sink,
                cancel=cancel,
            ),
            timeout=15,
        )
    finally:
        await trigger

    assert outcome.cancelled
    assert not outcome.timed_out
    assert outcome.exit_code == 128 + 9


async def test_output_past_the_preview_forwards_the_preview_and_spills_the_whole_stream(
    tmp_path,
):
    """N180: the preview is small and the manifest cap is the file's, not the message's.

    `web_fetch` stopped itself just under its 1 MiB cap, and the cap was where
    spilling began, so a page never spilled and arrived inline whole.
    """

    sink = Recording()
    size = PREVIEW_BYTES * 4
    outcome = await Executor(spill_dir=tmp_path).run(
        Command(
            argv=program(f"import sys; sys.stdout.write('x' * {size})"),
            name="page",
            max_output_bytes=1024 * 1024,
        ),
        sink,
    )

    assert sink.stdout == b"x" * PREVIEW_BYTES
    assert outcome.persisted_out is not None
    assert Path(outcome.persisted_out).read_bytes() == b"x" * size
    # Nothing was lost, so the model is not told the file was cut.
    assert not outcome.truncated
    assert outcome.persisted_err is None


async def test_the_spill_file_stops_at_the_manifest_cap(tmp_path):
    sink = Recording()
    outcome = await Executor(spill_dir=tmp_path).run(
        Command(argv=program("print('x' * 100)"), name="cap", max_output_bytes=10),
        sink,
    )

    # The preview never exceeds the cap, and the file holds no more than it.
    assert outcome.truncated
    assert sink.stdout == b"x" * 10
    assert Path(outcome.persisted_out).read_bytes() == b"x" * 10


async def test_an_inline_tool_forwards_up_to_its_cap_and_spills_nothing(tmp_path):
    """N127: a page the caller bounded is delivered as asked, not previewed."""

    sink = Recording()
    size = PREVIEW_BYTES * 2
    outcome = await Executor(spill_dir=tmp_path).run(
        Command(
            argv=program(f"import sys; sys.stdout.write('y' * {size + 50})"),
            name="pager",
            max_output_bytes=size,
            persist_output=False,
        ),
        sink,
    )

    assert sink.stdout == b"y" * size
    assert outcome.truncated
    assert outcome.persisted_out is None
    assert list(tmp_path.iterdir()) == []


async def test_without_a_spill_directory_the_cap_bounds_what_is_forwarded():
    sink = Recording()
    outcome = await Executor().run(
        Command(argv=program("print('x' * 100)"), max_output_bytes=10),
        sink,
    )

    assert outcome.truncated
    assert sink.stdout == b"x" * 10
    assert outcome.persisted_out is None


async def test_a_dispatch_that_fits_spills_nothing(tmp_path):
    sink = Recording()
    outcome = await Executor(spill_dir=tmp_path).run(
        Command(argv=program("print('ok')"), name="fits", max_output_bytes=1000),
        sink,
    )

    assert not outcome.truncated
    assert outcome.persisted_out is None
    assert outcome.persisted_err is None
    assert list(tmp_path.iterdir()) == []


async def test_the_command_runs_in_the_given_directory_and_environment(tmp_path):
    sink = Recording()
    outcome = await Executor().run(
        Command(
            argv=program("import os; print(os.getcwd()); print(os.environ.get('NEUMAN_TEST'))"),
            cwd=str(tmp_path),
            env={"PATH": "/usr/bin:/bin", "NEUMAN_TEST": "yes"},
        ),
        sink,
    )

    assert outcome.exit_code == 0
    assert sink.stdout.decode().splitlines() == [str(tmp_path), "yes"]


async def test_a_program_that_cannot_start_raises():
    """A dispatch that never ran has no exit status; the caller reports an error."""

    with pytest.raises(OSError):
        await Executor().run(Command(argv=("/nonexistent/neuman-program",)), Recording())


async def test_a_bad_working_directory_raises(tmp_path):
    with pytest.raises(OSError):
        await Executor().run(
            Command(argv=program("pass"), cwd=str(tmp_path / "gone")),
            Recording(),
        )
