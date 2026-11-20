"""The WebSocket bridge: authentication on upgrade, and the D12 inbox.

This is the half of docs/decisions.md D12 that lives in transport. The decision-layer tests
prove the `Inbox` delivers messages; these prove the *socket* does — that a turn
runs as a task while the receive loop keeps reading, so an approval or an
interrupt arriving mid-turn is delivered rather than dropped.

That distinction is the whole reason D12 exists. v1's approval waited on a
`concurrent.futures.Future` inside a worker thread, and anything arriving while
it waited was discarded, so hard-interrupt steering was simply absent (gotchas
A test that only exercises the inbox object cannot tell the
difference between that design and this one.

Every frame the socket emits is also validated against the shared `ws` contract,
so the server and the chat UI cannot drift apart silently.
"""

from __future__ import annotations

import time

import pytest
from fakes import effectful
from surface import CATALOG, build_surface

from neuman_server.composition.app import RunningTurn
from neuman_server.decision.harness.ports import Chunk, DispatchOutcome

COOKIE = "neuman_session"


@pytest.fixture
def surface(monkeypatch, tmp_path):
    return build_surface(monkeypatch, tmp_path)


def validate_frame(frame: dict) -> None:
    """Assert one emitted frame matches `contracts/schemas/ws.schema.json`."""

    from jsonschema import ValidationError
    from neuman_contracts import ws as contract

    try:
        contract.validate(frame)
    except ValidationError as exc:
        pytest.fail(f"frame violates the ws contract: {frame}\n{exc.message}")


# Every frame the socket should ever emit in these tests arrives immediately —
# the gateway and the client are fakes with no latency. A read that does not
# return
# means the receive loop is blocked, which is the exact defect these tests
# exist to catch (a turn awaited inline instead of run as a task drops every
# mid-turn message). Bounding the wait turns that into a named failure instead
# of a hung CI job.
RECEIVE_TIMEOUT_S = 10.0


def receive(socket, *, timeout_s: float = RECEIVE_TIMEOUT_S) -> dict:
    """Receive one frame, failing rather than hanging if none arrives.

    `WebSocketTestSession.receive` blocks on an anyio stream through a portal
    and takes no timeout, so the bound is applied inside the portal where the
    receive actually happens.
    """

    import json as _json

    import anyio

    async def _read():
        with anyio.fail_after(timeout_s):
            return await socket._send_rx.receive()

    try:
        message = socket.portal.call(_read)
    except TimeoutError:
        pytest.fail(
            f"no frame within {timeout_s}s: the receive loop is blocked, so a "
            "message arriving mid-turn would be dropped (docs/decisions.md D12)"
        )

    if message["type"] == "websocket.close":  # pragma: no cover - explicit failure
        pytest.fail(f"socket closed while awaiting a frame: {message}")
    return _json.loads(message["text"])


def open_session(surface, client, *, owner: str = "alice") -> str:
    """Seed an agent session owned by ``owner`` and authenticate as them."""

    row = surface.agent_sessions.seed(account_id=f"acct-{owner}")
    token, _csrf = surface.login(owner)
    client.cookies.set(COOKIE, token)
    return row.id


def text_reply(text: str = "done") -> list[Chunk]:
    return [Chunk("content", {"text": text}), Chunk("finish", {"reason": "stop"})]


def tool_reply(name: str, call_id: str, args: str = "{}") -> list[Chunk]:
    return [
        Chunk(
            "tool_call",
            {"index": 0, "id": call_id, "function": {"name": name, "arguments": args}},
        ),
        Chunk("finish", {"reason": "tool_calls"}),
    ]


# -- authentication on upgrade ----------------------------------------------


def test_the_socket_refuses_an_unauthenticated_upgrade(surface):
    from starlette.websockets import WebSocketDisconnect

    with surface.client() as client:
        session_id = surface.agent_sessions.seed(account_id="acct-alice").id
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(f"/ws/branches/{session_id}"):
                pass
    assert caught.value.code == 4401


def test_the_socket_refuses_a_foreign_origin(surface):
    """Browsers do not apply CORS to WebSockets, so the upgrade checks it itself."""

    from starlette.websockets import WebSocketDisconnect

    with surface.client() as client:
        session_id = open_session(surface, client)
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(
                f"/ws/branches/{session_id}", headers={"origin": "https://evil.test"}
            ):
                pass
    assert caught.value.code == 4403


def test_the_socket_refuses_someone_elses_session(surface):
    """Ownership is checked on the upgrade, before any frame is accepted."""

    from starlette.websockets import WebSocketDisconnect

    with surface.client() as client:
        bob_session = surface.agent_sessions.seed(account_id="acct-bob").id
        token, _csrf = surface.login("alice")
        client.cookies.set(COOKIE, token)

        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(f"/ws/branches/{bob_session}"):
                pass
    assert caught.value.code == 4404


def test_the_socket_refuses_a_removed_session(surface):
    """Removal holds for every browser, not just the one that asked.

    A tab left open on another device still has the id and would otherwise
    reconnect to a session the user took off their list — making the removal
    look like it had failed. 4404 is the same answer as "no such session",
    which from this user's side is now true.
    """

    from datetime import UTC, datetime

    from starlette.websockets import WebSocketDisconnect

    with surface.client() as client:
        session_id = open_session(surface, client)
        # Marked directly rather than through the route: the removal route has
        # its own tests, and what this one is about is the upgrade check.
        surface.agent_sessions.rows[session_id].removed_at = datetime.now(UTC)

        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(f"/ws/branches/{session_id}"):
                pass
    assert caught.value.code == 4404


