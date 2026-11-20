"""The admin panel's HTTP transport.

The same decision operations the local socket exposes, with a session and an
application administrator instead of a peer uid. Two transports, one set of
operations (D9): the local socket remains the bootstrap/recovery path, and this
is the one a person uses from the client UI.

Every route is wrapped rather than trusting the operation to check: a read that
forgot `require_admin` would otherwise be an unauthenticated read of the audit
chain, which is the N27 failure in a new place. The wrapper also sets
`neuman_admin_actor` so the operation audits the session account, not a peer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..adminapi import admin_routes
from .deps import connection, require_csrf, require_principal, services_of

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request

__all__ = ["admin_http_routes"]


def admin_http_routes() -> list[Any]:
    from starlette.routing import Route

    routes: list[Any] = []
    for route in admin_routes():
        # Bootstrap is local-only: it exists to create the first administrator
        # from a kernel-identified peer, and must not be reachable over the
        # network before one exists.
        if route.path == "/admin/bootstrap":
            continue
        routes.append(
            Route(
                f"/api{route.path}",
                _authorized(route.endpoint),
                methods=route.methods,
                name=route.name,
                include_in_schema=False,
            )
        )
    return routes


def _authorized(endpoint: Any) -> Any:
    async def wrapped(request: Request) -> Any:
        from starlette.responses import JSONResponse

        from ...decision.identity.authz import authorize

        services = services_of(request)
        async with connection(request) as conn:
            try:
                principal = await require_principal(request, conn)
                require_csrf(request, principal)
            except Exception as exc:  # noqa: BLE001 - Unauthorized is the ordinary path
                reason = getattr(exc, "reason", "unauthenticated")
                status = getattr(exc, "status", 401)
                return JSONResponse({"error": reason}, status_code=status)

            authorization = await authorize(
                principal.name,
                directory=services.directory,
                policy=services.admin_policy,
            )
            if not authorization.is_admin:
                return JSONResponse({"error": "forbidden"}, status_code=403)

            request.scope["neuman_admin_actor"] = principal.name

        return await endpoint(request)

    return wrapped
