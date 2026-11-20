"""One guard over the whole admin surface: a peer, and an admin.

Two checks, because they are two claims:

* **There is an identified peer.** The kernel reports a uid on the connection
  and it resolves to an account. A TCP connection has none, so the admin
  routes refuse there whatever else is true.
* **That peer holds admin authority.** Group membership, read through the
  directory by `decision.identity.authz`.

Both were meant to be enforced inside the operations, and both held for every
route that *mutates* and for none of the routes that only *read*.

N27 was the first half: `GET /admin/audit` returned the entire governance
record to an unauthenticated caller. The fix put a peer check here. N32 was
the second half, found the same way — `vm-appliance` gave an account
reachability without authority and asked what happened, and
`neuman-admin status` printed the appliance's schema version, live session
count and conflicted accounts. `_get_policy`, `_list_grants`, `_search`,
`_status`, `_list_models` and `_usage` all called operations that take no
actor and check nothing.

**Why the check lives in the guard.** Adding `require_admin` to six
handlers fixes six handlers. The seventh is one forgotten line away from the
same hole, and the failure is silent because an unauthorized read looks
exactly like an authorized one — which is precisely how this survived the
first fix. A guard over the prefix makes protection the consequence of
existing, and an exemption something that has to be written down where a
reviewer sees it.

**Why this does not violate D9.** The guard does not decide anything. It calls
`require_admin`, the same function the mutating operations call, so there
is still one implementation of "who is an operator" and the socket and HTTP
surfaces cannot drift. What the guard adds is that the call happens whether or
not a handler remembered to make it. The mutating operations keep their own
call: they need the `Authorization` for the audit record, and a check inside
the operation is what protects a *future* caller that is not an HTTP request
at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...decision.identity.authz import Denied, require_admin
from ...foundation import get_logger
from .deps import AdminUnauthorized, peer_name, services_of

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request
    from starlette.responses import Response

__all__ = ["ADMIN_PREFIX", "admin_guard"]

_log = get_logger("neuman.server.adminapi")

ADMIN_PREFIX = "/admin"


def admin_guard() -> Any:
    """Middleware refusing any `/admin` request without an authorized peer."""

    async def guard(request: Request, call_next: Any) -> Response:
        from starlette.responses import JSONResponse

        if not request.url.path.startswith(ADMIN_PREFIX):
            return await call_next(request)

        try:
            actor = peer_name(request)
        except AdminUnauthorized as exc:
            # Warning, not info: on a correctly deployed appliance these routes
            # are only reachable over the Unix socket, so a refusal here means
            # something is talking to them that should not be.
            _log.warning(
                "admin request without peer credentials",
                fields={"path": request.url.path, "reason": str(exc)},
            )
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        # The first administrator has to be creatable before one exists. The
        # kernel-identified peer is the authority for that one route; the
        # handler still refuses when an admin already exists unless it is the
        # explicit recovery path.
        if request.url.path == "/admin/bootstrap":
            request.scope["neuman_admin_actor"] = actor
            return await call_next(request)

        services = services_of(request)
        try:
            authorization = await require_admin(
                actor,
                directory=services.directory,
                policy=services.admin_policy,
            )
        except Denied as exc:
            # Expected in ordinary operation: an account may reach the socket
            # (it is in the `neuman` group) without holding operator authority.
            # Logged so the attempt is visible, at info because being refused
            # is the system working.
            _log.info(
                "admin request from a non-operator",
                fields={"path": request.url.path, "actor": actor},
            )
            return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)

        # Stashed so a handler need not re-derive either, and so the guard and
        # the handler can never disagree about who is acting.
        request.scope["neuman_admin_actor"] = actor
        request.scope["neuman_admin_authorization"] = authorization
        return await call_next(request)

    return guard