def test_the_owner_may_connect(surface):
    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "nonsense"})
            assert receive(socket)["type"] == "error"


# -- inbound validation -----------------------------------------------------


def test_an_unknown_message_type_is_rejected_without_closing(surface):
    """A malformed frame is the untrusted surface misbehaving, not a fatal error."""

    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "dispatch", "tool": "fs_write"})
            frame = receive(socket)
            assert frame == {"type": "error", "code": "unknown_message"}
            validate_frame(frame)

            # Still usable afterwards.
            socket.send_json({"type": "also_nonsense"})
            assert receive(socket)["code"] == "unknown_message"


def test_a_turn_needs_both_content_and_model(surface):
    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            for bad in ({"type": "message"}, {"type": "message", "content": "hi"}):
                socket.send_json(bad)
                frame = receive(socket)
                assert frame["code"] == "invalid_request"
                validate_frame(frame)


# -- a turn, end to end -----------------------------------------------------


def test_a_turn_streams_contract_valid_frames(surface):
    surface.gateway.scripts = [text_reply("hello")]

    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "message", "content": "hi", "model": "m"})

            seen = []
            while True:
                frame = receive(socket)
                validate_frame(frame)
                seen.append(frame["type"])
                if frame["type"] == "done":
                    break

    assert "user_message" in seen
    assert "token" in seen
    assert seen[-1] == "done"


def test_only_one_turn_runs_at_a_time(surface):
    """A second concurrent turn would race on the event log's sequence and the budget.

    The first turn is parked on an approval so it is genuinely in flight when
    the second message arrives — sending two messages back to back would
    otherwise let the first finish first and prove nothing.
    """

    surface.gateway.scripts = [tool_reply("fs_write", "call_1"), text_reply()]

    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "message", "content": "one", "model": "m"})
            # Drained to `tool_approval_required` rather than to `tool_proposal`:
            # a proposal is emitted before the gate runs, so it does not mean the
            # turn is parked yet, and the frame that does arrives after it. This
            # test wants the turn genuinely in flight.
            while receive(socket)["type"] != "tool_approval_required":
                pass

            # The first turn is now awaiting a verdict.
            socket.send_json({"type": "message", "content": "two", "model": "m"})
            frame = receive(socket)
            validate_frame(frame)
            assert frame["code"] == "turn_in_flight"

            # Let the parked turn finish so the socket closes cleanly.
            socket.send_json({"type": "approval", "tool_call_id": "call_1", "approved": False})
            while receive(socket)["type"] != "done":
                pass


def test_a_second_socket_cannot_start_a_turn_on_a_running_branch(surface):
    """The branch, not the connection, is what admits one turn at a time.

    The socket-local check this replaces let a second window start a turn
    while the first was mid-dispatch. Its request projected the first turn's
    assistant message before that turn had written its `tool` replies, and the
    provider refused it — then every later request on the branch was refused
    the same way, because the two user turns sat between the call and its
    result (N179). The refusal must be visible on the *second* socket, which
    is the one that tried to start the turn.
    """

    surface.gateway.scripts = [tool_reply("fs_write", "call_1"), text_reply()]

    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as first:
            first.send_json({"type": "message", "content": "one", "model": "m"})
            while receive(first)["type"] != "tool_approval_required":
                pass

            with client.websocket_connect(f"/ws/branches/{session_id}") as second:
                second.send_json({"type": "message", "content": "two", "model": "m"})
                frame = receive(second)
                validate_frame(frame)
                assert frame["code"] == "turn_in_flight"

            # The refusal started nothing, and the running turn is untouched.
            first.send_json({"type": "approval", "tool_call_id": "call_1", "approved": False})
            while receive(first)["type"] != "done":
                pass


def test_a_branch_accepts_another_turn_once_the_first_has_ended(surface):
    """The registry entry must go when its task does.

    A test that starts one turn per socket cannot see this: without the done
    callback the first turn would leave the branch `turn_in_flight` forever,
    and every later message would be refused. `done` is emitted before the task
    returns, so the test waits for the registry rather than racing it.
    """

    surface.gateway.scripts = [text_reply("first"), text_reply("second")]

    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "message", "content": "one", "model": "m"})
            while receive(socket)["type"] != "done":
                pass

            deadline = time.monotonic() + 5
            while (
                surface.services.running_turn(session_id) is not None
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            assert surface.services.running_turn(session_id) is None

            socket.send_json({"type": "message", "content": "two", "model": "m"})
            assert receive(socket)["type"] == "user_message"
            while receive(socket)["type"] != "done":
                pass


# -- the inbox, over the wire (D12) -----------------------------------------


def test_an_approval_arriving_mid_turn_is_delivered(surface):
    """The property v1 could not provide: a message received while a turn runs.

    The turn blocks on an approval for a mutating tool; the verdict is sent
    afterwards, on the same socket. If the receive loop were blocked by the
    running turn, this would deadlock until the approval timed out as a denial.
    """

    surface.gateway.scripts = [tool_reply("fs_write", "call_1"), text_reply("wrote it")]

    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "message", "content": "write", "model": "m"})

            # Wait for the proposal, which means the turn is now awaiting a verdict.
            while receive(socket)["type"] != "tool_proposal":
                pass

            socket.send_json({"type": "approval", "tool_call_id": "call_1", "approved": True})

            decision = None
            while True:
                frame = receive(socket)
                validate_frame(frame)
                if frame["type"] == "tool_decision":
                    decision = frame
                if frame["type"] == "done":
                    break

    assert decision is not None, "no decision frame; the approval was never delivered"
    assert decision["accepted"] is True
    assert decision["consent"] is True


