"""What the model is told about a background job (D98).

Three sentences, written once here so the loop that says them and the tests
that pin them read the same text. Each is short because each is context: the
note on a detached result and a job's end are re-sent with every later request
of the session, and a model following three builds should not be paying for
three paragraphs of explanation it read the first time.

* **Detached** — the result of a call whose process outlived its wait. Says the
  id, when it will be killed, and the two tools that follow it; never the spill
  path, which `job_output` names when it leaves something out.
* **Refused** — a call that could have become a job and was killed instead,
  because the client had no free slot. Says so, so the model does not retry the
  same build into the same wall.
* **Ended** — a job finished while no look was waiting on it. Logged as a
  `harness_event` of type `job_settled` carrying the sentence itself, and
  projected as a user turn marked as Neuman's: the log is what history is
  derived from, so a notice held in memory would vanish at the next step
  (projection.py). It does not carry the output — a look does — so a job that
  ends while the model is busy costs one line.

The client says the rest (`neuman_client/jobs.py`): a look's status line and
the unseen tail are its to write, because it holds the output.
"""

from __future__ import annotations

__all__ = [
    "JOB_KILL",
    "JOB_OUTPUT",
    "SETTLED",
    "detached_note",
    "duration",
    "ended_notice",
    "refused_note",
]

JOB_OUTPUT = "job_output"
JOB_KILL = "job_kill"

# The `harness_event` type a job's end is logged as.
SETTLED = "job_settled"


def duration(ms: int) -> str:
    """`45s`, `4m10s`, `1h02m` — the client's spelling, so a look and a notice agree."""

    seconds = ms // 1000
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def detached_note(job_id: str, *, wall_ms: int, lifetime_ms: int | None, can_kill: bool) -> str:
    kill = (
        f"killed at {duration(lifetime_ms)} if it has not ended"
        if lifetime_ms
        else "time-limited"
    )
    stop = "; job_kill stops it" if can_kill else ""
    return (
        f"[still running after {duration(wall_ms)}, so it continues as background job "
        f"{job_id} ({kill}). job_output with job_id {job_id!r} waits for it and shows the "
        f"output you have not seen{stop}.]"
    )


def refused_note(wait_ms: int) -> str:
    return (
        f"[killed at its {duration(wait_ms)} wait: the client is already running as many "
        "background jobs as it allows. job_output with no job_id lists them.]"
    )


def ended_notice(
    job_id: str,
    *,
    tool: str,
    ended: str,
    exit_code: int,
    wall_ms: int,
    persisted_out: str | None = None,
) -> str:
    took = duration(wall_ms)
    head = f"Neuman notice (not from the user): background job {job_id} ({tool})"
    if ended == "disconnected":
        where = f" Its output so far is in {persisted_out}." if persisted_out else ""
        return (
            f"{head} was killed when the Neuman client disconnected, so job_output can no "
            f"longer see it.{where}"
        )
    if ended == "lifetime":
        how = f"was killed at its {took} lifetime"
    elif ended == "killed":
        how = f"was stopped after {took}"
    else:
        how = f"exited {exit_code} after {took}"
    return f"{head} {how}. job_output shows the output you have not seen."
