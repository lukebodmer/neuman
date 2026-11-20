"""Background jobs, from the core's side (D98).

The client holds the process; the core decides whether a call may become a job,
records that it did and how it ended, and says each of those to the model in as
few words as will do — because each of those words is re-sent with every later
request of the session. These pin the decisions and the words.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fakes import FakeEvent, FakeExecution, FakeGateway, FakeRegistry, manifest
from test_harness import ACTOR, _harness, _policy, _tool_call

from neuman_server.adapters.clients import ClientConnection, ClientHub
from neuman_server.decision.harness import project
from neuman_server.decision.harness.inbox import Inbox
from neuman_server.decision.harness.jobs import (
    detached_note,
    duration,
    ended_notice,
    refused_note,
)
from neuman_server.decision.harness.ports import DispatchOutcome

# -- the hub -------------------------------------------------------------------


class FakeChannel:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, message: dict) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        pass


async def _connected(hub: ClientHub) -> tuple[ClientConnection, FakeChannel]:
    channel = FakeChannel()
    connection = ClientConnection(
        channel,
        account="alice",
        tools=frozenset({"shell"}),
        home="/home/alice",
        drops="/home/alice/.local/state/neuman/drops",
    )
    await hub.register(connection)
    return connection, channel


async def _sent(channel: FakeChannel, count: int = 1) -> None:
    async with asyncio.timeout(5):
        while len(channel.sent) < count:
            await asyncio.sleep(0.01)


def _detached(dispatch_id: str, job_id: str = "7f3a9c") -> dict:
    return {
        "type": "detached",
        "rpc_version": 1,
        "dispatch_id": dispatch_id,
        "sequence": 1,
        "job_id": job_id,
        "wall_ms": 120_004,
        "lifetime_ms": 3_600_000,
        "persisted_out": "/spill/shell-d1.out",
        "persisted_err": "/spill/shell-d1.err",
    }


async def _detach(hub: ClientHub, connection: ClientConnection, channel: FakeChannel):
    dispatch = asyncio.create_task(
        hub.dispatch(
            user="alice",
            dispatch_id="d1c0ffee01",
            tool="shell",
            args={"command": "make"},
            wait_ms=120_000,
            lifetime_ms=3_600_000,
            surfaces={"read": [], "write": []},
            session_id="sess1",
            branch_id="b1",
        )
    )
    await _sent(channel)
    connection.deliver(
        {
            "type": "started",
            "rpc_version": 1,
            "dispatch_id": "d1c0ffee01",
            "sequence": 0,
            "pid": 7,
        }
    )
    connection.deliver(_detached("d1c0ffee01"))
    return await dispatch


async def test_the_dispatch_carries_both_deadlines_and_the_session():
    hub = ClientHub()
    connection, channel = await _connected(hub)
    await _detach(hub, connection, channel)

    sent = channel.sent[0]
    assert (sent["wait_ms"], sent["lifetime_ms"]) == (120_000, 3_600_000)
    assert sent["session_id"] == "sess1"
    # The branch is the core's own bookkeeping, not the client's business.
    assert "branch_id" not in sent


async def test_a_dispatch_without_a_lifetime_lives_as_long_as_it_waits():
    hub = ClientHub()
    connection, channel = await _connected(hub)
    dispatch = asyncio.create_task(
        hub.dispatch(
            user="alice",
            dispatch_id="d1c0ffee02",
            tool="shell",
            args={},
            wait_ms=5000,
            surfaces={"read": [], "write": []},
        )
    )
    await _sent(channel)
    connection.disconnect()
    await asyncio.gather(dispatch, return_exceptions=True)

    assert channel.sent[0]["lifetime_ms"] == 5000
    assert "session_id" not in channel.sent[0]


async def test_a_detached_dispatch_returns_the_job_and_is_tracked():
    hub = ClientHub()
    connection, channel = await _connected(hub)

    outcome = await _detach(hub, connection, channel)

    assert outcome.job_id == "7f3a9c"
    assert outcome.lifetime_ms == 3_600_000
    assert not outcome.ok
    job = connection.jobs["7f3a9c"]
    assert (job.session_id, job.branch_id, job.tool) == ("sess1", "b1", "shell")


async def test_a_settled_frame_reaches_the_recorder_once():
    hub = ClientHub()
    recorded: list[tuple] = []

    async def record(account, job, frame) -> None:
        recorded.append((account, job.job_id, frame["ended"], frame["observed"]))

    hub.on_job_settled = record
    connection, channel = await _connected(hub)
    await _detach(hub, connection, channel)

    settled = {
        "type": "job_settled",
        "rpc_version": 1,
        "job_id": "7f3a9c",
        "dispatch_id": "d1c0ffee01",
        "exit_code": 0,
        "wall_ms": 400_000,
        "ended": "exited",
        "observed": False,
    }
    connection.deliver(settled)
    connection.deliver(settled)  # a duplicate is not a second end
    await asyncio.sleep(0.01)

    assert recorded == [("alice", "7f3a9c", "exited", False)]
    assert connection.jobs == {}


async def test_a_settlement_read_before_its_detached_frame_is_not_lost():
    """Both frames can come off one read, and the settlement must survive it.

    `detached` wakes the dispatch that registers the job; a `job_settled`
    delivered in the same breath lands while `connection.jobs` is still empty.
    Dropping it would lose the only record of how the job ended — and, since
    nothing else would ever report it again, leave the session believing the
    job was still running until the socket closed.
    """

    hub = ClientHub()
    recorded: list[tuple] = []

    async def record(account, job, frame) -> None:
        recorded.append((account, job.job_id, frame["ended"], frame["observed"]))

    hub.on_job_settled = record
    connection, channel = await _connected(hub)
    dispatch = asyncio.create_task(
        hub.dispatch(
            user="alice",
            dispatch_id="d1c0ffee01",
            tool="shell",
            args={"command": "make"},
            wait_ms=120_000,
            lifetime_ms=3_600_000,
            surfaces={"read": [], "write": []},
            session_id="sess1",
            branch_id="b1",
        )
    )
    await _sent(channel)
    connection.deliver(
        {
            "type": "started",
            "rpc_version": 1,
            "dispatch_id": "d1c0ffee01",
            "sequence": 0,
            "pid": 7,
        }
    )
    # Both before the dispatch task has had a chance to run: the settlement is
    # delivered while the registry still knows nothing about the job.
    connection.deliver(_detached("d1c0ffee01"))
    connection.deliver(
        {
            "type": "job_settled",
            "rpc_version": 1,
            "job_id": "7f3a9c",
            "dispatch_id": "d1c0ffee01",
            "exit_code": 0,
            "wall_ms": 400_000,
            "ended": "exited",
            "observed": False,
        }
    )
    outcome = await dispatch
    await asyncio.sleep(0.01)

    assert outcome.job_id == "7f3a9c"
    assert recorded == [("alice", "7f3a9c", "exited", False)]
    assert connection.jobs == {}


async def test_a_dropped_connection_records_its_jobs_as_lost():
    hub = ClientHub()
    recorded: list[str] = []

    async def record(account, job, frame) -> None:
        recorded.append(frame["ended"])

    hub.on_job_settled = record
    connection, channel = await _connected(hub)
    await _detach(hub, connection, channel)

    connection.disconnect()
    await asyncio.sleep(0.01)

    assert recorded == ["disconnected"]


async def test_ending_a_sessions_jobs_cancels_them_by_their_dispatch():
    hub = ClientHub()
    connection, channel = await _connected(hub)
    await _detach(hub, connection, channel)

    ended = await hub.end_session_jobs(user="alice", session_id="sess1")
    other = await hub.end_session_jobs(user="alice", session_id="sess2")

    assert ended == ["7f3a9c"] and other == []
    assert channel.sent[-1] == {"type": "cancel", "rpc_version": 1, "dispatch_id": "d1c0ffee01"}


# -- the harness: whether a call may become a job --------------------------------


def _shell_registry(*extra):
    return FakeRegistry(
        [
            manifest(
                "shell",
                needs={"read", "write"},
                mutating=True,
                wait_ms=120_000,
                lifetime_ms=3_600_000,
            ),
            manifest("fs_read", needs={"read"}),
            *extra,
        ]
    )


JOB_TOOLS = (manifest("job_output", daemon=True), manifest("job_kill", daemon=True))


async def _run_shell(*, tools, outcome=None, session_id="sess1", registry=None):
    execution = FakeExecution(outcome or DispatchOutcome(exit_code=0, stdout="ok"))
    harness = _harness(
        gateway=FakeGateway([_tool_call("shell", '{"command": "make"}'), []]),
        execution=execution,
        registry=registry or _shell_registry(*JOB_TOOLS),
        policy=_policy(tools=tools, auto_accept="yolo"),
    )
    await harness.run_turn(
        branch_id="b1",
        actor=ACTOR,
        message="build it",
        model="m",
        inbox=Inbox("b1"),
        session_id=session_id,
    )
    return harness, execution


def _result(harness) -> dict:
    [result] = [
        event.payload
        for event in harness.event_repository.rows["b1"]
        if event.kind == "tool_result"
    ]
    return result


async def test_a_call_may_become_a_job_when_the_model_can_follow_it():
    _, execution = await _run_shell(tools=("shell", "fs_read", "job_output", "job_kill"))

    [dispatch] = execution.dispatches
    assert (dispatch["wait_ms"], dispatch["lifetime_ms"]) == (120_000, 3_600_000)
    assert dispatch["session_id"] == "sess1"
    assert dispatch["branch_id"] == "b1"


async def test_without_job_output_the_call_dies_at_its_wait():
    """A job the model cannot look at is a process it has lost."""

    _, execution = await _run_shell(tools=("shell", "fs_read"))

    [dispatch] = execution.dispatches
    assert dispatch["lifetime_ms"] == dispatch["wait_ms"] == 120_000
    assert dispatch["session_id"] is None


async def test_without_a_session_the_call_dies_at_its_wait():
    _, execution = await _run_shell(
        tools=("shell", "fs_read", "job_output", "job_kill"), session_id=None
    )

    [dispatch] = execution.dispatches
    assert dispatch["lifetime_ms"] == dispatch["wait_ms"]


async def test_a_tool_that_never_detaches_is_never_offered_a_lifetime():
    execution = FakeExecution(DispatchOutcome(exit_code=0, stdout="x"))
    harness = _harness(
        gateway=FakeGateway([_tool_call("fs_read", '{"path": "/srv/a"}'), []]),
        execution=execution,
        registry=_shell_registry(*JOB_TOOLS),
        policy=_policy(tools=("shell", "fs_read", "job_output", "job_kill")),
    )
    await harness.run_turn(
        branch_id="b1",
        actor=ACTOR,
        message="read",
        model="m",
        inbox=Inbox("b1"),
        session_id="s",
    )

    [dispatch] = execution.dispatches
    assert dispatch["lifetime_ms"] == dispatch["wait_ms"]
    assert dispatch["session_id"] is None


# -- the harness: what the model is told -----------------------------------------


async def test_a_detached_result_is_the_preview_and_one_sentence_recorded_first():
    harness, _ = await _run_shell(
        tools=("shell", "fs_read", "job_output", "job_kill"),
        outcome=DispatchOutcome(
            exit_code=0,
            stdout="compiling 1/40\ncompiling 2/40\n",
            stderr="warning: unused\n",
            wall_ms=120_004,
            persisted_out="/spill/shell-x.out",
            persisted_err="/spill/shell-x.err",
            job_id="7f3a9c",
            lifetime_ms=3_600_000,
        ),
    )

    content = _result(harness)["content"]
    assert (
        "compiling 1/40\ncompiling 2/40\nstderr:\nwarning: unused\n"
        "[still running after 2m00s, so it continues as background job 7f3a9c "
        "(killed at 1h00m if it has not ended). job_output with job_id '7f3a9c' waits "
        "for it and shows the output you have not seen; job_kill stops it.]"
    ) in content
    # The files are still being written; a look names them when it matters.
    assert "/spill/" not in content

    actions = [row.action for row in harness.audit_repository.rows]
    assert actions.index("job.detached") > actions.index("tool.dispatch")
    [detached] = [row for row in harness.audit_repository.rows if row.action == "job.detached"]
    assert detached.payload["job_id"] == "7f3a9c"
    assert detached.payload["session"] == "sess1"


async def test_a_detached_result_does_not_offer_a_kill_the_model_does_not_have():
    harness, _ = await _run_shell(
        tools=("shell", "fs_read", "job_output"),
        outcome=DispatchOutcome(
            exit_code=0, wall_ms=120_000, job_id="7f3a9c", lifetime_ms=3_600_000
        ),
    )

    assert "job_kill" not in _result(harness)["content"]


async def test_a_call_killed_for_want_of_a_slot_says_so():
    harness, _ = await _run_shell(
        tools=("shell", "fs_read", "job_output", "job_kill"),
        outcome=DispatchOutcome(exit_code=137, timed_out=True, wall_ms=120_001),
    )

    assert refused_note(120_000) in _result(harness)["content"]


async def test_a_call_that_could_never_detach_still_just_times_out():
    harness, _ = await _run_shell(
        tools=("shell", "fs_read"),
        outcome=DispatchOutcome(exit_code=137, timed_out=True, wall_ms=120_001),
    )

    content = _result(harness)["content"]
    assert "[timed out after 120001 ms]" in content
    assert "background" not in content


# -- the notices ---------------------------------------------------------------------


def test_the_sentences_are_short_and_say_what_to_do():
    assert duration(45_000) == "45s"
    assert duration(250_000) == "4m10s"
    assert duration(3_720_000) == "1h02m"

    assert detached_note("7f3a9c", wall_ms=120_000, lifetime_ms=3_600_000, can_kill=False) == (
        "[still running after 2m00s, so it continues as background job 7f3a9c (killed at "
        "1h00m if it has not ended). job_output with job_id '7f3a9c' waits for it and shows "
        "the output you have not seen.]"
    )
    assert ended_notice(
        "7f3a9c", tool="shell", ended="exited", exit_code=2, wall_ms=362_000
    ) == (
        "Neuman notice (not from the user): background job 7f3a9c (shell) exited 2 after "
        "6m02s. job_output shows the output you have not seen."
    )
    assert "killed at its 1h00m lifetime" in ended_notice(
        "7f3a9c", tool="shell", ended="lifetime", exit_code=137, wall_ms=3_600_000
    )
    lost = ended_notice(
        "7f3a9c",
        tool="shell",
        ended="disconnected",
        exit_code=0,
        wall_ms=0,
        persisted_out="/spill/shell-x.out",
    )
    assert "job_output can no longer see it" in lost
    assert lost.endswith("Its output so far is in /spill/shell-x.out.")


def _event(seq: int, kind: str, payload: dict) -> FakeEvent:
    return FakeEvent(seq=seq, ts=datetime.now(UTC), kind=kind, message_id=None, payload=payload)


def _notice(seq: int, text: str) -> FakeEvent:
    return _event(seq, "harness_event", {"type": "job_settled", "content": text})


def _call(seq: int, *ids: str) -> FakeEvent:
    return _event(
        seq,
        "response_final",
        {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": "shell", "arguments": "{}"},
                    }
                    for call_id in ids
                ],
            }
        },
    )


def _reply(seq: int, call_id: str) -> FakeEvent:
    return _event(
        seq, "tool_result", {"tool_call_id": call_id, "name": "shell", "content": "ok"}
    )


def test_a_notice_never_splits_a_run_of_tool_replies():
    messages = project(
        [
            _event(1, "user_message", {"content": "go"}),
            _call(2, "a", "b"),
            _reply(3, "a"),
            _notice(4, "job ended"),
            _reply(5, "b"),
        ]
    )

    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "tool", "user"]
    assert messages[-1]["content"] == "job ended"


def test_a_notice_joins_the_user_turn_after_it():
    """Two user turns in a row is a hard error on a strict chat template."""

    messages = project(
        [
            _event(1, "user_message", {"content": "go"}),
            _event(
                2, "response_final", {"message": {"role": "assistant", "content": "started"}}
            ),
            _notice(3, "job one ended"),
            _notice(4, "job two ended"),
            _event(5, "user_message", {"content": "and now?"}),
        ]
    )

    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[-1]["content"] == "job one ended\njob two ended\n\nand now?"


def test_a_reader_sees_the_notice_as_a_note():
    messages = project(
        [_event(1, "user_message", {"content": "go"}), _notice(2, "job ended")],
        for_display=True,
    )

    assert messages[-1]["role"] == "note"
    assert messages[-1]["content"] == "job ended"
