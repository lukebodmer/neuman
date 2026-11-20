"""Starting a tool package's host on demand (D101), against a real process.

The host here is a few lines of Python that binds the socket it is told to, as
the Blender host's add-on does. A fake `Popen` would prove only that the
manager calls it.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path

from neuman_client.hosts import HostManager
from neuman_client.tools import HostSpec

HOST = """
import os, socket, sys, time
path = os.environ["NEUMAN_HOST_SOCKET"]
with open(os.environ["STARTS"], "a") as f:
    f.write("start\\n")
server = socket.socket(socket.AF_UNIX)
server.bind(path)
server.listen(4)
while True:
    connection, _ = server.accept()
    connection.close()
"""


def _spec(tmp_path: Path) -> HostSpec:
    script = tmp_path / "host"
    script.write_text(f"#!{sys.executable}\n{HOST}", encoding="utf-8")
    script.chmod(0o755)
    return HostSpec(
        package="studio",
        executable=script,
        socket=tmp_path / "studio.sock",
        environment={
            **os.environ,
            "NEUMAN_HOST_SOCKET": str(tmp_path / "studio.sock"),
            "STARTS": str(tmp_path / "starts"),
        },
    )


def _wait_for(path: Path) -> None:
    deadline = time.monotonic() + 10
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert path.exists(), "the host never opened its socket"


def test_a_host_is_started_once_while_it_comes_up_and_once_it_answers(tmp_path):
    """Two dispatches in a row start one host, not two.

    The second arrives before the socket exists — the first is still starting —
    and a second host would take the socket from the first.
    """

    manager = HostManager(tmp_path / "logs")
    spec = _spec(tmp_path)
    try:
        manager.ensure(spec)
        manager.ensure(spec)
        _wait_for(spec.socket)
        manager.ensure(spec)
        time.sleep(0.3)
        assert (tmp_path / "starts").read_text().count("start") == 1
        assert (tmp_path / "logs" / "studio.log").exists()
    finally:
        manager._running["studio"].kill()


def test_a_host_already_answering_is_used_rather_than_started(tmp_path):
    """A host left from a previous client — the person's open window — is kept."""

    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(tmp_path / "studio.sock"))
    listener.listen(1)
    try:
        HostManager(tmp_path / "logs").ensure(_spec(tmp_path))
        time.sleep(0.3)
        assert not (tmp_path / "starts").exists()
    finally:
        listener.close()
