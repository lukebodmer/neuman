"""Getting the peer's uid out of the kernel and into the ASGI scope.

`deps.peer_name` needs the uid on the other end of the connection, so it can
decide who is acting. ASGI has no place to put one: the scope carries `client`
and `server` as `(host, port)` pairs, and uvicorn derives both through
`get_remote_addr`, which returns `None` for a Unix socket because
`getpeername()` gives a path string rather than a tuple. So on the surface
where peer credentials are the *entire* authentication mechanism, the scope
arrives with `client: None` and nothing else.

That is why `peer_name` could never succeed. It looked for
`scope["transport"]`, which uvicorn does not set, and then for
`scope["_peer_socket"]`, which nothing set. Both branches were dead, so it
always raised — the surface failed closed for the whole of its existence, and
the reason nobody noticed is that failing closed is indistinguishable from
working until someone tries to use it (N26).

**Where the credential is captured.** The socket exists at the asyncio
transport layer and is gone by the time ASGI sees a request, so capture has to
happen in `connection_made`. The obvious next step — write it into
`self.scope` — does not work: uvicorn sets `self.scope = None` at connection
time and *rebuilds* it per request from its own attributes, so anything stored
there is discarded when the first request arrives. Verified by reading
`h11_impl`, after assuming otherwise first.

What is read per request is `self.app`. So the protocol subclass swaps in an
app wrapped around the uid it just read, and the wrapper injects that uid into
each scope on its way past. The wrapper depends on ASGI's contract rather than
on uvicorn's internals, which is the difference between a seam and a guess.

**Why the uid is resolved once, at connection.** A connection outlives a
request. Reading it per request would mean an account deleted mid-connection
still authorizes, and a uid reused by a new account silently becomes the
operator. Reading it once, when the kernel vouches for it, makes the
credential exactly as old as the connection.
"""

from __future__ import annotations

import socket
import struct
from typing import Any

__all__ = ["PEER_UID", "peer_credential_protocol", "read_peer_uid"]

# The scope key holding the peer's uid, absent when there is none. Named here
# so the protocol and `deps.py` cannot disagree about the spelling.
PEER_UID = "neuman_peer_uid"

_UCRED = "3i"  # struct ucred: pid, uid, gid


def read_peer_uid(sock: Any) -> int | None:
    """The uid on the other end, or None when this connection has no peer.

    Returns None rather than raising: "no peer credentials" is the ordinary
    answer on a TCP socket, and it is the caller's job to turn that into a
    refusal.
    """

    if sock is None:
        return None

    # Check the family rather than relying on the getsockopt failing. On Linux
    # SO_PEERCRED on a TCP socket does *not* raise: it succeeds and returns
    # pid 0, uid -1, gid -1. An earlier version of this function trusted the
    # exception and would have handed -1 to the caller, which only refused
    # because `pwd.getpwuid(-1)` happens to raise KeyError — an accident one
    # NSS configuration away from attributing every TCP connection to whatever
    # account -1 resolved to.
    if sock.family != socket.AF_UNIX:
        return None

    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize(_UCRED))
    except (OSError, AttributeError):
        return None

    _pid, uid, _gid = struct.unpack(_UCRED, raw)
    if uid < 0:
        # Belt and braces: the kernel reports an unidentified peer this way,
        # and a negative uid must never reach NSS.
        return None
    return uid


def _with_peer_uid(app: Any, uid: int) -> Any:
    """An ASGI app that stamps `uid` into every scope it passes on."""

    async def wrapped(scope: dict[str, Any], receive: Any, send: Any) -> None:
        # "lifespan" has no connection behind it and must not be touched; it
        # is also never routed through a per-connection app, but being
        # explicit costs one line and a wrong guess here breaks startup.
        if scope.get("type") in ("http", "websocket"):
            scope[PEER_UID] = uid
        await app(scope, receive, send)

    return wrapped


def peer_credential_protocol(base: Any) -> Any:
    """Wrap a uvicorn protocol class so each connection carries its peer's uid.

    `base` is whichever HTTP protocol uvicorn selected (h11 or httptools). The
    wrapper overrides `connection_made`, which is asyncio's interface rather
    than uvicorn's, so it does not care which.
    """

    class PeerCredentialProtocol(base):  # type: ignore[misc, valid-type]
        def connection_made(self, transport: Any) -> None:
            super().connection_made(transport)
            uid = read_peer_uid(transport.get_extra_info("socket"))
            if uid is not None:
                # `self.app` is read when a request arrives, so replacing it
                # here applies to every request on this connection and to no
                # other connection.
                self.app = _with_peer_uid(self.app, uid)

    return PeerCredentialProtocol
