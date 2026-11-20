"""The client channel: the WebSocket a client daemon dispatches over.

A client is not a browser. It authenticates with the same session cookie the API
uses — obtained by logging in through the UI the client itself serves — and the
socket carries the dispatch RPC rather than session events. Identity is the
session's, checked here before the connection reaches the hub, so the hub never
sees an unauthenticated peer and a client can only act for an account it logged
in as. The hello carries facts about the machine that will run the dispatch —
its home and the tools it can run — not an identity claim: the local account
behind the client is the executor, and its name is not part of the protocol
(D67).

There is no Origin check. An Origin header belongs to a browser, and this
connection is a daemon's; absent is normal and present is ignored, because the
cookie is the credential either way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...adapters.clients import RPC_VERSION, ClientConnection
from ...adapters.persistence import AccountStore, SessionStore
from ...decision.identity import resolve_session
from .deps import COOKIE_NAME

if TYPE_CHECKING:  # pragma: no cover
    from starlette.websockets import WebSocket

__all__ = ["client_routes"]


def client_routes() -> list[Any]:
    from starlette.routing import WebSocketRoute

    return [WebSocketRoute("/client", _client_socket)]


class _Channel:
    """A `ClientConnection`'s send and close onto a Starlette WebSocket."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def send(self, message: dict[str, Any]) -> None:
        await self._websocket.send_json(message)

    async def close(self) -> None:
        try:
            await self._websocket.close()
        except Exception:  # noqa: BLE001 - already gone
            pass


async def _client_socket(websocket: WebSocket) -> None:
    services = websocket.app.state.services

    token = websocket.cookies.get(COOKIE_NAME)
    if not token:
        await websocket.close(code=4401)
        return

    async with services.database.connection() as conn:
        principal = await resolve_session(
            token,
            sessions=SessionStore(conn),
            accounts=AccountStore(conn),
        )
    if principal is None:
        await websocket.close(code=4401)
        return
    if principal.must_change_password:
        # The daemon has nothing to do until the person has replaced the
        # temporary password; refusing is clearer than accepting a channel that
        # can receive no dispatch.
        await websocket.close(code=4403)
        return

    await websocket.accept()

    try:
        hello = await websocket.receive_json()
    except Exception:  # noqa: BLE001 - a disconnect before the handshake
        return

    if hello.get("type") != "hello" or hello.get("rpc_version") != RPC_VERSION:
        await websocket.close(code=4400)
        return

    tools = frozenset(tool for tool in hello.get("tools", []) if isinstance(tool, str))
    home = hello.get("home")
    if not isinstance(home, str) or not home.startswith("/"):
        # Without it every `$HOME` grant expands against the wrong machine. A
        # client that cannot say is a client the core cannot check paths for.
        await websocket.send_json(
            {
                "type": "error",
                "rpc_version": RPC_VERSION,
                "dispatch_id": None,
                "code": "bad_request",
                "message": "hello must name the home directory on this machine",
            }
        )
        await websocket.close(code=4400)
        return

    drops = hello.get("drops")
    if not isinstance(drops, str) or not drops.startswith("/") or drops.rstrip("/") == "":
        # The directory a dropped file is attached from (D102). Refused rather
        # than defaulted: a guessed one would accept attachments the client
        # never wrote, and "/" would accept every path on the machine.
        await websocket.send_json(
            {
                "type": "error",
                "rpc_version": RPC_VERSION,
                "dispatch_id": None,
                "code": "bad_request",
                "message": "hello must name the drops directory on this machine",
            }
        )
        await websocket.close(code=4400)
        return

    connection = ClientConnection(
        _Channel(websocket), account=principal.name, tools=tools, home=home, drops=drops
    )
    await services.execution.register(connection)
    await connection.send(
        {"type": "welcome", "rpc_version": RPC_VERSION, "user": principal.name}
    )

    try:
        while True:
            connection.deliver(await websocket.receive_json())
    except Exception:  # noqa: BLE001 - a disconnect is the normal exit
        pass
    finally:
        connection.disconnect()
        services.execution.unregister(connection)
