"""Background jobs: commands that outlived their wait, and the two tools that follow them.

A dispatch whose process is still running at its wait, with lifetime left and a
free slot here, becomes a job (D98). The process keeps running, its output keeps
going to the spill files, and the model gets control back with the job's id.
`job_output` looks at a job and `job_kill` stops one. Both are answered here, in
the daemon, because the jobs live here: a subprocess could not see them.

**What a look puts in the model's context is the design.** Every byte a job
tool returns is re-sent with every later request of the session, so a look
returns only output the model has not been shown — the dispatch's preview counts
as shown — and at most a tail of that. While the job runs the tail is a few
lines, enough to tell progress from a hang; once it has ended, a little more,
because the end of a build is where it says what went wrong. Anything longer is
in the spill file, named only when something was left out. A look that waits is
the cheap way to follow a long job: one call that returns when the job ends
costs one result, where polling costs one per poll.

**A job belongs to a session.** The dispatch names the session, and a look or a
kill finds only that session's jobs, so one conversation cannot read or stop
another's build.

**Jobs die with the connection.** A job the core can no longer hear about is an
effect with no record and nobody to stop it, so a dropped socket kills every job
exactly as it cancels every dispatch (`daemon.py`). Surviving a reconnect needs
the hello to re-announce live jobs; until then this is the honest rule.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .executor import Background

__all__ = ["SLOTS", "Job", "JobRegistry", "job_kill", "job_output"]

# Running jobs per daemon. At the cap a dispatch is killed at its wait, as it
# was before jobs existed, and the result says why. Evicting a running build to
# make room for another would be worse than refusing the second.
SLOTS = 4

# Ended jobs kept for a later look, newest first. A job that ended is still
# worth asking about — its exit status, the output nobody read — but not
# forever, and they hold their tails in memory.
KEEP_ENDED = 16

# How much of each stream one look shows: the newest bytes the model has not
# seen, cut to a line boundary. Small while the job runs, because a look then
# answers "is it moving?"; larger once it has ended, because the end is the
# answer.
LOOK_RUNNING = 1024
LOOK_ENDED = 4096

# The longest a look may wait for a job to end, and how long it waits when the
# model does not say. The default is generous on purpose: a model that follows a
# long build with short looks pays for each one with a step and its result.
WAIT_DEFAULT_S = 300
WAIT_MAX_S = 600

LABEL_CHARS = 60


@dataclass(slots=True)
class Job:
    id: str
    dispatch_id: str
    session_id: str
    tool: str
    label: str
    background: Background
    # Bytes of each stream the model has been shown. Starts at what the
    # dispatch forwarded, because that preview is in the model's context.
    shown: dict[str, int] = field(default_factory=dict)
    # Looks waiting on it now. A job that ends while one is waiting has been
    # reported by that look, so the core records it without a notice.
    watchers: int = 0
    observed: bool = False

    @property
    def running(self) -> bool:
        return not self.background.done.is_set()


class JobRegistry:
    """The jobs this daemon holds, for every session of its account."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._runners: set[asyncio.Task[None]] = set()
        self.on_settled: Callable[[Job], Awaitable[None]] | None = None

    def admit(self) -> bool:
        return sum(1 for job in self._jobs.values() if job.running) < SLOTS

    def add(
        self,
        background: Background,
        *,
        dispatch_id: str,
        session_id: str,
        tool: str,
        args: dict[str, object],
    ) -> Job:
        job_id = secrets.token_hex(3)
        while job_id in self._jobs:
            job_id = secrets.token_hex(3)
        job = Job(
            id=job_id,
            dispatch_id=dispatch_id,
            session_id=session_id,
            tool=tool,
            label=_label(tool, args),
            background=background,
            shown={"stdout": background.out.forwarded, "stderr": background.err.forwarded},
        )
        self._jobs[job_id] = job
        runner = asyncio.create_task(self._see_through(job))
        self._runners.add(runner)
        runner.add_done_callback(self._runners.discard)
        return job

    def find(self, job_id: str, session_id: str | None) -> Job | None:
        job = self._jobs.get(job_id)
        if job is None or session_id is None or job.session_id != session_id:
            return None
        return job

    def by_dispatch(self, dispatch_id: str) -> Job | None:
        for job in self._jobs.values():
            if job.dispatch_id == dispatch_id:
                return job
        return None

    def in_session(self, session_id: str | None) -> list[Job]:
        return [job for job in self._jobs.values() if job.session_id == session_id]

    def live(self) -> list[Job]:
        """The jobs still running, newest first.

        What the browser's row lists. No session filter: the daemon serves one
        account, and the person at the keyboard is that account. The fence
        `find` applies is between *conversations*, so one session's model cannot
        read another's output; it is not a boundary against the human, who can
        see the process in `ps` anyway.
        """

        return sorted(
            (job for job in self._jobs.values() if job.running),
            key=lambda job: job.background.started_at,
            reverse=True,
        )

    async def kill_all(self) -> None:
        """Kill every running job and wait for each to settle."""

        for job in self._jobs.values():
            job.background.kill()
        if self._runners:
            await asyncio.gather(*self._runners, return_exceptions=True)
        self._jobs.clear()

    async def _see_through(self, job: Job) -> None:
        try:
            await job.background.run()
        finally:
            job.observed = job.watchers > 0
            self._prune()
        if self.on_settled is not None:
            await self.on_settled(job)

    def _prune(self) -> None:
        ended = [job for job in self._jobs.values() if not job.running]
        for job in ended[: max(len(ended) - KEEP_ENDED, 0)]:
            del self._jobs[job.id]


