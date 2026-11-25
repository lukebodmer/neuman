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

**The handshake is where two versions meet.** The client says which dispatch
shape it speaks; the server answers with one it also speaks, out of
`SUPPORTED_RPC_VERSIONS`, and every frame on that socket uses it (D111). A
client that speaks nothing the server does is refused with an `error` frame
naming both releases and both shapes, and a close code that means "do not
retry" — because the alternative is a daemon reconnecting forever against a
socket that will never accept it, which is a support call with no sentence in
it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ... import __version__
from ...adapters.clients import (
    MIN_CLIENT_RELEASE,
    MIN_CLIENT_RPC_VERSION,
    SUPPORTED_RPC_VERSIONS,
    ClientConnection,
)
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

    if hello.get("type") != "hello":
        await websocket.close(code=4400)
        return

    release = hello.get("release")
    if not isinstance(release, str) or not release:
        # Optional: a client too old to name its release is still one the shape
        # gate can accept, and refusing it would turn a reporting gap into an
        # outage. Recorded as unknown.
        release = None

    speaks = hello.get("rpc_version")
    if not isinstance(speaks, int) or isinstance(speaks, bool):
        await _refuse_version(websocket, speaks=None, release=release)
        return
    if speaks not in SUPPORTED_RPC_VERSIONS:
        # The refusal is the point. A bare close code leaves a daemon retrying
        # forever and a person with no sentence to act on, so both ends are
        # named and the close code says "terminal" (D111).
        await _refuse_version(websocket, speaks=speaks, release=release)
        return

    tools = frozenset(tool for tool in hello.get("tools", []) if isinstance(tool, str))
    home = hello.get("home")
    if not isinstance(home, str) or not home.startswith("/"):
        # Without it every `$HOME` grant expands against the wrong machine. A
        # client that cannot say is a client the core cannot check paths for.
        await websocket.send_json(
            {
                "type": "error",
                "rpc_version": speaks,
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
                "rpc_version": speaks,
                "dispatch_id": None,
                "code": "bad_request",
                "message": "hello must name the drops directory on this machine",
            }
        )
        await websocket.close(code=4400)
        return

    connection = ClientConnection(
        _Channel(websocket),
        account=principal.name,
        tools=tools,
        home=home,
        drops=drops,
        rpc_version=speaks,
        release=release,
    )
    await services.execution.register(connection)
    await connection.send(
        {
            "type": "welcome",
            "rpc_version": connection.rpc_version,
            "user": principal.name,
            "release": __version__,
            "rpc_versions": sorted(SUPPORTED_RPC_VERSIONS),
            "minimum_client": {
                "release": MIN_CLIENT_RELEASE,
                "rpc": MIN_CLIENT_RPC_VERSION,
            },
        }
    )

    try:
        while True:
            connection.deliver(await websocket.receive_json())
    except Exception:  # noqa: BLE001 - a disconnect is the normal exit
        pass
    finally:
        connection.disconnect()
        services.execution.unregister(connection)


async def _refuse_version(
    websocket: WebSocket, *, speaks: int | None, release: str | None
) -> None:
    """Refuse a handshake neither end can serve, in words the other end can use.

    Three facts travel, because the client cannot derive any of them: what this
    appliance is, what the client said it is, and the shapes that would have
    been accepted. Without the last one a daemon knows only that it was turned
    away, which is the reconnect loop this replaces (D111).

    `4406` rather than `4400`: the daemon treats it as terminal, so a refused
    client stops retrying and shows the sentence, where `4400` means a malformed
    hello that a restart might fix.
    """

    accepted = sorted(SUPPORTED_RPC_VERSIONS)
    await websocket.send_json(
        {
            "type": "error",
            "rpc_version": accepted[-1],
            "dispatch_id": None,
            "code": "version_mismatch",
            "message": _version_message(speaks=speaks, release=release),
            "versions": {
                "server": {"release": __version__, "rpc": accepted[-1]},
                "client": {"release": release, "rpc": speaks},
                "accepted": accepted,
            },
        }
    )
    await websocket.close(code=4406, reason="version_mismatch")


def _version_message(*, speaks: int | None, release: str | None) -> str:
    """The refusal as one sentence, naming whichever half has to move.

    The direction is knowable: a client below the smallest accepted shape is the
    one to update, and a client above the largest is talking to an appliance
    that is behind it. Only "no idea what you speak" is ambiguous, and that one
    says so rather than guessing.
    """

    accepted = sorted(SUPPORTED_RPC_VERSIONS)
    theirs = f"Neuman {release}" if release else "this client"
    if speaks is None:
        return (
            f"{theirs} did not say which dispatch protocol it speaks; this appliance "
            f"speaks {accepted}. Update whichever end is older."
        )
    if speaks < accepted[0]:
        return (
            f"{theirs} speaks dispatch protocol {speaks} and this appliance speaks "
            f"{accepted}. Update the client."
        )
    return (
        f"{theirs} speaks dispatch protocol {speaks}, which is newer than this "
        f"appliance's {accepted}. The appliance needs updating; nothing on this "
        "computer does."
    )
