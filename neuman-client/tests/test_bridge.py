"""The bridge against a real Unix socket and a real loopback port.

The bridge is the only place the client hands a path to the socket API, and the
host seam answers with a `Path`. `socket.bind` accepts bytes or str, so a `Path`
reached it as a `TypeError` inside the browser unit's `ExecStart` — a crash loop
a second after every start, with nothing in the suite binding a Unix socket at
all (N164). These tests bind one, so whichever type the seam returns is the
type exercised here.
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest

from neuman_client.browser.bridge import serve


def _echoing_server() -> tuple[socket.socket, int]:
    """A loopback TCP server that echoes what it receives, standing in for CDP."""

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def accept() -> None:
        connection, _ = listener.accept()
        with connection:
            while data := connection.recv(65536):
                connection.sendall(data)

    threading.Thread(target=accept, daemon=True).start()
    return listener, listener.getsockname()[1]


def _wait_for(path: Path, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)
    raise AssertionError(f"{path} was never created")


def _receive(client: socket.socket, expected: bytes) -> bytes:
    received = b""
    while len(received) < len(expected):
        chunk = client.recv(len(expected) - len(received))
        if not chunk:
            break
        received += chunk
    return received


@pytest.mark.parametrize("as_path", [True, False], ids=["Path", "str"])
def test_it_binds_the_path_the_seam_hands_it_and_forwards(tmp_path, as_path):
    listener, port = _echoing_server()
    socket_path = tmp_path / "runtime" / "neuman-browse.sock"
    stop = threading.Event()
    bridge = threading.Thread(
        target=serve,
        args=(socket_path if as_path else str(socket_path), port, stop),
        daemon=True,
    )

    try:
        bridge.start()
        # The directory does not exist yet: `serve` creates it, which is what
        # lets the unit bind a socket in a runtime directory it did not make.
        _wait_for(socket_path)

        assert socket_path.stat().st_mode & 0o777 == 0o600, "the socket is one user's"

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5.0)
            client.connect(str(socket_path))
            client.sendall(b"ping")
            assert _receive(client, b"ping") == b"ping"
    finally:
        stop.set()
        bridge.join(timeout=5)
        listener.close()

    assert not socket_path.exists(), "the socket is removed when the bridge stops"
