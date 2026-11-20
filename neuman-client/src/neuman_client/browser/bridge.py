"""A Unix-socket-to-loopback bridge in front of Chromium's DevTools port.

Chromium only exposes the Chrome DevTools Protocol on a TCP port. The server
dials a Unix socket instead, because a socket's mode is an access control that a
loopback port is not: a `0600` socket is reachable by one user, a `127.0.0.1`
port by every process on the machine. This accepts connections on the socket and
copies bytes to the port in both directions.

It is a byte pipe on purpose. Neither the HTTP requests nor the WebSocket
upgrade need to be understood to be forwarded, and a proxy that parses what it
carries is a proxy that can disagree with it.
"""

from __future__ import annotations

import os
import socket
import threading
import time

__all__ = ["serve"]

_CHUNK = 65536
_BACKLOG = 16


def _pump(source: socket.socket, sink: socket.socket, stop: threading.Event) -> None:
    try:
        while not stop.is_set():
            data = source.recv(_CHUNK)
            if not data:
                break
            sink.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (source, sink):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _handle(connection: socket.socket, port: int, stop: threading.Event) -> None:
    # Chromium may still be starting when the first dispatch arrives, so retry
    # the upstream connection rather than dropping it.
    upstream: socket.socket | None = None
    deadline = time.monotonic() + 20.0
    while upstream is None and time.monotonic() < deadline and not stop.is_set():
        try:
            upstream = socket.create_connection(("127.0.0.1", port), timeout=2.0)
        except OSError:
            time.sleep(0.2)
    if upstream is None:
        connection.close()
        return
    upstream.settimeout(None)
    connection.settimeout(None)
    for source, sink in ((connection, upstream), (upstream, connection)):
        threading.Thread(target=_pump, args=(source, sink, stop), daemon=True).start()


def serve(socket_path: str | os.PathLike[str], port: int, stop: threading.Event) -> None:
    """Bind `socket_path` and forward every connection to `127.0.0.1:port`."""

    # `socket.bind` takes bytes or str only, and the host seam hands every
    # caller a `Path`. Converting here rather than at each caller is what keeps
    # the seam free to answer with the type it actually means — the first
    # version took the `Path` straight through and the browser unit died inside
    # `bind` a second after every start (N164).
    path = os.fspath(socket_path)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass

    # The umask is what gives the socket its mode at creation, so there is no
    # window where it exists world-readable — a `chmod` after `bind` leaves one.
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    previous_umask = os.umask(0o177)
    try:
        server.bind(path)
    finally:
        os.umask(previous_umask)
    os.chmod(path, 0o600)
    server.listen(_BACKLOG)
    server.settimeout(1.0)

    try:
        while not stop.is_set():
            try:
                connection, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=_handle, args=(connection, port, stop), daemon=True).start()
    finally:
        server.close()
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
