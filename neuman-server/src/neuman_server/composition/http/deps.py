"""Shared request plumbing for the HTTP surface.

Turning a request into a principal is the one thing every authenticated route
does, so it happens in exactly one place. The CSRF check lives here too, beside
it, because "who is this?" and "did they mean to send this?" are answered
together or not at all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ...adapters.persistence import AccountStore, SessionStore
from ...decision.identity import Principal, resolve_session, tokens
from ...foundation import bind

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request

__all__ = [
    "COOKIE_NAME",
    "MachineFacts",
    "Unauthorized",
    "acting_home",
    "connection",
    "machine_facts",
    "require_csrf",
    "require_principal",
    "services_of",
]

COOKIE_NAME = "neuman_session"
CSRF_HEADER = "x-csrf-token"


@dataclass(frozen=True, slots=True)
class MachineFacts:
    """What one turn needs to know about the place it runs (D112).

    Assembled once per turn and per route rather than asked for field by field,
    because the pieces come from two places — the live connection and the last
    hello the database kept — and a caller that read them separately could
    describe a machine that is half online.

    Three fields, and every one of them is read: where `~` means, where a dropped
    file comes from, and what an operator says is installed there.
    """

    home: str | None
    drops: str | None
    # Told to the model, because the handshake advertises Neuman's own tools and
    # says nothing about `PATH`.
    notes: str = ""


def acting_home(services: Any, principal: Principal, machine: str | None = None) -> str | None:
    """Where `~` means for a machine, if its client is connected.

    Policy naming `$HOME` expands into directories under an account's home, and
    only the client knows which directory that is: it reports its home on
    `hello` (N145). With nothing connected there is no answer — an account is
    not a host account (D67), so this server has no home of its own to offer —
    and a `$HOME` grant reaches nothing until a client says where it is.

    `machine` absent asks the hub for the account's most recently connected
    machine, which is what every caller did before machines existed.
    """

    return services.execution.home(principal.name, machine)


async def machine_facts(
    conn: Any, services: Any, principal: Principal, machine: str
) -> MachineFacts:
    """Where `~` means on a machine, and what is installed there.

    Live first, then the machine's last hello. The fallback is the point: policy
    for a branch has to compose the same way whether the computer is awake or
    asleep, or a session's reach would change shape when a lid closed. A home
    directory does not move, so the last answer stays right until the machine
    says otherwise.

    A machine with no row has never connected on this account, so every field is
    empty: `$HOME` then names nothing, which is the same answer as a machine that
    is merely away and never a directory on the wrong computer.

    One read for three fields rather than three helpers, because they are three
    answers about one row and the routes that ask want more than one of them.
    """

    from ...adapters.persistence import MachineStore

    row = await MachineStore(conn).get(principal.account_id, machine)

    live_home = services.execution.home(principal.name, machine)
    live_drops = services.execution.drops(principal.name, machine)
    return MachineFacts(
        home=live_home if live_home is not None else (row.home if row is not None else None),
        drops=(
            live_drops if live_drops is not None else (row.drops if row is not None else None)
        ),
        notes=row.notes if row is not None else "",
    )


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