def test_a_denial_arriving_mid_turn_stops_the_tool(surface):
    surface.gateway.scripts = [tool_reply("fs_write", "call_1"), text_reply("fine")]

    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "message", "content": "write", "model": "m"})
            while receive(socket)["type"] != "tool_proposal":
                pass

            socket.send_json(
                {
                    "type": "approval",
                    "tool_call_id": "call_1",
                    "approved": False,
                    "reason": "no",
                }
            )

            decision = None
            result = None
            while True:
                frame = receive(socket)
                validate_frame(frame)
                if frame["type"] == "tool_decision":
                    decision = frame
                if frame["type"] == "tool_result":
                    result = frame
                if frame["type"] == "done":
                    break

    assert decision is not None and decision["accepted"] is False
    # A denied call still reports back, so the model can respond to the refusal.
    assert result is not None
    assert result["exit_code"] == -1
    # `effectful` rather than the raw list: a mutating call is previewed
    # read-only before anyone is asked, so what must not have happened is the
    # write, not the dispatch.
    assert effectful(surface.services.execution.dispatches) == [], (
        "a denied tool was dispatched"
    )


def test_an_interrupt_ends_the_turn(surface):
    """An interrupt denies every pending decision rather than leaving it hanging."""

    surface.gateway.scripts = [tool_reply("fs_write", "call_1"), text_reply()]

    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "message", "content": "write", "model": "m"})
            while receive(socket)["type"] != "tool_proposal":
                pass

            socket.send_json({"type": "interrupt"})

            while True:
                frame = receive(socket)
                validate_frame(frame)
                if frame["type"] == "done":
                    assert frame["reason"] == "interrupted"
                    break

    assert effectful(surface.services.execution.dispatches) == [], (
        "an interrupted tool was dispatched"
    )


def test_a_new_turn_may_start_after_an_interrupt(surface):
    """The interrupt must be cleared, or the session is dead after one use."""

    surface.gateway.scripts = [
        tool_reply("fs_write", "call_1"),
        text_reply(),
        text_reply("second turn"),
    ]

    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "message", "content": "one", "model": "m"})
            while receive(socket)["type"] != "tool_proposal":
                pass
            socket.send_json({"type": "interrupt"})
            while receive(socket)["type"] != "done":
                pass

            socket.send_json({"type": "message", "content": "two", "model": "m"})
            reasons = []
            while True:
                frame = receive(socket)
                validate_frame(frame)
                if frame["type"] == "done":
                    reasons.append(frame["reason"])
                    break
                assert frame["type"] != "error", f"second turn refused: {frame}"

    assert reasons == ["stop"]


def test_steering_is_delivered_before_the_next_turn(surface):
    """A steer folds into the conversation — at the next turn, not mid-turn.

    Measured rather than assumed (gotchas N23). The turn task and this
    socket's receive loop share one event loop, and the turn runs its
    iterations without yielding long enough for a queued frame to be read, so
    a steer sent *during* a turn is drained at the start of the next one.

    Approvals and interrupts are unaffected, because both are awaited: the
    loop blocks on `await_approval` and checks `interrupted()` between chunks,
    which yields and lets the frame through. Those are the D12 properties, and
    the tests above cover them.

    This asserts the behaviour that exists. Folding a steer into a running
    turn is a harness design question — it interacts with what a steer should
    do to an in-flight tool call — not a bug to fix in passing.
    """

    surface.gateway.scripts = [
        text_reply("first turn"),
        text_reply("second turn"),
    ]

    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "message", "content": "one", "model": "m"})
            while receive(socket)["type"] != "done":
                pass

            socket.send_json({"type": "steer", "content": "the steer"})
            socket.send_json({"type": "message", "content": "two", "model": "m"})

            contents = []
            while True:
                frame = receive(socket)
                validate_frame(frame)
                if frame["type"] == "user_message":
                    contents.append(frame["content"])
                if frame["type"] == "done":
                    break

    assert "the steer" in contents, contents


# -- session titles, through the socket ---------------------------------------


def test_a_first_message_names_the_session(surface):
    """The wiring, not the helper.

    `_derive_title` has its own tests; this asserts the composition layer
    actually calls it. `set_title` existed from M7 with no callers, so every
    session in the list read "untitled" — a unit test of the derivation would
    have passed throughout.
    """

    surface.gateway.scripts = [text_reply("hello")]

    with surface.client() as client:
        session_id = open_session(surface, client)
        assert surface.agent_sessions.rows[session_id].title is None

        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json(
                {"type": "message", "content": "read the config file", "model": "m"}
            )
            while True:
                frame = receive(socket)
                if frame["type"] == "done":
                    break

        assert surface.agent_sessions.rows[session_id].title == "read the config file"


