"""The client daemon against a fake connection: handshake, dispatch, cancel.

The daemon owns the message vocabulary and the executor wiring; the socket is
just two calls. That is what lets these tests drive it with a queue and assert on
what it sent, rather than standing up a WebSocket to test the daemon's logic.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest

from neuman_client import __version__
from neuman_client.daemon import ClientDaemon, Refused
from neuman_client.executor import Executor
from neuman_client.tools import ToolHost
from neuman_client.transport import LinkClosed


class FakeConnection:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[dict] = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False

    async def send(self, message: dict) -> None:
        self.sent.append(message)

    async def recv(self) -> dict:
        return await self.incoming.get()

    async def close(self) -> None:
        self.closed = True


class FakeTransport:
    def __init__(self) -> None:
        self.connection = FakeConnection()

    async def connect(self) -> FakeConnection:
        return self.connection


def _manifest(directory: Path, name: str) -> None:
    tool = directory / name
    tool.mkdir(parents=True, exist_ok=True)
    (tool / "manifest.toml").write_text(
        f'[tool]\nname = "{name}"\nexec = "{name}"\nwait_ms = 30000\n',
        encoding="utf-8",
    )


def _script(bin_dir: Path, name: str, body: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _host(tmp_path: Path) -> ToolHost:
    manifests = tmp_path / "manifests"
    bin_dir = tmp_path / "bin"
    _manifest(manifests, "echoer")
    _manifest(manifests, "sleeper")
    _script(bin_dir, "echoer", 'echo "args=$1"')
    _script(bin_dir, "sleeper", "sleep 30")
    return ToolHost(manifests_dir=manifests, bin_dir=bin_dir)


def _dispatch(dispatch_id: str, tool: str, args: dict, wait_ms: int = 30000) -> dict:
    return {
        "type": "dispatch",
        "rpc_version": 2,
        "dispatch_id": dispatch_id,
        "tool": tool,
        "args": args,
        "wait_ms": wait_ms,
        "lifetime_ms": wait_ms,
        "surfaces": {"read": [], "write": []},
    }


async def _until(predicate, *, timeout: float = 10.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


def _daemon(tmp_path: Path, host: ToolHost, transport: FakeTransport, **kwargs) -> ClientDaemon:
    return ClientDaemon(
        transport=transport,
        machine="ernest",
        home="/home/alice",
        drops="/home/alice/.local/state/neuman/drops",
        host=host,
        executor=Executor(spill_dir=tmp_path / "spill"),
        **kwargs,
    )


async def _collect_until_result(connection: FakeConnection) -> list[dict]:
    """Everything the daemon sent after the handshake, up to a terminal message."""

    messages: list[dict] = []
    while True:
        # Index 0 is the hello; the dispatch machinery starts after it.
        await _until(lambda: len(connection.sent) > 1 + len(messages))
        message = connection.sent[1 + len(messages)]
        messages.append(message)
        if message["type"] in ("result", "error"):
            return messages


async def test_the_handshake_carries_the_home_and_the_tool_names(tmp_path):
    transport = FakeTransport()
    daemon = _daemon(tmp_path, _host(tmp_path), transport)
    task = asyncio.create_task(daemon.run())
    try:
        await _until(lambda: transport.connection.sent)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    hello = transport.connection.sent[0]
    assert hello == {
        "type": "hello",
        "rpc_version": 2,
        # Which computer this is (D112). Required, not optional: a dispatch has
        # to land somewhere nameable, and a default would be a record that says
        # a command ran on a machine it did not.
        "machine": {"label": "ernest", "kind": "client"},
        "home": "/home/alice",
        "drops": "/home/alice/.local/state/neuman/drops",
        "tools": ["echoer", "sleeper"],
        # What an operator sees per machine, and what a refusal quotes back
        # (D111). Optional in the contract; a build that knows its release
        # always sends it.
        "release": __version__,
    }


async def test_a_dispatch_runs_and_reports_started_output_and_result(tmp_path):
    transport = FakeTransport()
    daemon = _daemon(tmp_path, _host(tmp_path), transport)
    task = asyncio.create_task(daemon.run())
    try:
        await _until(lambda: transport.connection.sent)
        transport.connection.incoming.put_nowait(
            {
                "type": "welcome",
                "rpc_version": 2,
                "user": "alice",
                "machine": {"label": "ernest", "kind": "client"},
            }
        )
        transport.connection.incoming.put_nowait(
            _dispatch("d12345678", "echoer", {"path": "/tmp/x"})
        )
        messages = await _collect_until_result(transport.connection)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert [message["type"] for message in messages] == ["started", "stdout", "result"]
    [started, stdout, result] = messages
    assert started["sequence"] == 0 and started["pid"] > 0
    assert stdout["sequence"] == 1
    assert base64.b64decode(stdout["chunk"]).startswith(b"args=")
    assert result["sequence"] == 2
    assert result["exit_code"] == 0


async def test_a_dispatch_addressed_to_another_machine_is_refused(tmp_path):
    """D112: a routing bug at the core stops here rather than running.

    An `rm` meant for `dgx` executed on a laptop is the failure the machine field
    exists to make impossible. The daemon is the last place it can be caught, and
    catching it is the difference between a refusal and an effect on the wrong
    computer.
    """

    transport = FakeTransport()
    daemon = _daemon(tmp_path, _host(tmp_path), transport)
    task = asyncio.create_task(daemon.run())
    try:
        await _until(lambda: transport.connection.sent)
        transport.connection.incoming.put_nowait(
            {
                "type": "welcome",
                "rpc_version": 2,
                "user": "alice",
                "machine": {"label": "ernest", "kind": "client"},
            }
        )
        misrouted = _dispatch("d12345678", "echoer", {"path": "/tmp/x"})
        misrouted["machine"] = "dgx"
        transport.connection.incoming.put_nowait(misrouted)
        [message] = await _collect_until_result(transport.connection)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert message["type"] == "error"
    assert message["code"] == "wrong_machine"
    # Nothing ran: no `started` frame, no pid, no exit status.
    assert message["dispatch_id"] == "d12345678"
    assert "dgx" in message["message"] and "ernest" in message["message"]


async def test_an_unknown_tool_is_an_error_not_a_spawn(tmp_path):
    transport = FakeTransport()
    daemon = _daemon(tmp_path, _host(tmp_path), transport)
    task = asyncio.create_task(daemon.run())
    try:
        await _until(lambda: transport.connection.sent)
        transport.connection.incoming.put_nowait(
            {
                "type": "welcome",
                "rpc_version": 2,
                "user": "alice",
                "machine": {"label": "ernest", "kind": "client"},
            }
        )
        transport.connection.incoming.put_nowait(_dispatch("d12345678", "not_a_tool", {}))
        [message] = await _collect_until_result(transport.connection)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert message["type"] == "error"
    assert message["code"] == "unknown_tool"
    assert message["dispatch_id"] == "d12345678"


async def test_a_cancel_stops_the_process_and_reports_it(tmp_path):
    transport = FakeTransport()
    daemon = _daemon(tmp_path, _host(tmp_path), transport)
    task = asyncio.create_task(daemon.run())
    try:
        await _until(lambda: transport.connection.sent)
        transport.connection.incoming.put_nowait(
            {
                "type": "welcome",
                "rpc_version": 2,
                "user": "alice",
                "machine": {"label": "ernest", "kind": "client"},
            }
        )
        transport.connection.incoming.put_nowait(
            _dispatch("d12345678", "sleeper", {}, wait_ms=60000)
        )
        await _until(
            lambda: any(message["type"] == "started" for message in transport.connection.sent)
        )
        transport.connection.incoming.put_nowait(
            {"type": "cancel", "rpc_version": 2, "dispatch_id": "d12345678"}
        )
        messages = await _collect_until_result(transport.connection)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    result = messages[-1]
    assert result["type"] == "result"
    assert result["cancelled"] is True
    assert result["exit_code"] == 128 + 9


async def test_a_package_tool_without_its_program_is_refused_by_name(tmp_path, monkeypatch):
    """`missing_program`, naming the program and where to get it (D101).

    Not spawned to fail: the tool never runs, and the refusal is the sentence
    the model passes on to the person.
    """

    _host(tmp_path)  # lays out `echoer` and `sleeper`
    manifests = tmp_path / "manifests"
    (manifests / "packages").mkdir()
    (manifests / "packages" / "studio.toml").write_text(
        '[package]\nid = "studio"\ntools = ["echoer"]\n'
        '[package.requires]\nprogram = "studio-not-installed"\nname = "Studio"\n'
        'url = "https://studio.example/download"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    host = ToolHost(manifests_dir=manifests, bin_dir=tmp_path / "bin")
    transport = FakeTransport()
    daemon = _daemon(tmp_path, host, transport)
    task = asyncio.create_task(daemon.run())
    try:
        await _until(lambda: transport.connection.sent)
        transport.connection.incoming.put_nowait(
            {
                "type": "welcome",
                "rpc_version": 2,
                "user": "alice",
                "machine": {"label": "ernest", "kind": "client"},
            }
        )
        transport.connection.incoming.put_nowait(_dispatch("d12345678", "echoer", {}))
        messages = await _collect_until_result(transport.connection)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    [error] = messages
    assert error["code"] == "missing_program"
    assert (
        "Studio" in error["message"] and "https://studio.example/download" in error["message"]
    )


# -- a refusal, which is not a dropped connection (D111) ---------------------


def _refusal(accepted: list[int], *, server: str = "0.2.0", client: str = "0.1.0") -> dict:
    return {
        "type": "error",
        "rpc_version": max(accepted),
        "dispatch_id": None,
        "code": "version_mismatch",
        "message": "this appliance speaks dispatch protocol 2; update the client.",
        "versions": {
            "server": {"release": server, "rpc": max(accepted)},
            "client": {"release": client, "rpc": 1},
            "accepted": accepted,
        },
    }


async def test_a_version_mismatch_is_recorded_as_refused_not_as_lost(tmp_path):
    """The state the page draws, and the reason it exists.

    A refused client that treated this like a dropped connection would reconnect
    forever, and the person would see a chat whose agent has quietly lost its
    tools — the confusing failure this whole path is for (D111).
    """

    transport = FakeTransport()
    daemon = _daemon(tmp_path, _host(tmp_path), transport)
    transport.connection.incoming.put_nowait(_refusal([2]))

    with pytest.raises(Refused):
        await daemon._serve()

    link = daemon.link
    assert link.state == "refused"
    assert link.direction() == "client"
    assert link.visible is True
    assert "0.2.0" in link.body()
    assert link.command() == "sudo apt update && sudo apt install --only-upgrade neuman-client"


async def test_a_close_before_the_welcome_is_a_refusal_too(tmp_path):
    """An appliance older than the payload says nothing, and must still not loop."""

    class _Closed(FakeConnection):
        async def recv(self) -> dict:
            raise LinkClosed(4400, "")

    class _ClosedTransport:
        def __init__(self) -> None:
            self.connection = _Closed()

        async def connect(self) -> _Closed:
            return self.connection

    daemon = _daemon(tmp_path, _host(tmp_path), _ClosedTransport())
    with pytest.raises(Refused):
        await daemon._serve()

    assert daemon.link.state == "refused"
    assert daemon.link.direction() is None, "a bare close cannot say which end is older"


async def test_a_refused_daemon_waits_for_a_person_rather_than_a_timer(tmp_path):
    """One hello, then nothing until something that could change the answer."""

    transport = FakeTransport()
    daemon = _daemon(tmp_path, _host(tmp_path), transport)
    task = asyncio.create_task(daemon.run())
    try:
        await _until(lambda: transport.connection.sent)
        transport.connection.incoming.put_nowait(_refusal([2]))
        await _until(lambda: daemon.link.state == "refused")

        # No backoff can produce a different answer, so there is no second
        # hello while it waits.
        await asyncio.sleep(0.2)
        assert len(transport.connection.sent) == 1

        # A person signing in, or pressing Try again, is the event.
        daemon.retry()
        await _until(lambda: len(transport.connection.sent) == 2)
        assert transport.connection.sent[1]["type"] == "hello"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_an_idle_daemon_restarts_into_a_new_package(tmp_path, monkeypatch):
    """`apt` replaces the files; this process is what has to notice (D111)."""

    from neuman_client import daemon as daemon_module

    transport = FakeTransport()
    daemon = _daemon(tmp_path, _host(tmp_path), transport, build_id="deb 0.1.0")
    monkeypatch.setattr(daemon_module, "installed_build_id", lambda: "deb 0.2.0")

    restart = asyncio.Event()
    task = asyncio.create_task(daemon.watch_build(restart, interval=0.01))
    try:
        await asyncio.wait_for(restart.wait(), timeout=5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert daemon.link.state == "updating"
    assert "0.2.0" in daemon.link.body()
