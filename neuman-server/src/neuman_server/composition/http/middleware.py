"""Request correlation, CSRF/Origin checks, security headers.

The session surface is the untrusted network surface (docs/decisions.md D1), so every unsafe
request re-checks its origin and its CSRF token. "Re-checks" is the operative
word: these are checked per request, not established once at login, because a
session that is valid is not the same thing as a request that is intended.

Origin and CSRF are two independent defences:

* **Origin/Referer** stops a cross-site request that carries the cookie
  automatically.
* **CSRF token** stops anything that can forge an Origin header but cannot read
  the second secret (which lives outside the HttpOnly cookie).

Either alone has known gaps; together they are the standard pairing.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from ...foundation import Settings, bind, get_logger

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request
    from starlette.responses import Response

__all__ = ["middleware_stack"]

_log = get_logger("neuman.server.http")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "x-csrf-token"

_SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    # The client is served from our own origin and inlines nothing it did not
    # build; no external fetches are expected from the appliance's UI.
    "content-security-policy": (
        "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
        "style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
}


def middleware_stack(settings: Settings) -> list[Any]:
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware

    from ..adminapi.guard import admin_guard

    return [
        Middleware(BaseHTTPMiddleware, dispatch=_correlate),
        # Before the origin guard: an admin request without peer credentials
        # must be refused whatever its Origin says, and an unauthenticated
        # read of the audit chain is not a CSRF problem (N27).
        Middleware(BaseHTTPMiddleware, dispatch=admin_guard()),
        Middleware(BaseHTTPMiddleware, dispatch=_make_origin_guard(settings)),
        Middleware(BaseHTTPMiddleware, dispatch=_security_headers),
    ]


async def _correlate(request: Request, call_next: Any) -> Response:
    """Attach a request id to the request and to every log record it emits."""

    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    request.state.request_id = request_id
    bind(request_id=request_id)

    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response


def _make_origin_guard(settings: Settings) -> Any:
    allowed = frozenset(settings.allowed_origins)

    async def guard(request: Request, call_next: Any) -> Response:
        from starlette.responses import JSONResponse

        if request.method in SAFE_METHODS:
            return await call_next(request)

        origin = request.headers.get("origin")
        if origin is not None and not _origin_ok(origin, request, allowed):
            _log.warning(
                "rejected cross-origin request",
                fields={"origin": origin, "path": request.url.path},
            )
            return JSONResponse({"error": "origin_not_allowed"}, status_code=403)

        return await call_next(request)

    return guard


def _origin_ok(origin: str, request: Request, allowed: frozenset[str]) -> bool:
    if origin in allowed:
        return True
    # Same-origin by construction: compare against what the client asked for.
    host = request.headers.get("host")
    if host and origin.endswith(f"//{host}"):
        return True
    return False


async def _security_headers(request: Request, call_next: Any) -> Response:
    response = await call_next(request)
    for header, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response