def test_a_second_message_does_not_rename_the_session(surface):
    """Only the opening message names it.

    Otherwise the label would follow the conversation, and a session would be
    hard to find again precisely because you had used it more.
    """

    surface.gateway.scripts = [text_reply("one"), text_reply("two")]

    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            for content in ("the first thing", "a completely different thing"):
                socket.send_json({"type": "message", "content": content, "model": "m"})
                while True:
                    if receive(socket)["type"] == "done":
                        break

        assert surface.agent_sessions.rows[session_id].title == "the first thing"


def test_an_attached_file_reaches_the_model_request(surface):
    """End to end: a path on the session appears in the system prompt.

    The chain is column → store → ws → run_turn → build_request → system_prompt,
    and a break anywhere in it leaves a feature that stores a list nothing reads.
    Every unit along the way has its own test and all of them would still pass.
    """

    surface.gateway.scripts = [text_reply("ok")]

    with surface.client() as client:
        session_id = open_session(surface, client)
        surface.agent_sessions.branches[session_id].attachments = ("/srv/eng/spec.pdf",)

        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "message", "content": "summarise it", "model": "m"})
            while True:
                if receive(socket)["type"] == "done":
                    break

    assert surface.gateway.requests, "the gateway was never called"
    system = surface.gateway.requests[0]["messages"][0]
    assert system["role"] == "system", system
    assert "/srv/eng/spec.pdf" in system["content"]


def test_a_detached_file_is_gone_from_the_next_turn(surface):
    """Read per turn, not cached at connect.

    Attaching mid-conversation must take effect on the next message, and detaching
    must actually detach — a list captured once at socket open would do neither.
    """

    surface.gateway.scripts = [text_reply("one"), text_reply("two")]

    with surface.client() as client:
        session_id = open_session(surface, client)
        surface.agent_sessions.branches[session_id].attachments = ("/srv/eng/spec.pdf",)

        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "message", "content": "first", "model": "m"})
            while True:
                if receive(socket)["type"] == "done":
                    break

            surface.agent_sessions.branches[session_id].attachments = ()

            socket.send_json({"type": "message", "content": "second", "model": "m"})
            while True:
                if receive(socket)["type"] == "done":
                    break

    first, second = surface.gateway.requests[0], surface.gateway.requests[1]
    assert "/srv/eng/spec.pdf" in first["messages"][0]["content"]
    assert "/srv/eng/spec.pdf" not in second["messages"][0]["content"]


def test_a_read_only_tool_is_approvable_under_auto_accept_never(monkeypatch, tmp_path):
    """`auto_accept = "never"` routes *reads* through consent too.

    Every other approval test uses a mutating tool under the default `safe`,
    where reads bypass consent entirely — so the path a cautious operator's
    appliance takes on every single tool call had no test at all. That is the
    configuration a new deployment is most likely to choose.
    """

    baseline = {
        "schema_version": 2,
        "defaults": {
            "tools": ["fs_read", "fs_list"],
            "files_read": ["/srv/eng"],
            "files_write": [],
            "auto_accept": "never",
        },
        "roles": {},
        "users": [{"name": "alice"}],
        **CATALOG,
    }

    surface = build_surface(monkeypatch, tmp_path, baseline=baseline)
    surface.gateway.scripts = [tool_reply("fs_list", "call_1"), text_reply("listed")]

    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "message", "content": "list it", "model": "m"})

            while receive(socket)["type"] != "tool_proposal":
                pass

            socket.send_json({"type": "approval", "tool_call_id": "call_1", "approved": True})

            decision = None
            while True:
                frame = receive(socket)
                validate_frame(frame)
                if frame["type"] == "tool_decision":
                    decision = frame
                if frame["type"] == "done":
                    break

    assert decision is not None, "no decision frame; the approval was never delivered"
    assert decision["accepted"] is True, decision
    assert [d["tool"] for d in surface.services.execution.dispatches] == ["fs_list"]


# -- who is asked, and what the client is told (N89) --------------------------
#
# The client draws approve/deny controls on a frame and removes them on another.
# Getting the pair wrong leaves a card offering a choice that was already made:
# under `yolo` nothing emitted a decision at all, so the buttons stayed for the
# life of the session and pressing either did nothing, because no inbox was
# waiting for a verdict.


def _collect(surface, client, *, until: str, limit: int = 40) -> list[dict]:
    """Every frame of one turn, up to and including `until`."""

    session_id = open_session(surface, client)
    frames: list[dict] = []
    with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
        socket.send_json({"type": "message", "content": "go", "model": "m"})
        for _ in range(limit):
            frame = receive(socket)
            validate_frame(frame)
            frames.append(frame)
            if frame["type"] == until:
                break
        else:  # pragma: no cover - a turn that never finishes is a failure
            raise AssertionError(f"never saw {until}: {[f['type'] for f in frames]}")
    return frames


