"""Audit search, verification, and export for operators."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...adapters.persistence import AuditStore
from ...decision.audit import rebuild, search, verify
from ...decision.identity.authz import Denied, require_admin
from .deps import admin_actor, connection, services_of

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request
    from starlette.responses import Response

__all__ = ["admin_audit_routes"]


def admin_audit_routes() -> list[Any]:
    from starlette.routing import Route

    return [
        Route("/admin/audit", _search, methods=["GET"]),
        Route("/admin/audit/verify", _verify, methods=["POST"]),
        Route("/admin/audit/reindex", _reindex, methods=["POST"]),
    ]


async def _search(request: Request) -> Response:
    from starlette.responses import JSONResponse

    params = request.query_params
    try:
        limit = int(params.get("limit", "50"))
    except ValueError:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    async with connection(request) as conn:
        hits = await search(
            AuditStore(conn),
            params.get("q", ""),
            actor=params.get("actor"),
            action=params.get("action"),
            limit=limit,
        )

    return JSONResponse(
        {
            "hits": [
                {
                    "seq": hit.seq,
                    "ts": hit.ts.isoformat(),
                    "actor": hit.actor,
                    "action": hit.action,
                    "payload": hit.payload,
                }
                for hit in hits
            ]
        }
    )


async def _verify(request: Request) -> Response:
    """Walk the chain and confirm every link."""

    from starlette.responses import JSONResponse

    services = services_of(request)

    async with connection(request) as conn:
        try:
            await require_admin(
                admin_actor(request),
                directory=services.directory,
                policy=services.admin_policy,
            )
        except Denied as exc:
            return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)

        result = await verify(AuditStore(conn))

    return JSONResponse(
        {
            "ok": result.ok,
            "checked": result.checked,
            "first_bad_seq": result.first_bad_seq,
            "detail": result.detail,
        },
        # A broken chain is a real finding, not a server error, but it must not
        # look like success to a monitoring system.
        status_code=200 if result.ok else 409,
    )


async def _reindex(request: Request) -> Response:
    from starlette.responses import JSONResponse

    services = services_of(request)

    async with connection(request) as conn:
        try:
            await require_admin(
                admin_actor(request),
                directory=services.directory,
                policy=services.admin_policy,
            )
        except Denied as exc:
            return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)

        indexed = await rebuild(AuditStore(conn))

    return JSONResponse({"indexed": indexed})
