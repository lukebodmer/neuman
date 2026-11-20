"""Account and group administration.

The operations an operator needs on a running appliance: create a person, set a
temporary password, disable a leaver, make an administrator, and put people in
the groups policy grants name. Every mutation is guarded by `require_admin`,
audited, and takes effect on the next request — no server restart, no database
restart, no rebuild (D68).

`/admin/bootstrap` is the one route the guard lets through without an existing
administrator: it creates the first one from the local socket. It is reachable
only by a peer the kernel identified, and only while no enabled administrator
exists (or with `force`, which is the recovery path).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ...adapters.persistence import (
    AccountStore,
    AgentSessionStore,
    AuditStore,
    GrantStore,
    SessionStore,
    SettingsStore,
    UsageStore,
)
from ...decision.audit import chain
from ...decision.identity.authz import Denied, require_admin
from ...decision.policy.fragments import FragmentError
from ...decision.policy.ops import preview_draft, revoke_role
from ...decision.policy.roles import assign_roles_in_baseline
from ...decision.policy.source import account_role_conflicts, conflict_detail
from .deps import admin_actor, connection, peer_name, services_of

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request
    from starlette.responses import Response

__all__ = ["admin_account_routes"]

# A login name, not a display name. Lower-case, no spaces, no path separators;
# the same shape `common.schema.json#/$defs/userName` already puts in the
# baseline, so an account and a policy entry can never disagree about what a
# name is.
_NAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
MIN_PASSWORD = 12
MAX_PASSWORD = 1024


class _BaselineChanged(Exception):
    """The baseline moved while an account creation was holding it."""


def admin_account_routes() -> list[Any]:
    from starlette.routing import Route

    return [
        Route("/admin/bootstrap", _bootstrap, methods=["POST"]),
        Route("/admin/accounts", _list_accounts, methods=["GET"]),
        Route("/admin/accounts", _create_account, methods=["POST"]),
        Route("/admin/accounts/{name}", _update_account, methods=["PATCH"]),
        Route("/admin/accounts/{name}/password", _reset_password, methods=["POST"]),
        Route("/admin/groups", _list_groups, methods=["GET"]),
        Route("/admin/groups", _create_group, methods=["POST"]),
        Route("/admin/groups/{group}", _delete_group, methods=["DELETE"]),
        Route("/admin/groups/{group}/members", _members, methods=["GET"]),
        Route("/admin/groups/{group}/members", _add_member, methods=["POST"]),
        Route("/admin/groups/{group}/members/{name}", _remove_member, methods=["DELETE"]),
    ]


def _valid_password(password: Any) -> bool:
    return isinstance(password, str) and MIN_PASSWORD <= len(password) <= MAX_PASSWORD


async def _bootstrap(request: Request) -> Response:
    from starlette.responses import JSONResponse

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    try:
        actor = peer_name(request)
    except Exception:  # noqa: BLE001 - the socket is the only expected caller
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    name = body.get("name") or actor
    password = body.get("password")
    force = bool(body.get("force", False))
    if not isinstance(name, str) or not _NAME.match(name):
        return JSONResponse({"error": "invalid_name"}, status_code=400)
    if not _valid_password(password):
        return JSONResponse(
            {"error": "password_too_short", "min": MIN_PASSWORD}, status_code=400
        )

    services = services_of(request)
    async with connection(request) as conn:
        accounts = AccountStore(conn)
        if await accounts.has_admin() and not force:
            return JSONResponse({"error": "already_bootstrapped"}, status_code=409)

        password_hash = await services.passwords.hash(password)
        existing = await accounts.by_name(name)
        if existing is None:
            created = await accounts.create(
                name=name, password_hash=password_hash, must_change_password=False
            )
            if created is None:
                return JSONResponse({"error": "name_taken"}, status_code=409)
        else:
            await accounts.set_password(
                name=name, password_hash=password_hash, must_change_password=False
            )
        await accounts.add_member(group="neuman-admin", name=name)
        await chain.append(
            AuditStore(conn),
            actor=actor,
            action="identity.bootstrap_admin",
            payload={"account": name, "force": force},
            mirror=services.settings.audit_mirror_path,
        )

    return JSONResponse({"account": name, "admin": True})


async def _list_accounts(request: Request) -> Response:
    """One page of the roster: identity, groups, today's tokens, session count.

    Paged and searched in SQL rather than in the browser. The list used to be
    every account with one `groups_of` query each, which is a page of fifty
    costing fifty queries before it draws a single row; at a few hundred
    accounts that is the difference between a table and a pause. The today and
    session figures are single queries over the page for the same reason.
    """

    from starlette.responses import JSONResponse

    params = request.query_params
    query = (params.get("q") or "").strip()
    try:
        limit = int(params.get("limit", "50"))
        offset = int(params.get("offset", "0"))
    except ValueError:
        return JSONResponse(
            {"error": "invalid_request", "detail": "limit and offset must be integers"},
            status_code=400,
        )
    limit = max(1, min(limit, 200))
    offset = max(offset, 0)

    async with connection(request) as conn:
        store = AccountStore(conn)
        total, rows = await store.page(query=query, limit=limit, offset=offset)
        ids = [row.id for row in rows]
        groups = await store.groups_for(ids)
        usage = await UsageStore(conn).today_for(ids, datetime.now(UTC).date())
        sessions = await AgentSessionStore(conn).counts_by_account(ids)
        accounts = [
            {
                "name": row.name,
                "disabled": row.disabled,
                "must_change_password": row.must_change_password,
                "admin": "neuman-admin" in groups.get(row.id, ()),
                "groups": sorted(groups.get(row.id, ())),
                "created": row.first_seen.isoformat() if row.first_seen else None,
                "last_seen": row.last_seen.isoformat() if row.last_seen else None,
                "settled": usage.get(row.id, (0, 0))[0],
                "reserved": usage.get(row.id, (0, 0))[1],
                "sessions": sessions.get(row.id, 0),
            }
            for row in rows
        ]

    return JSONResponse(
        {"accounts": accounts, "total": total, "limit": limit, "offset": offset}
    )


async def _create_account(request: Request) -> Response:
    from starlette.responses import JSONResponse

    body = await _body(request)
    if body is None:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    name = body.get("name")
    password = body.get("password")
    admin = bool(body.get("admin", False))
    must_change = bool(body.get("must_change_password", True))
    roles = body.get("roles") or []
    groups = body.get("groups") or []
    if not isinstance(name, str) or not _NAME.match(name):
        return JSONResponse({"error": "invalid_name"}, status_code=400)
    if not _valid_password(password):
        return JSONResponse(
            {"error": "password_too_short", "min": MIN_PASSWORD}, status_code=400
        )
    if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
        return JSONResponse({"error": "invalid_roles"}, status_code=400)
    if not isinstance(groups, list) or not all(isinstance(group, str) for group in groups):
        return JSONResponse({"error": "invalid_groups"}, status_code=400)
    if any(not _NAME.match(group) for group in groups):
        return JSONResponse({"error": "invalid_group"}, status_code=400)
    if "neuman-admin" in groups:
        # Authority is the `admin` field, not a group a caller can smuggle in
        # beside it. The panel filters this group out for the same reason.
        return JSONResponse({"error": "invalid_group"}, status_code=400)

    services = services_of(request)
    document: dict[str, Any] | None = None
    try:
        async with connection(request) as conn:
            actor = admin_actor(request)
            try:
                await require_admin(
                    actor, directory=services.directory, policy=services.admin_policy
                )
            except Denied as exc:
                return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)

            source = services.policy_source()
            # The baseline may already name roles for this name — an imported
            # baseline, or an account removed and created again. Creating it
            # without an explicit selection keeps them; an explicit selection
            # replaces them.
            listed = [
                role
                for role in (source.user_entry(name).get("roles") or [])
                if role in source.known_roles
            ]
            declared = sorted(set(roles)) if roles else sorted(set(listed))
            try:
                draft = preview_draft(source=source, roles=declared)
            except FragmentError as exc:
                return JSONResponse(
                    {"error": "unknown_role", "detail": str(exc)}, status_code=400
                )
            if draft.conflicts:
                return JSONResponse(
                    {
                        "error": "role_conflict",
                        "detail": conflict_detail(draft.conflicts),
                    },
                    status_code=400,
                )

            if groups or admin:
                preview_groups = set(groups)
                if admin:
                    preview_groups.add("neuman-admin")
                granted = await GrantStore(conn).grants_for_groups(frozenset(preview_groups))
                known = set(services.baseline.get("roles") or {})
                inherited = sorted({role for _, role in granted if role in known})
                draft = preview_draft(
                    source=source,
                    roles=sorted(set(declared) | set(inherited)),
                )
                if draft.conflicts:
                    return JSONResponse(
                        {
                            "error": "role_conflict",
                            "detail": conflict_detail(draft.conflicts),
                        },
                        status_code=400,
                    )

            async with conn.transaction():
                accounts = AccountStore(conn)
                created = await accounts.create(
                    name=name,
                    password_hash=await services.passwords.hash(password),
                    must_change_password=must_change,
                )
                if created is None:
                    return JSONResponse({"error": "name_taken"}, status_code=409)
                for group in sorted(set(groups)):
                    await accounts.add_member(group=group, name=name)
                if admin:
                    await accounts.add_member(group="neuman-admin", name=name)

                if roles:
                    current = services.baseline
                    next_document = assign_roles_in_baseline(current, name, roles)
                    replaced = await SettingsStore(conn).replace_if(
                        "baseline", expected=current, value=next_document
                    )
                    if not replaced:
                        raise _BaselineChanged
                    document = next_document

                await chain.append(
                    AuditStore(conn),
                    actor=actor,
                    action="identity.account_created",
                    payload={
                        "account": name,
                        "admin": admin,
                        "roles": declared,
                        "groups": sorted(set(groups)),
                    },
                    mirror=services.settings.audit_mirror_path,
                )
    except _BaselineChanged:
        return JSONResponse(
            {
                "error": "baseline_changed",
                "detail": "the baseline changed while the account was created; retry",
            },
            status_code=409,
        )

    if document is not None:
        services.baseline = document
    return JSONResponse(
        {
            "account": name,
            "admin": admin,
            "roles": declared,
            "groups": sorted(set(groups)),
        },
        status_code=201,
    )


async def _is_last_admin(accounts: AccountStore, name: str) -> bool:
    """Whether `name` is the only enabled administrator (D85).

    Disabled ones do not count: they cannot sign in to put anyone else back.
    """

    enabled = await accounts.members("neuman-admin")
    return {row.name.lower() for row in enabled if not row.disabled} == {name.lower()}


def _last_admin_refused() -> Response:
    from starlette.responses import JSONResponse

    return JSONResponse(
        {"error": "last_admin", "detail": "add another administrator first"}, status_code=400
    )


async def _update_account(request: Request) -> Response:
    from starlette.responses import JSONResponse

    body = await _body(request)
    if body is None:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    name = request.path_params["name"]
    disabled = body.get("disabled")
    must_change = body.get("must_change_password")
    admin = body.get("admin")
    if disabled is not None and not isinstance(disabled, bool):
        return JSONResponse({"error": "invalid_disabled"}, status_code=400)
    if must_change is not None and not isinstance(must_change, bool):
        return JSONResponse({"error": "invalid_must_change_password"}, status_code=400)
    if admin is not None and not isinstance(admin, bool):
        return JSONResponse({"error": "invalid_admin"}, status_code=400)

    services = services_of(request)
    async with connection(request) as conn:
        actor = admin_actor(request)
        try:
            await require_admin(
                actor, directory=services.directory, policy=services.admin_policy
            )
        except Denied as exc:
            return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)

        accounts = AccountStore(conn)
        row = await accounts.by_name(name)
        if row is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        if (disabled or admin is False) and await _is_last_admin(accounts, name):
            return _last_admin_refused()

        if disabled is not None:
            row = await accounts.set_disabled(name=name, disabled=disabled)
            if disabled:
                # Revocation is immediate: the next request has no session and
                # the account's client channel is closed rather than left to
                # receive a dispatch nobody can approve.
                await SessionStore(conn).delete_for_account(row.id)
                await services.execution.drop(name)
            await chain.append(
                AuditStore(conn),
                actor=actor,
                action="identity.account_disabled" if disabled else "identity.account_enabled",
                payload={"account": name},
                mirror=services.settings.audit_mirror_path,
            )
        if must_change is not None:
            row = await accounts.set_must_change_password(name=name, required=must_change)
        if admin is not None:
            if admin:
                grants = GrantStore(conn)
                admin_roles = [
                    role
                    for _, role in await grants.grants_for_groups(frozenset({"neuman-admin"}))
                ]
                conflicts = await account_role_conflicts(
                    services.policy_source(),
                    name,
                    directory=services.directory,
                    grants=grants,
                    extra_roles=admin_roles,
                )
                if conflicts:
                    return JSONResponse(
                        {
                            "error": "role_conflict",
                            "detail": conflict_detail(conflicts),
                        },
                        status_code=400,
                    )
                await accounts.add_member(group="neuman-admin", name=name)
            else:
                await accounts.remove_member(group="neuman-admin", name=name)
            await chain.append(
                AuditStore(conn),
                actor=actor,
                action="identity.admin_granted" if admin else "identity.admin_revoked",
                payload={"account": name},
                mirror=services.settings.audit_mirror_path,
            )

    return JSONResponse(
        {
            "account": name,
            "disabled": bool(row.disabled) if row else None,
            "must_change_password": bool(row.must_change_password) if row else None,
            "admin": admin if admin is not None else None,
        }
    )


async def _reset_password(request: Request) -> Response:
    from starlette.responses import JSONResponse

    body = await _body(request)
    if body is None:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    name = request.path_params["name"]
    password = body.get("password")
    must_change = bool(body.get("must_change_password", True))
    if not _valid_password(password):
        return JSONResponse(
            {"error": "password_too_short", "min": MIN_PASSWORD}, status_code=400
        )

    services = services_of(request)
    async with connection(request) as conn:
        actor = admin_actor(request)
        try:
            await require_admin(
                actor, directory=services.directory, policy=services.admin_policy
            )
        except Denied as exc:
            return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)

        accounts = AccountStore(conn)
        row = await accounts.set_password(
            name=name,
            password_hash=await services.passwords.hash(password),
            must_change_password=must_change,
        )
        if row is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        await SessionStore(conn).delete_for_account(row.id)
        await services.execution.drop(name)
        await chain.append(
            AuditStore(conn),
            actor=actor,
            action="identity.password_reset",
            payload={"account": name, "must_change_password": must_change},
            mirror=services.settings.audit_mirror_path,
        )
    return JSONResponse({"account": name, "must_change_password": must_change})


async def _list_groups(request: Request) -> Response:
    from starlette.responses import JSONResponse

    async with connection(request) as conn:
        store = AccountStore(conn)
        groups = []
        for name in await store.list_groups():
            groups.append(
                {"name": name, "members": [row.name for row in await store.members(name)]}
            )
    return JSONResponse({"groups": groups})


async def _create_group(request: Request) -> Response:
    from starlette.responses import JSONResponse

    body = await _body(request)
    if body is None:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    name = body.get("name")
    if not isinstance(name, str) or not _NAME.match(name):
        return JSONResponse({"error": "invalid_name"}, status_code=400)

    services = services_of(request)
    async with connection(request) as conn:
        actor = admin_actor(request)
        try:
            await require_admin(
                actor, directory=services.directory, policy=services.admin_policy
            )
        except Denied as exc:
            return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)
        accounts = AccountStore(conn)
        # Refused rather than ensured: a new group that landed on an old name
        # would quietly take on that group's members and roles.
        if await accounts.group_exists(name):
            return JSONResponse(
                {"error": "group_exists", "detail": f"{name} already exists"}, status_code=409
            )
        await accounts.ensure_group(name)
        await chain.append(
            AuditStore(conn),
            actor=actor,
            action="identity.group_created",
            payload={"group": name},
            mirror=services.settings.audit_mirror_path,
        )
    return JSONResponse({"group": name}, status_code=201)


async def _delete_group(request: Request) -> Response:
    """Delete a group, revoking its grants through the audited operation (D85)."""

    from starlette.responses import JSONResponse

    group = request.path_params["group"]
    services = services_of(request)
    async with connection(request) as conn:
        actor = admin_actor(request)
        try:
            await require_admin(
                actor, directory=services.directory, policy=services.admin_policy
            )
        except Denied as exc:
            return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)
        if group == "neuman-admin":
            return JSONResponse(
                {"error": "builtin_group", "detail": "neuman-admin holds admin authority"},
                status_code=400,
            )
        accounts = AccountStore(conn)
        if not await accounts.group_exists(group):
            return JSONResponse({"error": "not_found"}, status_code=404)

        grants = GrantStore(conn)
        members = [row.name for row in await accounts.members(group)]
        roles = [role for _, role in await grants.grants_for_groups(frozenset({group}))]
        for role in roles:
            await revoke_role(
                group,
                role,
                actor=actor,
                directory=services.directory,
                grants=grants,
                audit=AuditStore(conn),
                admin_policy=services.admin_policy,
                mirror=services.settings.audit_mirror_path,
            )
        await accounts.delete_group(group)
        await chain.append(
            AuditStore(conn),
            actor=actor,
            action="identity.group_deleted",
            payload={"group": group, "members": members, "roles": roles},
            mirror=services.settings.audit_mirror_path,
        )
    return JSONResponse({"group": group})


async def _members(request: Request) -> Response:
    from starlette.responses import JSONResponse

    group = request.path_params["group"]
    async with connection(request) as conn:
        rows = await AccountStore(conn).members(group)
    return JSONResponse({"group": group, "members": [row.name for row in rows]})


async def _add_member(request: Request) -> Response:
    from starlette.responses import JSONResponse

    body = await _body(request)
    if body is None:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    group = request.path_params["group"]
    name = body.get("account")
    if not isinstance(name, str):
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    services = services_of(request)
    async with connection(request) as conn:
        actor = admin_actor(request)
        try:
            await require_admin(
                actor, directory=services.directory, policy=services.admin_policy
            )
        except Denied as exc:
            return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)
        accounts = AccountStore(conn)
        if await accounts.by_name(name) is None:
            return JSONResponse({"error": "no_such_account"}, status_code=404)

        grants = GrantStore(conn)
        group_roles = [role for _, role in await grants.grants_for_groups(frozenset({group}))]
        conflicts = await account_role_conflicts(
            services.policy_source(),
            name,
            directory=services.directory,
            grants=grants,
            extra_roles=group_roles,
        )
        if conflicts:
            return JSONResponse(
                {"error": "role_conflict", "detail": conflict_detail(conflicts)},
                status_code=400,
            )

        added = await accounts.add_member(group=group, name=name)
        if not added:
            return JSONResponse({"error": "no_such_account"}, status_code=404)
        await chain.append(
            AuditStore(conn),
            actor=actor,
            action="identity.group_member_added",
            payload={"group": group, "account": name},
            mirror=services.settings.audit_mirror_path,
        )
    return JSONResponse({"group": group, "account": name})


async def _remove_member(request: Request) -> Response:
    from starlette.responses import JSONResponse

    group = request.path_params["group"]
    name = request.path_params["name"]

    services = services_of(request)
    async with connection(request) as conn:
        actor = admin_actor(request)
        try:
            await require_admin(
                actor, directory=services.directory, policy=services.admin_policy
            )
        except Denied as exc:
            return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)
        accounts = AccountStore(conn)
        if group == "neuman-admin" and await _is_last_admin(accounts, name):
            return _last_admin_refused()
        removed = await accounts.remove_member(group=group, name=name)
        if not removed:
            return JSONResponse({"error": "not_found"}, status_code=404)
        await chain.append(
            AuditStore(conn),
            actor=actor,
            action="identity.group_member_removed",
            payload={"group": group, "account": name},
            mirror=services.settings.audit_mirror_path,
        )
    return JSONResponse({"group": group, "account": name})


async def _body(request: Request) -> dict[str, Any] | None:
    try:
        body = await request.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None