def test_an_auto_approved_call_reports_its_decision(surface, tmp_path, monkeypatch):
    """The bug. A call nobody is asked about must still say it was decided.

    `_needs_consent` returning false recorded the decision and emitted nothing,
    so the client never learned the verdict and kept its buttons. The contract
    already described this case — "consent=false means the gate decided alone" —
    so the frame was specified and simply never sent.
    """

    baseline = {
        "schema_version": 2,
        "defaults": {
            "tools": ["fs_read", "fs_write"],
            "files_read": ["/srv/eng"],
            "files_write": ["/srv/eng/work"],
            "auto_accept": "yolo",
        },
        "roles": {},
        "users": [{"name": "alice"}],
        **CATALOG,
    }
    running = build_surface(monkeypatch, tmp_path, baseline=baseline)
    # `fs_write` is mutating, so under `safe` it would ask; under `yolo` it must
    # not. Choosing a mutating tool is what makes the mode the variable.
    running.gateway.scripts = [tool_reply("fs_write", "call_1"), text_reply()]

    with running.client() as client:
        frames = _collect(running, client, until="tool_decision")

    kinds = [frame["type"] for frame in frames]
    assert "tool_approval_required" not in kinds, f"a human was asked under yolo: {kinds}"

    decision = frames[-1]
    assert decision["accepted"] is True
    assert decision["consent"] is False, "an auto-approved call claimed consent"


def test_a_call_needing_approval_says_so_before_blocking(surface):
    """The frame that reveals the controls, and its order.

    It has to arrive *after* the proposal — the client needs a card to reveal
    them on — and *before* the harness blocks, or the controls appear only once
    the verdict has already timed out.
    """

    surface.gateway.scripts = [tool_reply("fs_write", "call_1"), text_reply()]

    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "message", "content": "go", "model": "m"})

            kinds: list[str] = []
            while True:
                frame = receive(socket)
                validate_frame(frame)
                kinds.append(frame["type"])
                if frame["type"] == "tool_approval_required":
                    assert frame["tool_call_id"] == "call_1"
                    break

            assert "tool_proposal" in kinds, kinds
            assert kinds.index("tool_proposal") < kinds.index("tool_approval_required")
            # Nothing has been decided yet: the harness is parked.
            assert "tool_decision" not in kinds, kinds

            socket.send_json({"type": "approval", "tool_call_id": "call_1", "approved": True})
            while receive(socket)["type"] != "done":
                pass


def test_a_read_only_call_under_safe_is_never_asked_about(surface):
    """The common case, and the one that flashed buttons.

    Under the default `safe` a read-only tool is auto-approved — so it gets a
    decision with `consent: false` and no approval frame, exactly like `yolo`.
    The difference between the modes is *which* calls take this path, not
    whether the path exists.
    """

    surface.gateway.scripts = [tool_reply("fs_read", "call_1"), text_reply()]

    with surface.client() as client:
        frames = _collect(surface, client, until="tool_decision")

    kinds = [frame["type"] for frame in frames]
    assert "tool_approval_required" not in kinds, kinds
    assert frames[-1]["consent"] is False


# -- reasoning levels -------------------------------------------------------


def _reasoning_baseline(declaration):
    """A baseline granting one model, declared with `declaration`."""

    model = {
        "provider": "p",
        "provider_model": "provider/m",
        "context_tokens": 100000,
    }
    if declaration is not None:
        model["reasoning"] = declaration
    return {
        "schema_version": 2,
        "defaults": {
            "tools": ["fs_read"],
            "files_read": ["/srv/eng"],
            "files_write": [],
            "auto_accept": "safe",
            "models": ["m"],
        },
        "roles": {},
        "users": [{"name": "alice"}],
        "providers": {"p": {"url": "https://cloud.test/v1", "api_key_file": None}},
        "models": {"m": model},
    }


def _run(running, frame):
    """Drive one turn to `done` and return the frames it produced."""

    with running.client() as client:
        session_id = open_session(running, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json(frame)
            frames = []
            while True:
                received = receive(socket)
                validate_frame(received)
                frames.append(received)
                if received["type"] == "done":
                    return frames


def test_a_sessions_tool_selection_narrows_what_the_turn_advertises(surface):
    """The session row reaches the request, and a deselected tool disappears.

    Read from the row per turn rather than carried on the frame, because the
    selection persists until the user changes it (D50) — so switching a tool off
    takes effect on the next message the way a revocation does, and a reconnect
    does not reset it.

    Asserted on the request the provider would have received: the point of the
    feature is that the schema is *absent*, not present and refused.
    """

    surface.gateway.scripts = [text_reply("hello")]

    with surface.client() as client:
        row = surface.agent_sessions.seed(account_id="acct-alice")
        # The baseline grants `fs_read` and `fs_write`; the user wants only one.
        surface.agent_sessions.branches[row.branch_id].tool_selection = ("fs_read",)
        token, _csrf = surface.login("alice")
        client.cookies.set(COOKIE, token)

        with client.websocket_connect(f"/ws/branches/{row.branch_id}") as socket:
            socket.send_json({"type": "message", "content": "hi", "model": "m"})
            while receive(socket)["type"] != "done":
                pass

    request = surface.gateway.requests[0]
    assert [t["function"]["name"] for t in request["tools"]] == ["fs_read"], request
    assert "fs_write" not in request["messages"][0]["content"]


def test_a_session_with_no_selection_advertises_every_granted_tool(surface):
    """The default, through the whole stack.

    A NULL column must not be read anywhere as "nothing selected": that would
    silently disarm every session that never opened the chooser.
    """

    surface.gateway.scripts = [text_reply("hello")]

    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "message", "content": "hi", "model": "m"})
            while receive(socket)["type"] != "done":
                pass

    request = surface.gateway.requests[0]
    assert sorted(t["function"]["name"] for t in request["tools"]) == ["fs_read", "fs_write"]


