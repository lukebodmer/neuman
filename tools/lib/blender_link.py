"""Ask the Blender the package's host started for something, and print the answer.

Shared by `blender`, `blender_scene` and `blender_view`, which differ only in
what they may ask — that split is the gate's, not this module's (D59, D100).

One request per connection, newline-terminated JSON each way, over the Unix
socket the client names in `NEUMAN_HOST_SOCKET`. Standard library only, for
the reason `browse_cdp` gives (N47).

The client starts the host when it dispatches one of these tools and nothing is
answering, and does not wait for it (D15: a dispatch must report `started`
within seconds, and Blender takes longer than that to open). So the wait is
here: a socket that is not there yet is retried until Blender has had time to
come up.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time

SOCKET_ENV = "NEUMAN_HOST_SOCKET"

# How long a host may take to start answering. Blender opened in about three
# seconds here; the rest is margin for a slower machine and a first start.
START_S = 90.0


class BlenderError(RuntimeError):
    pass


def _connect(path: str) -> socket.socket:
    deadline = time.monotonic() + START_S
    while True:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.connect(path)
            return connection
        except (FileNotFoundError, ConnectionRefusedError) as error:
            connection.close()
            if time.monotonic() >= deadline:
                raise BlenderError(
                    f"Blender did not start within {START_S:.0f} seconds; if its window "
                    "opened, it may have stopped on an error"
                ) from error
            time.sleep(0.25)
        except OSError as error:
            # Not "still starting": a path too long for a socket, a permission.
            connection.close()
            raise BlenderError(f"cannot reach Blender at {path}: {error}") from error


def ask(request: dict) -> dict:
    path = os.environ.get(SOCKET_ENV)
    if not path:
        raise BlenderError(
            f"{SOCKET_ENV} is not set: this tool runs from the Neuman client, which "
            "starts Blender for it"
        )
    with _connect(path) as connection:
        connection.sendall(json.dumps(request).encode() + b"\n")
        data = b""
        while True:
            chunk = connection.recv(1 << 20)
            if not chunk:
                break
            data += chunk
    try:
        return json.loads(data)
    except json.JSONDecodeError as error:
        raise BlenderError(
            "Blender closed the connection without answering; it may have been closed"
        ) from error


def arguments(tool: str, argv: list[str]) -> dict | None:
    if len(argv) != 2:
        print(f"{tool}: expected one JSON argument", file=sys.stderr)
        return None
    try:
        args = json.loads(argv[1])
    except json.JSONDecodeError as error:
        print(f"{tool}: invalid arguments: {error}", file=sys.stderr)
        return None
    if not isinstance(args, dict):
        print(f"{tool}: arguments must be an object", file=sys.stderr)
        return None
    return args


def run(tool: str, request: dict) -> int:
    """Send `request`, print what came back, and return the exit code."""

    try:
        reply = ask(request)
    except BlenderError as error:
        print(f"{tool}: {error}", file=sys.stderr)
        return 1
    image = reply.get("image")
    if image:
        # The `returns_image` shape the harness lifts: one line,
        # `media_type base64data` (D28).
        sys.stdout.write(f"{image['media_type']} {image['data']}\n")
        return 0
    text = reply.get("text") or ""
    if text:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    if not reply.get("ok"):
        print(f"{tool}: {reply.get('error') or 'Blender refused the request'}", file=sys.stderr)
        if reply.get("trace"):
            print(reply["trace"], file=sys.stderr)
        return 1
    return 0
