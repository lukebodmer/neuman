"""Shared request plumbing for the HTTP surface.

Turning a request into a principal is the one thing every authenticated route
does, so it happens in exactly one place. The CSRF check lives here too, beside
it, because "who is this?" and "did they mean to send this?" are answered
together or not at all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from ...adapters.persistence import AccountStore, SessionStore
from ...decision.identity import Principal, resolve_session, tokens
from ...foundation import bind

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request

__all__ = [
    "COOKIE_NAME",
    "Unauthorized",
    "acting_drops",
    "acting_home",
    "connection",
    "require_csrf",
    "require_principal",
    "services_of",
]

COOKIE_NAME = "neuman_session"
CSRF_HEADER = "x-csrf-token"


def acting_home(services: Any, principal: Principal) -> str | None:
    """Where `~` means for the machine that will run the dispatch, if one is connected.

    Policy naming `$HOME` expands into directories under an account's home, and
    only the client knows which directory that is: it reports its home on
    `hello` (N145). With nothing connected there is no answer — an account is
    not a host account (D67), so this server has no home of its own to offer —
    and a `$HOME` grant reaches nothing until a client says where it is. That is
    also the only time anything could run.
    """

    return services.execution.home(principal.name)


def acting_drops(services: Any, principal: Principal) -> str | None:
    """The connected client's drops directory, if one is connected (D102).

    Reported on `hello` like the home, and for the same reason: it is a path on
    the client's machine. With nothing connected a dropped file cannot be
    attached, which is also when nothing could read it.
    """

    return services.execution.drops(principal.name)


class Unauthorized(Exception):
    """No valid principal for this request."""

    def __init__(self, reason: str = "unauthenticated", status: int = 401) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


def services_of(request: Request) -> Any:
    return request.app.state.services


@asynccontextmanager
async def connection(request: Request) -> AsyncIterator[Any]:
    """Borrow a database connection for this request."""

    services = services_of(request)
    async with services.database.connection() as conn:
        yield conn


async def require_principal(request: Request, conn: Any) -> Principal:
    """Resolve the session cookie into a principal, or raise.

    Re-reads the account on every request, so a disabled account stops working
    immediately (D68).
    """

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise Unauthorized()

    principal = await resolve_session(
        token,
        sessions=SessionStore(conn),
        accounts=AccountStore(conn),
    )
    if principal is None:
        raise Unauthorized()

    # A temporary password buys exactly the routes needed to replace it. Every
    # other read and write waits: the alternative is an account whose first act
    # can be a tool call on someone else's machine, with a credential the admin
    # chose and read out loud.
    if principal.must_change_password and request.url.path not in (
        "/api/me",
        "/api/me/password",
        "/api/logout",
    ):
        raise Unauthorized("password_change_required", status=403)

    bind(account=principal.name)
    return principal


def require_csrf(request: Request, principal: Principal) -> None:
    """Check the CSRF token on an unsafe request.

    Constant-time comparison: the token is a secret, and a timing oracle on it
    would undo the point of having a second one.
    """

    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    supplied = request.headers.get(CSRF_HEADER, "")
    if not supplied or not tokens.tokens_equal(supplied, principal.csrf_token):
        raise Unauthorized("csrf_failed", status=403)