def test_the_accounts_instructions_reach_the_model_request(surface):
    """The whole chain: an account row becomes a sentence in the system prompt.

    Asserted on the request the provider would have received, not on the turn
    completing. The fake answers anything, so an outcome-only test could not tell
    a prompt that carries the text from one that silently dropped it.
    """

    surface.gateway.scripts = [text_reply("hello")]
    surface.accounts.rows["alice"].instructions = "ACCOUNT-INSTRUCTIONS"

    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "message", "content": "hi", "model": "m"})
            while receive(socket)["type"] != "done":
                pass

    system = surface.gateway.requests[0]["messages"][0]["content"]
    assert "ACCOUNT-INSTRUCTIONS" in system


def test_an_edit_to_the_instructions_reaches_the_next_turn(surface):
    """Read per turn, so a change in Settings applies without a reconnect.

    The same reason the tool selection and the attachments are read from the row
    every turn: this text belongs to the account, and a preference that needed a
    new session to take effect would look broken in the session it was typed in.
    """

    surface.gateway.scripts = [text_reply("one"), text_reply("two")]
    surface.accounts.rows["alice"].instructions = "INSTRUCTION-FIRST"

    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "message", "content": "hi", "model": "m"})
            while receive(socket)["type"] != "done":
                pass

        surface.accounts.rows["alice"].instructions = "INSTRUCTION-SECOND"

        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "message", "content": "again", "model": "m"})
            while receive(socket)["type"] != "done":
                pass

    first = surface.gateway.requests[0]["messages"][0]["content"]
    second = surface.gateway.requests[1]["messages"][0]["content"]
    assert "INSTRUCTION-FIRST" in first
    assert "INSTRUCTION-SECOND" not in first
    assert "INSTRUCTION-SECOND" in second


EFFORT = {"control": "effort", "levels": ["low", "high"], "default": "low"}


def test_a_chosen_reasoning_level_reaches_the_model_request(monkeypatch, tmp_path):
    """The whole chain: a frame field becomes a provider's own request field.

    Asserted on the request the gateway *received*, not on the turn completing.
    The fake answers anything, so a test checking only the outcome could not
    see a level accepted by the surface and dropped before the wire — the shape
    of N48, N86, N89 and N94.
    """

    running = build_surface(monkeypatch, tmp_path, baseline=_reasoning_baseline(EFFORT))
    running.gateway.scripts = [text_reply("hello")]

    _run(running, {"type": "message", "content": "hi", "model": "m", "reasoning": "high"})

    assert running.gateway.requests, "the gateway received no request"
    assert running.gateway.requests[0]["reasoning_effort"] == "high", running.gateway.requests[
        0
    ]


def test_an_unchosen_level_sends_the_models_declared_default(monkeypatch, tmp_path):
    """A user who expressed no preference gets the operator's."""

    running = build_surface(monkeypatch, tmp_path, baseline=_reasoning_baseline(EFFORT))
    running.gateway.scripts = [text_reply("hello")]

    _run(running, {"type": "message", "content": "hi", "model": "m"})

    assert running.gateway.requests[0]["reasoning_effort"] == "low"


def test_a_model_that_declares_none_is_sent_no_reasoning_field(monkeypatch, tmp_path):
    """The ordinary model. An absent field is what lets the provider's own
    default stand — a request that always carried one would override it."""

    running = build_surface(monkeypatch, tmp_path, baseline=_reasoning_baseline(None))
    running.gateway.scripts = [text_reply("hello")]

    _run(running, {"type": "message", "content": "hi", "model": "m"})

    request = running.gateway.requests[0]
    assert "reasoning_effort" not in request, request
    assert "thinking" not in request, request
    assert "chat_template_kwargs" not in request, request


def test_an_undeclared_level_ends_the_turn_before_the_provider_sees_it(monkeypatch, tmp_path):
    """Refused by name, with nothing sent.

    The alternative is the provider's own error for an unrecognised effort
    string, arriving mid-turn in the provider's dialect after the prompt is
    already paid for.
    """

    running = build_surface(monkeypatch, tmp_path, baseline=_reasoning_baseline(EFFORT))
    running.gateway.scripts = [text_reply("hello")]

    frames = _run(
        running,
        {"type": "message", "content": "hi", "model": "m", "reasoning": "ludicrous"},
    )

    codes = [f.get("code") for f in frames if f["type"] == "error"]
    assert "reasoning_error" in codes, frames
    assert not running.gateway.requests, "a refused level still reached the provider"


def test_the_deepseek_dialect_sends_both_of_its_fields(monkeypatch, tmp_path):
    """The envelope alone runs at the provider's default depth; the string
    alone is ignored by a provider that gates on the envelope. Sending one
    without the other is the failure this dialect exists to name."""

    running = build_surface(
        monkeypatch,
        tmp_path,
        baseline=_reasoning_baseline(
            {"control": "budget", "levels": ["low", "high"], "default": "low"},
        ),
    )
    running.gateway.scripts = [text_reply("hello")]

    _run(running, {"type": "message", "content": "hi", "model": "m", "reasoning": "high"})

    request = running.gateway.requests[0]
    assert request["thinking"] == {"type": "enabled"}, request
    assert request["reasoning_effort"] == "high", request


