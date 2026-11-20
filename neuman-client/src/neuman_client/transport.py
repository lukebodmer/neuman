"""How the client reaches the core: one authenticated WebSocket.

The client logs in — through the UI it serves — and holds the session cookie
that login returned. That cookie authenticates a single WebSocket to `/client`,
and everything above this module reads like a conversation: send a message,
receive a message. The handshake, dispatches, results and cancellation all ride
the same socket.

The transport is deliberately just two calls. There is no URL building and no
protocol knowledge here; the daemon above owns the message vocabulary.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

__all__ = ["Connection", "Transport", "WebSocketTransport"]

# One message. Generous, because a dispatch's stdout rides base64-encoded and is
# therefore ~4/3 its size.
MAX_MESSAGE_BYTES = 8 * 1024 * 1024

COOKIE = "neuman_session"


class Connection(Protocol):
    async def send(self, message: dict[str, Any]) -> None: ...

    async def recv(self) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class Transport(Protocol):
    async def connect(self) -> Connection: ...


class _WebSocketConnection:
    def __init__(self, websocket: Any) -> None:
        self._websocket = websocket

    async def send(self, message: dict[str, Any]) -> None:
        await self._websocket.send(json.dumps(message, separators=(",", ":")))

    async def recv(self) -> dict[str, Any]:
        return json.loads(await self._websocket.recv())

    async def close(self) -> None:
        await self._websocket.close()


class WebSocketTransport:
    """A connection to the core's `/client` socket, as the logged-in account.

    The cookie is the whole credential. An expired one is refused by the core at
    the handshake (`4401`), which surfaces as a `websockets` handshake error and
    the daemon backs off — so a user who has logged out is a client that
    reconnects when they log in again, rather than one that hammers.
    """

    def __init__(self, *, url: str, cookie: Callable[[], str | None]) -> None:
        self._url = url
        self._cookie = cookie

    async def connect(self) -> Connection:
        import websockets

        cookie = self._cookie()
        if not cookie:
            # No stored session yet. The daemon backs off and retries, so a user
            # who has not logged in is a client that waits rather than one that
            # fails outright.
            raise OSError("no session for the core: log in through the UI")

        websocket = await websockets.connect(
            self._url,
            additional_headers={"Cookie": f"{COOKIE}={cookie}"},
            max_size=MAX_MESSAGE_BYTES,
        )
        return _WebSocketConnection(websocket)
