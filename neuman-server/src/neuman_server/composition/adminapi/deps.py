"""Admin surface plumbing: who is the admin on this connection?

The admin API is served over a Unix socket and authenticated by **peer
credentials** — the kernel tells us the uid on the other end of the socket, and
it cannot be forged by anything that can reach the socket. That uid is resolved
to a name through NSS, and the name is what `require_admin` then checks
inside each operation.

Peer credentials are a second factor, not a replacement for authorization:
"being on the machine as this uid" and "holding admin authority" are
different claims, and the appliance checks both.
"""

from __future__ import annotations

import pwd
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from .peercred import PEER_UID

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request

__all__ = ["AdminUnauthorized", "admin_actor", "connection", "peer_name", "services_of"]


class AdminUnauthorized(Exception):
    """The peer on this connection cannot be identified."""


def services_of(request: Request) -> Any:
    return request.app.state.services


@asynccontextmanager
async def connection(request: Request) -> AsyncIterator[Any]:
    services = services_of(request)
    async with services.database.connection() as conn:
        yield conn


def peer_name(request: Request) -> str:
    """The account name of the peer, from SO_PEERCRED.

    Raises :class:`AdminUnauthorized` when the peer cannot be identified, which
    includes every TCP connection: peer credentials are a property of a Unix
    socket, so reaching the admin routes over the network cannot produce one.

    The uid is put in the scope by `peercred.py` at connection time. This used
    to look for `scope["transport"]` and `scope["_peer_socket"]`, neither of
    which uvicorn or anything else ever set, so it raised unconditionally and
    the admin surface had no way to succeed (N26).
    """

    uid = request.scope.get(PEER_UID)
    if uid is None:
        raise AdminUnauthorized("no peer credentials on this connection")

    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError as exc:
        # A uid with no account is not an admin. This happens when an
        # account is deleted while a connection is open, and it must refuse
        # rather than fall back to the number.
        raise AdminUnauthorized(f"peer uid {uid} does not resolve to an account") from exc


def admin_actor(request: Request) -> str:
    """The name the operation will authorize.

    The local socket's guard stashes the peer name; the HTTP admin surface
    stashes the session account. Reading the scope first keeps one source of
    truth for "who is acting" across both transports, and falls back to the
    peer for a route that reached the socket before the guard did.
    """

    return request.scope.get("neuman_admin_actor") or peer_name(request)