# -- the working set, and the turns in it ------------------------------------
#
# `GET /api/active` is the rail's read (D63): the account's working set, of
# which a running turn is one part. These live here because a turn has to be
# genuinely in flight to test one, and only a socket can start a turn: a reply
# the fake gateway can finish instantly is over before the next request is made,
# so a turn parked on an approval is the only observable one.


class _Unfinished:
    """A task that has not finished, for the registry's `done()` check."""

    def done(self) -> bool:
        return False


def _answer_and_finish(socket, call_id: str = "call_1") -> None:
    """Answer a parked approval and drain to `done`.

    A socket closed mid-turn cancels the turn, and a cancelled turn leaves the
    registry in a state a later assertion could not tell from a bug.
    """

    socket.send_json({"type": "approval", "tool_call_id": call_id, "approved": False})
    while receive(socket)["type"] != "done":
        pass


def _active(client) -> dict:
    return {row["id"]: row for row in client.get("/api/active").json()["sessions"]}


def test_a_blocked_turn_is_in_the_working_set_with_the_tool_it_asks_about(surface):
    """The account's own read says a call is waiting, and on what.

    Before this, "a turn is running" was a fact only the operator's overview
    could see: a second window showed the session as ordinary history, so a call
    waiting for a person was invisible in the window the person was looking at.
    The registry is the same one `turn_in_flight` consults (N179), so there is
    still one answer to what is running.
    """

    surface.gateway.scripts = [tool_reply("fs_write", "call_1"), text_reply()]

    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "message", "content": "one", "model": "m"})
            while receive(socket)["type"] != "tool_approval_required":
                pass

            row = _active(client)[session_id]

            # The fake's root branch shares the session's id, so one id names
            # both; production gives them different ones and the read carries
            # both for that reason.
            assert row["state"] == "approval", row
            assert row["tool"] == "fs_write", row
            assert row["branch_id"] == session_id, row

            _answer_and_finish(socket)


def test_a_finished_turn_leaves_the_session_idle_in_the_working_set(surface):
    """A turn ending does not empty the rail: the session was just used.

    It drops from `running` to `idle` — and to `finished` when the reply is
    unseen, which is what `read_at` decides (see `test_active_surface`).
    """

    surface.gateway.scripts = [tool_reply("fs_write", "call_1"), text_reply()]

    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "message", "content": "one", "model": "m"})
            while receive(socket)["type"] != "tool_approval_required":
                pass

            assert _active(client)[session_id]["state"] == "approval"

            _answer_and_finish(socket)

            row = _active(client)[session_id]
            assert row["state"] in ("idle", "finished"), row
            assert surface.services.running_turn(session_id) is None


def test_the_working_set_shows_only_the_callers_own_sessions(surface):
    """The read is scoped by account, like every other session route.

    An unscoped read would tell one person what another is doing — and the
    branch id it carries is enough to try to open the session with.
    """

    surface.gateway.scripts = [tool_reply("fs_write", "call_1"), text_reply()]

    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as socket:
            socket.send_json({"type": "message", "content": "one", "model": "m"})
            while receive(socket)["type"] != "tool_approval_required":
                pass

            # Asserted for the owner first, so the empty answer below is a
            # refusal rather than a read that never finds anything.
            assert session_id in _active(client)

            bob_token, _bob_csrf = surface.login("bob")
            client.cookies.set(COOKIE, bob_token)
            assert _active(client) == {}

            _answer_and_finish(socket)


def test_a_removed_session_leaves_the_working_set(surface):
    """A removed session must not come back as a row, turn or no turn.

    Its turn keeps running — removal ends the viewing, not the work — so the
    registry still names it. Without the listing's `removed_at` scope the rail
    would put back a row the user tidied, and opening it would answer
    `not_found`.
    """

    surface.gateway.scripts = [tool_reply("fs_write", "call_1"), text_reply()]

    with surface.client() as client:
        token, csrf = surface.login("alice")
        client.cookies.set(COOKIE, token)
        row = surface.agent_sessions.seed(account_id="acct-alice")
        with client.websocket_connect(f"/ws/branches/{row.branch_id}") as socket:
            socket.send_json({"type": "message", "content": "one", "model": "m"})
            while receive(socket)["type"] != "tool_approval_required":
                pass

            assert row.id in _active(client)

            removed = client.post(
                f"/api/sessions/{row.id}/remove", json={}, headers={"x-csrf-token": csrf}
            )
            assert removed.status_code == 200

            assert _active(client) == {}

            _answer_and_finish(socket)