async def job_output(
    registry: JobRegistry,
    args: dict[str, object],
    *,
    session_id: str | None,
    wait_ms: int,
    cancel: asyncio.Event,
) -> tuple[int, str]:
    """Answer `job_output`: (exit code, text for the model)."""

    job_id = args.get("job_id")
    if not job_id:
        return 0, _listing(registry, session_id)
    job = registry.find(str(job_id), session_id)
    if job is None:
        return 1, _unknown(registry, str(job_id), session_id)

    wait_s = args.get("wait_s", WAIT_DEFAULT_S)
    if not isinstance(wait_s, int) or isinstance(wait_s, bool) or wait_s < 0:
        return 1, "wait_s must be a whole number of seconds, 0 or more"
    wait = min(wait_s, WAIT_MAX_S, wait_ms / 1000)

    if job.running and wait > 0:
        job.watchers += 1
        try:
            await _until(job.background.done, cancel, wait)
        finally:
            job.watchers -= 1
    return 0, _look(job)


async def job_kill(
    registry: JobRegistry,
    args: dict[str, object],
    *,
    session_id: str | None,
) -> tuple[int, str]:
    """Answer `job_kill`: (exit code, text for the model)."""

    job_id = str(args.get("job_id") or "")
    job = registry.find(job_id, session_id)
    if job is None:
        return 1, _unknown(registry, job_id, session_id)
    if job.running:
        job.watchers += 1
        try:
            job.background.kill()
            await job.background.done.wait()
        finally:
            job.watchers -= 1
    return 0, _look(job)


async def _until(done: asyncio.Event, cancel: asyncio.Event, seconds: float) -> None:
    waits = [asyncio.create_task(done.wait()), asyncio.create_task(cancel.wait())]
    try:
        await asyncio.wait(waits, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in waits:
            task.cancel()
        await asyncio.gather(*waits, return_exceptions=True)


def _look(job: Job) -> str:
    """One look: a status line, then the output the model has not seen."""

    background = job.background
    if job.running:
        status = (
            f"job {job.id}: still running after {duration(background.running_ms)} "
            f"(killed at {duration(background.lifetime_ms)})"
        )
        budget = LOOK_RUNNING
    else:
        status = f"job {job.id}: {ending(background)}"
        budget = LOOK_ENDED

    parts = [status]
    for stream, capture in (("stdout", background.out), ("stderr", background.err)):
        text = _unseen(job, stream, capture, budget)
        if text:
            parts.append(text if stream == "stdout" else f"stderr:\n{text}")
    if len(parts) == 1:
        parts.append("(no new output)")
    return "\n".join(parts)


def _unseen(job: Job, stream: str, capture: Any, budget: int) -> str:
    """The stream's output since the last look, at most `budget` bytes of its end."""

    seen: int = capture.seen
    tail: bytearray = capture.tail
    new = seen - job.shown.get(stream, 0)
    job.shown[stream] = seen
    if new <= 0:
        return ""

    chunk = bytes(tail[-min(new, budget, len(tail)) :])
    skipped = new - len(chunk)
    if skipped > 0:
        # Start on a whole line: half a line at the top reads as corruption.
        newline = chunk.find(b"\n")
        if 0 <= newline < len(chunk) - 1:
            skipped += newline + 1
            chunk = chunk[newline + 1 :]
    text = chunk.decode("utf-8", errors="replace").rstrip("\n")
    if skipped <= 0:
        return text

    note = f"[{skipped:,} earlier bytes not shown"
    path = capture.persisted
    if path is not None:
        note += f"; all of it is in {path}"
        if capture.truncated:
            note += " (cut at the tool's size limit)"
        note += " — search it with fs_search"
    return f"{note}]\n{text}"


def ending(background: Background) -> str:
    took = duration(background.wall_ms or 0)
    if background.ended == "lifetime":
        return f"killed at its {duration(background.lifetime_ms)} lifetime"
    if background.ended == "killed":
        return f"killed after {took}"
    return f"exited {background.exit_code} after {took}"


def _listing(registry: JobRegistry, session_id: str | None) -> str:
    jobs = registry.in_session(session_id)
    if not jobs:
        return "no jobs in this session"
    lines = ["jobs in this session:"]
    for job in jobs:
        background = job.background
        state = (
            f"running {duration(background.running_ms)}" if job.running else ending(background)
        )
        lines.append(f"{job.id}  {state}  {job.tool}: {job.label}")
    return "\n".join(lines)


def _unknown(registry: JobRegistry, job_id: str, session_id: str | None) -> str:
    known = [job.id for job in registry.in_session(session_id)]
    if not known:
        return f"no job {job_id!r} in this session, which has none"
    return f"no job {job_id!r} in this session; its jobs are {', '.join(known)}"


def _label(tool: str, args: dict[str, object]) -> str:
    """What the job was, in a few words, for a listing that has to tell them apart."""

    value: Any
    if tool == "shell":
        value = args.get("command", "")
    elif tool == "git":
        parts = args.get("args")
        value = " ".join(str(part) for part in parts) if isinstance(parts, list) else ""
    elif tool == "python":
        lines = str(args.get("code", "")).strip().splitlines()
        value = lines[0] if lines else ""
    else:
        value = json.dumps(args, separators=(",", ":"))
    text = " ".join(str(value).split())
    return text if len(text) <= LABEL_CHARS else text[: LABEL_CHARS - 1] + "…"


def duration(ms: int) -> str:
    """`45s`, `4m10s`, `1h02m`: short, because it is read in every look."""

    seconds = ms // 1000
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"
