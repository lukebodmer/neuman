"""Policy authoring for operators.

Every handler here delegates to `decision.policy.ops`, which guards, applies,
and audits. This file validates request shape and serializes a response; it
holds no policy logic and performs no authorization check of its own — doing
that here would let the HTTP admin surface and the socket surface drift apart,
and a drift in an authorization check is a privilege escalation (docs/decisions.md D9).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...adapters.persistence import AccountStore, AuditStore, GenerationStore, GrantStore
from ...decision.identity.authz import Denied
from ...decision.policy.fragments import FragmentError
from ...decision.policy.ops import (
    clear_overlay,
    grant_role,
    preview_draft,
    preview_policy_detail,
    revoke_role,
    set_overlay,
)
from ...decision.policy.source import account_role_conflicts, conflict_detail
from ...foundation.state import read_overlay
from .deps import admin_actor, connection, services_of

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request
    from starlette.responses import Response

__all__ = ["admin_policy_routes"]


def admin_policy_routes() -> list[Any]:
    from starlette.routing import Route

    return [
        Route("/admin/policy/preview", _preview, methods=["POST"]),
        Route("/admin/policy/{name}", _get_policy, methods=["GET"]),
        Route("/admin/policy/{name}/overlay", _put_overlay, methods=["PUT"]),
        Route("/admin/policy/{name}/overlay", _delete_overlay, methods=["DELETE"]),
        Route("/admin/grants", _list_grants, methods=["GET"]),
        Route("/admin/grants", _add_grant, methods=["POST"]),
        Route("/admin/grants", _remove_grant, methods=["DELETE"]),
    ]


async def _get_policy(request: Request) -> Response:
    from starlette.responses import JSONResponse

    services = services_of(request)
    name = request.path_params["name"]

    async with connection(request) as conn:
        preview = await preview_policy_detail(
            name,
            source=services.policy_source(),
            directory=services.directory,
            grants=GrantStore(conn),
        )

    return JSONResponse(
        {
            "account": name,
            "policy": preview.policy.to_json(),
            # The value the roles already give, before the account's own
            # exceptions. The editor writes only the difference from this, so
            # it has to be handed the baseline rather than deriving one by
            # subtracting from the effective policy in the browser.
            "inherited": preview.inherited.to_json(),
            "roles": [
                {"name": role.name, "source": role.source, "known": role.known}
                for role in preview.roles
            ],
            "reach": sorted(preview.policy.reach),
            "provenance": preview.policy.provenance,
            "inherited_provenance": preview.inherited.provenance,
            "conflicts": _conflicts_json(preview.conflicts),
            # The overlay as written, not the composed result: the editor has
            # to send back the fields it did not touch, and a client that
            # reconstructed them from provenance would silently drop any field
            # whose layer happened to agree with the effective value.
            "overlay": read_overlay(services.settings.overlay_dir, name) or {},
        }
    )


async def _preview(request: Request) -> Response:
    """Compose a proposed account policy for the onboarding dialog.

    The client may not merge roles itself; that would be a second policy engine
    and would preview a different result from the one the server stores.
    """

    from starlette.responses import JSONResponse

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    roles = body.get("roles") or []
    overlay = body.get("overlay") or None
    if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
        return JSONResponse({"error": "invalid_roles"}, status_code=400)
    if overlay is not None and not isinstance(overlay, dict):
        return JSONResponse({"error": "invalid_overlay"}, status_code=400)

    services = services_of(request)
    try:
        preview = preview_draft(
            source=services.policy_source(),
            roles=roles,
            overlay=overlay,
        )
    except FragmentError as exc:
        return JSONResponse({"error": "invalid_policy", "detail": str(exc)}, status_code=400)

    return JSONResponse(
        {
            "policy": preview.policy.to_json(),
            "provenance": preview.policy.provenance,
            "reach": sorted(preview.policy.reach),
            "conflicts": _conflicts_json(preview.conflicts),
        }
    )


def _conflicts_json(conflicts: Any) -> list[dict[str, Any]]:
    return [
        {
            "field": conflict.field,
            "values": [{"role": role, "value": value} for role, value in conflict.values],
        }
        for conflict in conflicts
    ]


async def _put_overlay(request: Request) -> Response:
    from starlette.responses import JSONResponse

    services = services_of(request)
    name = request.path_params["name"]

    try:
        fragment = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    if not isinstance(fragment, dict):
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    async with connection(request) as conn:
        try:
            change = await set_overlay(
                name,
                fragment,
                actor=admin_actor(request),
                source=services.policy_source(),
                directory=services.directory,
                accounts=AccountStore(conn),
                generations=GenerationStore(conn),
                audit=AuditStore(conn),
                grants=GrantStore(conn),
                admin_policy=services.admin_policy,
                mirror=services.settings.audit_mirror_path,
            )
        except Denied as exc:
            return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)
        except FragmentError as exc:
            return JSONResponse(
                {"error": "invalid_policy", "detail": str(exc)}, status_code=400
            )

    return JSONResponse(
        {
            "account": change.account,
            "generation": change.generation,
            "changed": change.changed,
            "policy": change.policy.to_json(),
        }
    )


async def _delete_overlay(request: Request) -> Response:
    from starlette.responses import JSONResponse

    services = services_of(request)
    name = request.path_params["name"]

    async with connection(request) as conn:
        try:
            change = await clear_overlay(
                name,
                actor=admin_actor(request),
                source=services.policy_source(),
                directory=services.directory,
                accounts=AccountStore(conn),
                generations=GenerationStore(conn),
                audit=AuditStore(conn),
                grants=GrantStore(conn),
                admin_policy=services.admin_policy,
                mirror=services.settings.audit_mirror_path,
            )
        except Denied as exc:
            return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)

    return JSONResponse({"account": change.account, "generation": change.generation})


async def _list_grants(request: Request) -> Response:
    from starlette.responses import JSONResponse

    async with connection(request) as conn:
        pairs = await GrantStore(conn).list_grants()

    return JSONResponse({"grants": [{"group": g, "role": r} for g, r in pairs]})


async def _add_grant(request: Request) -> Response:
    return await _mutate_grant(request, grant=True)


async def _remove_grant(request: Request) -> Response:
    return await _mutate_grant(request, grant=False)


async def _mutate_grant(request: Request, *, grant: bool) -> Response:
    from starlette.responses import JSONResponse

    services = services_of(request)

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    group = body.get("group")
    role = body.get("role")
    if not isinstance(group, str) or not isinstance(role, str) or not group or not role:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    operation = grant_role if grant else revoke_role

    async with connection(request) as conn:
        grants = GrantStore(conn)
        if grant:
            accounts = AccountStore(conn)
            for member in await accounts.members(group):
                if member.disabled:
                    continue
                conflicts = await account_role_conflicts(
                    services.policy_source(),
                    member.name,
                    directory=services.directory,
                    grants=grants,
                    extra_roles=[role],
                )
                if conflicts:
                    return JSONResponse(
                        {
                            "error": "role_conflict",
                            "detail": (f"{member.name}: {conflict_detail(conflicts)}"),
                        },
                        status_code=400,
                    )
        try:
            changed = await operation(
                group,
                role,
                actor=admin_actor(request),
                directory=services.directory,
                grants=grants,
                audit=AuditStore(conn),
                admin_policy=services.admin_policy,
                mirror=services.settings.audit_mirror_path,
            )
        except Denied as exc:
            return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)
        # As a first member does: a grant whose group has no row would be in force
        # for nobody and shown nowhere (D85).
        if grant:
            await AccountStore(conn).ensure_group(group)

    return JSONResponse({"changed": changed})
