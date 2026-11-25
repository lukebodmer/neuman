"""The hub and the client connection, driven through a channel.

The hub is transport-shaped: a connection owns a `Channel`, and whoever reads the
socket calls `deliver`. That is what makes it testable without a WebSocket, and
these tests exercise the parts that decide anything — routing by dispatch id, the
sequence check, the handshake-gated tool list, and the disconnect path.
"""

from __future__ import annotations

import asyncio
import base64

import pytest

from neuman_server.adapters.clients import (
    ClientConnection,
    ClientError,
    ClientHub,
    ClientUnavailable,
)


class FakeChannel:
    """A channel that records what the hub sent and whether it was closed."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    async def send(self, message: dict) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True


async def _connected(
    hub: ClientHub,
    *,
    machine: str = "ernest",
    tools: frozenset[str] = frozenset({"fs_read"}),
    home: str = "/home/alice",
) -> tuple[ClientConnection, FakeChannel]:
    channel = FakeChannel()
    connection = ClientConnection(
        channel,
        account="alice",
        machine=machine,
        tools=tools,
        home=home,
        drops="/home/alice/.local/state/neuman/drops",
    )
    await hub.register(connection)
    return connection, channel


async def _wait_for_sent(channel: FakeChannel) -> None:
    async with asyncio.timeout(5):
        while not channel.sent:
            await asyncio.sleep(0.01)


def _started(dispatch_id: str, sequence: int = 0) -> dict:
    return {
        "type": "started",
        "rpc_version": 2,
        "dispatch_id": dispatch_id,
        "sequence": sequence,
        "pid": 4242,
    }


async def test_a_dispatch_round_trips():
    hub = ClientHub()
    connection, channel = await _connected(hub)

    dispatch = asyncio.create_task(
        hub.dispatch(
            user="alice",
            dispatch_id="d12345678",
            tool="fs_read",
            args={"path": "/tmp/x"},
            wait_ms=5000,
            surfaces={"read": [], "write": []},
        )
    )
    await _wait_for_sent(channel)
    assert channel.sent[0]["type"] == "dispatch"
    assert channel.sent[0]["tool"] == "fs_read"

    connection.deliver(_started("d12345678"))
    connection.deliver(
        {
            "type": "stdout",
            "rpc_version": 2,
            "dispatch_id": "d12345678",
            "sequence": 1,
            "chunk": base64.b64encode(b"hi").decode(),
        }
    )
    connection.deliver(
        {
            "type": "result",
            "rpc_version": 2,
            "dispatch_id": "d12345678",
            "sequence": 2,
            "exit_code": 0,
            "wall_ms": 3,
            "timed_out": False,
            "cancelled": False,
            "truncated": False,
        }
    )

    outcome = await dispatch
    assert outcome.stdout == "hi"
    assert outcome.exit_code == 0


async def test_a_dispatch_speaks_the_shape_the_connection_settled_on():
    """The frame's version is the connection's, not the module's (D111).

    One build can serve a client one shape behind, and every frame on that
    socket has to be a shape that client can read. Pinned here with a version
    this build does not itself speak, so the test proves the plumbing rather
    than the constant.
    """

    hub = ClientHub()
    channel = FakeChannel()
    connection = ClientConnection(
        channel,
        account="alice",
        machine="ernest",
        tools=frozenset({"fs_read"}),
        home="/home/alice",
        drops="/home/alice/.local/state/neuman/drops",
        rpc_version=7,
        release="0.0.7",
    )
    await hub.register(connection)

    dispatch = asyncio.create_task(
        hub.dispatch(
            user="alice",
            dispatch_id="d12345678",
            tool="fs_read",
            args={"path": "/tmp/x"},
            wait_ms=5000,
            surfaces={"read": [], "write": []},
        )
    )
    await _wait_for_sent(channel)
    assert channel.sent[0]["rpc_version"] == 7

    await hub.cancel(user="alice", dispatch_id="d12345678")
    assert channel.sent[1] == {
        "type": "cancel",
        "rpc_version": 7,
        "dispatch_id": "d12345678",
    }

    connection.deliver(_started("d12345678"))
    connection.deliver(
        {
            "type": "result",
            "rpc_version": 7,
            "dispatch_id": "d12345678",
            "sequence": 1,
            "exit_code": 0,
            "wall_ms": 1,
            "timed_out": False,
            "cancelled": False,
            "truncated": False,
        }
    )
    await dispatch


async def test_a_dispatch_with_no_connected_client_is_unavailable():
    hub = ClientHub()
    with pytest.raises(ClientUnavailable):
        await hub.dispatch(
            user="alice",
            dispatch_id="d12345678",
            tool="fs_read",
            args={},
            wait_ms=5000,
            surfaces={"read": [], "write": []},
        )


async def test_a_tool_the_client_did_not_advertise_is_refused_before_sending():
    hub = ClientHub()
    _connection, channel = await _connected(hub, tools=frozenset({"fs_read"}))

    with pytest.raises(ClientUnavailable):
        await hub.dispatch(
            user="alice",
            dispatch_id="d12345678",
            tool="shell",
            args={},
            wait_ms=5000,
            surfaces={"read": [], "write": []},
        )
    assert channel.sent == []


async def test_a_dispatch_without_surfaces_is_refused_before_sending():
    hub = ClientHub()
    _connection, channel = await _connected(hub)

    with pytest.raises(ClientError):
        await hub.dispatch(
            user="alice",
            dispatch_id="d12345678",
            tool="fs_read",
            args={},
            wait_ms=5000,
        )
    assert channel.sent == []


async def test_a_sequence_gap_fails_the_dispatch():
    hub = ClientHub()
    connection, channel = await _connected(hub)

    dispatch = asyncio.create_task(
        hub.dispatch(
            user="alice",
            dispatch_id="d12345678",
            tool="fs_read",
            args={},
            wait_ms=5000,
            surfaces={"read": [], "write": []},
        )
    )
    await _wait_for_sent(channel)
    connection.deliver(_started("d12345678"))
    # 2 where 1 is due: one chunk was lost, and the reader must not present the
    # rest as complete output (D26).
    connection.deliver(
        {
            "type": "stdout",
            "rpc_version": 2,
            "dispatch_id": "d12345678",
            "sequence": 2,
            "chunk": base64.b64encode(b"hi").decode(),
        }
    )

    with pytest.raises(ClientError, match="gap"):
        await dispatch


async def test_a_disconnect_wakes_the_waiter():
    hub = ClientHub()
    connection, channel = await _connected(hub)

    dispatch = asyncio.create_task(
        hub.dispatch(
            user="alice",
            dispatch_id="d12345678",
            tool="fs_read",
            args={},
            wait_ms=30000,
            surfaces={"read": [], "write": []},
        )
    )
    await _wait_for_sent(channel)
    connection.disconnect()

    with pytest.raises(ClientError, match="disconnected"):
        await dispatch
    assert hub.connected("alice") is False


async def test_a_second_connection_from_one_machine_replaces_the_first():
    """A reconnect on the same computer takes over its own stale socket.

    Two sockets for one machine is a reconnect the network has not noticed yet,
    and leaving dispatches to race between them is how a result arrives on the
    wrong one.
    """

    hub = ClientHub()
    first, first_channel = await _connected(hub)
    _second, _second_channel = await _connected(hub, tools=frozenset({"shell"}))

    assert first.closed
    assert first_channel.closed
    assert hub.available("alice", "ernest") == frozenset({"shell"})
    assert hub.connected("alice", "ernest")


async def test_a_second_machine_is_a_second_channel_and_kills_nothing():
    """The bug D112 exists to end (N185).

    Signing in from a laptop used to disconnect the desktop, fail its in-flight
    dispatches and kill its jobs — silently. Two computers are two places, not
    one fight over a slot.
    """

    hub = ClientHub()
    desktop, desktop_channel = await _connected(hub, tools=frozenset({"shell"}))
    laptop, _laptop_channel = await _connected(hub, machine="laptop")

    assert not desktop.closed
    assert not desktop_channel.closed
    assert not laptop.closed
    assert hub.connected("alice", "ernest")
    assert hub.connected("alice", "laptop")
    # Each machine advertises what it can run, and the advertisement does not
    # follow the newest connection.
    assert hub.available("alice", "ernest") == frozenset({"shell"})
    assert hub.available("alice", "laptop") == frozenset({"fs_read"})
    # A dispatch to a machine with no client is refused rather than landing on
    # whichever one is connected.
    assert hub.connected("alice", "dgx") is False


async def test_the_hub_reports_the_clients_home_and_nothing_without_one():
    """Where `~` means, per N145 — and None is a real answer.

    The home is the one thing only the client can tell the core: the directory it
    can look up belongs to its own host, and the two differ as soon as the client
    is somewhere else. A disconnected account has no such answer, which is why
    the caller has a fallback rather than this raising.
    """

    hub = ClientHub()
    assert hub.home("alice") is None

    await _connected(hub, home="/home/remote")
    assert hub.home("alice", "ernest") == "/home/remote"

    # A second computer's home is its own, and the first machine keeps its own
    # answer: this is the fact that used to flip under a live session (N186).
    await _connected(hub, machine="laptop", home="/home/elsewhere")
    assert hub.home("alice", "laptop") == "/home/elsewhere"
    assert hub.home("alice", "ernest") == "/home/remote"
    # A machine nobody has connected from has no home, which is the same answer
    # as a machine that is merely away and never the wrong directory.
    assert hub.home("alice", "dgx") is None


async def test_cancel_reaches_the_client():
    hub = ClientHub()
    connection, channel = await _connected(hub)

    await hub.cancel(user="alice", dispatch_id="d12345678")

    assert channel.sent[-1] == {
        "type": "cancel",
        "rpc_version": 2,
        "dispatch_id": "d12345678",
    }
    assert not connection.closed
