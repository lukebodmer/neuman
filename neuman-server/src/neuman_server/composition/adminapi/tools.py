"""The tool catalog, for authoring.

`/admin/models` answers what the appliance can reach; this answers what it can
*do*, which is the other half of an account's policy. The list is the registry's
own manifests — not what any account is granted — so the panel can draw a tool
matrix without inferring the catalog from one account's policy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .deps import services_of

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request
    from starlette.responses import Response

__all__ = ["admin_tool_routes"]


def admin_tool_routes() -> list[Any]:
    from starlette.routing import Route

    return [Route("/admin/tools", _list_tools, methods=["GET"])]


async def _list_tools(request: Request) -> Response:
    """Every manifest, reduced to what a chooser needs.

    `summary` is the line written for a person and the description is what the
    model is told; where a manifest has no summary, a truncated description is
    the honest fallback rather than a blank row. Parameters are omitted: they
    are the tool's contract with the model, not a decision an operator makes
    when granting it.
    """

    from starlette.responses import JSONResponse

    services = services_of(request)
    tools = []
    for name in services.registry.names():
        manifest = services.registry.get(name)
        if manifest is None:  # pragma: no cover - names come from the same map
            continue
        tools.append(
            {
                "name": manifest.name,
                "summary": manifest.summary or manifest.description[:160],
                "needs": sorted(manifest.needs),
                "mutating": bool(manifest.mutating),
                "critical": bool(manifest.critical),
                "diagnostic": bool(manifest.diagnostic),
            }
        )
    return JSONResponse({"tools": tools})