def test_a_turn_with_nothing_pending_reads_as_running(surface):
    """`approval` means a verdict is waiting, not merely that a turn is alive.

    Set directly rather than driven through a socket: a turn with no approval
    waiting finishes in microseconds against the fake gateway, and the wiring
    from socket to registry is the first test's job.
    """

    with surface.client() as client:
        row = surface.agent_sessions.seed(account_id="acct-alice")
        token, _csrf = surface.login("alice")
        client.cookies.set(COOKIE, token)
        surface.services.turns[row.branch_id] = RunningTurn(
            account="alice",
            account_id="acct-alice",
            session_id=row.id,
            inbox=surface.services.inbox_for(row.branch_id),
            task=_Unfinished(),
        )

        row = _active(client)[row.id]
        assert row["state"] == "running", row
        assert row["tool"] is None, row


# -- the card a blocked turn is asking --------------------------------------
#
# The verdict is the account's, and the inbox that takes it is the branch's, so
# any socket on the branch can deliver one. What a second window lacked was the
# *prompt*: the card, with what the model asked for and the diff the write would
# make. These assert that it is served, that it is the same preview the owning
# window was given rather than a second dispatch, and that a verdict from that
# window ends the wait (D63).


def _diff_outcome() -> DispatchOutcome:
    return DispatchOutcome(
        exit_code=0,
        stdout=('planned the write\n{"neuman_diff": {"path": "/tmp/x", "diff": "-a\\n+b\\n"}}'),
    )


def test_a_blocked_branch_serves_the_card_another_window_draws(surface):
    """The preview is carried, not recomputed.

    Resolving a preview is a second dispatch to the human's machine
    (`Harness._preview_write`). Serving a fresh one would be a different tool
    run, and it could describe a different write than the call the verdict
    applies to — so the assertion is equality with the frame the owning window
    received, not merely that a preview is present.
    """

    surface.gateway.scripts = [
        tool_reply("fs_write", "call_1", '{"path": "/tmp/x"}'),
        text_reply(),
    ]
    surface.services.execution.outcomes["fs_write"] = _diff_outcome()

    with surface.client() as client:
        row = surface.agent_sessions.seed(account_id="acct-alice")
        token, _csrf = surface.login("alice")
        client.cookies.set(COOKIE, token)
        with client.websocket_connect(f"/ws/branches/{row.branch_id}") as socket:
            socket.send_json({"type": "message", "content": "one", "model": "m"})
            while True:
                frame = receive(socket)
                if frame["type"] == "tool_approval_required":
                    break

            served = client.get(f"/api/branches/{row.branch_id}/pending").json()["pending"]

            assert served == [
                {
                    "tool_call_id": "call_1",
                    "tool": "fs_write",
                    "arguments": {"path": "/tmp/x"},
                    "preview": frame.get("preview"),
                }
            ], served
            # The frame did carry one, so the equality above is about the same
            # diff and not two nulls.
            assert frame.get("preview"), frame

            _answer_and_finish(socket)


def test_a_verdict_from_another_window_ends_the_wait(surface):
    """A second socket on the branch delivers the approval the first is waiting on.

    This is what makes the card answerable where it is drawn: the inbox is per
    branch, and `ws` posts every non-message kind into it, so the socket that
    carries the verdict need not be the one that started the turn. The second
    socket must not disturb the turn's life either — it owns nothing, so its
    close cancels nothing (D63).
    """

    surface.gateway.scripts = [tool_reply("fs_write", "call_1"), text_reply()]

    with surface.client() as client:
        session_id = open_session(surface, client)
        with client.websocket_connect(f"/ws/branches/{session_id}") as first:
            first.send_json({"type": "message", "content": "one", "model": "m"})
            while receive(first)["type"] != "tool_approval_required":
                pass

            with client.websocket_connect(f"/ws/branches/{session_id}") as second:
                second.send_json(
                    {"type": "approval", "tool_call_id": "call_1", "approved": False}
                )

            # The turn the *first* socket owns carries on and ends: the verdict
            # went into the branch's inbox, which is where the harness waits.
            # `consent` is what says a person was asked, and reaching `done`
            # inside this test's receive timeout is what says the verdict landed
            # rather than the turn timing out as a denial.
            decided = None
            while True:
                frame = receive(first)
                if frame["type"] == "tool_decision":
                    decided = frame
                if frame["type"] == "done":
                    break

            assert decided is not None, "the parked call was never decided"
            assert decided["accepted"] is False and decided["consent"] is True, decided
            assert surface.services.running_turn(session_id) is None


def test_pending_is_scoped_to_the_branchs_owner(surface):
    """The card is reachable only by the account that owns the branch.

    It carries the arguments and the diff of a write — a path and its contents —
    so an unscoped read would hand one person another's work in progress.
    """

    surface.gateway.scripts = [tool_reply("fs_write", "call_1"), text_reply()]

    with surface.client() as client:
        row = surface.agent_sessions.seed(account_id="acct-alice")
        token, _csrf = surface.login("alice")
        client.cookies.set(COOKIE, token)
        with client.websocket_connect(f"/ws/branches/{row.branch_id}") as socket:
            socket.send_json({"type": "message", "content": "one", "model": "m"})
            while receive(socket)["type"] != "tool_approval_required":
                pass

            bob_token, _bob_csrf = surface.login("bob")
            client.cookies.set(COOKIE, bob_token)
            assert client.get(f"/api/branches/{row.branch_id}/pending").status_code == 404

            client.cookies.set(COOKIE, token)
            assert client.get(f"/api/branches/{row.branch_id}/pending").status_code == 200

            _answer_and_finish(socket)
