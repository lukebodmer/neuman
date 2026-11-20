"""The WebSocket transport against a real `websockets` server.

What this module owns is the cookie header and the two calls. The server here is
a real endpoint, so the upgrade and the framing are real and only the daemon
above is left out.
"""

from __future__ import annotations

import json

import pytest
import websockets

from neuman_client.transport import WebSocketTransport


async def test_the_cookie_is_sent_and_a_message_round_trips():
    seen: dict[str, str | None] = {}

    async def handler(websocket) -> None:
        seen["cookie"] = websocket.request.headers.get("Cookie")
        message = json.loads(await websocket.recv())
        await websocket.send(json.dumps({"echo": message}))

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        connection = await WebSocketTransport(
            url=f"ws://127.0.0.1:{port}/client", cookie=lambda: "abc123"
        ).connect()
        try:
            await connection.send({"type": "hello"})
            reply = await connection.recv()
        finally:
            await connection.close()

    assert seen["cookie"] == "neuman_session=abc123"
    assert reply == {"echo": {"type": "hello"}}


async def test_no_session_is_an_error_not_a_connection():
    transport = WebSocketTransport(url="ws://127.0.0.1:1/client", cookie=lambda: None)
    with pytest.raises(OSError):
        await transport.connect()
