"""Login, logout, and the account's own password.

Thin: it unpacks a request, calls `decision.identity.login`, and sets a cookie.
Every rule — throttling, the account lookup, argon2, audit — is in the
operation, so the admin surface performing a login gets exactly the same
behaviour (D9, D68).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...adapters.persistence import AccountStore, AuditStore, SessionStore
from ...decision.audit import chain
from ...decision.identity import AuthUnavailable, login, logout
from ...foundation import get_logger
from .deps import (
    COOKIE_NAME,
    Unauthorized,
    connection,
    require_csrf,
    require_principal,
    services_of,
)

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request
    from starlette.responses import Response

__all__ = ["auth_routes"]

_log = get_logger("neuman.server.http.auth")

_MAX_NAME = 64
_MAX_PASSWORD = 1024
# A minimum, not a composition rule. Length is the only requirement that
# reliably buys entropy; the rest is theatre that pushes people to reuse.
MIN_PASSWORD = 12


async def _is_admin(services: Any, name: str) -> bool:
    """Whether this account holds operator authority right now.

    Reported so the client knows whether to draw the admin panel. Not *trusted* for
    anything: every admin route re-checks with `require_admin`, because a flag in
    a response is a hint to a UI and an authorization decision belongs in the
    operation (D9). A forged `is_admin` gets a prettier button and the same 403.
    """

    from ...decision.identity.authz import authorize

    return (
        await authorize(name, directory=services.directory, policy=services.admin_policy)
    ).is_admin


def auth_routes() -> list[Any]:
    from starlette.routing import Route

    return [
        Route("/api/login", _login, methods=["POST"]),
        Route("/api/brand", _brand, methods=["GET"]),
        Route("/api/logout", _logout, methods=["POST"]),
        Route("/api/me", _me, methods=["GET"]),
        Route("/api/me/interface", _set_interface, methods=["PATCH"]),
        Route("/api/me/instructions", _set_instructions, methods=["PATCH"]),
        Route("/api/me/defaults", _set_defaults, methods=["PATCH"]),
        Route("/api/me/password", _change_password, methods=["PATCH"]),
    ]


async def _login(request: Request) -> Response:
    from starlette.responses import JSONResponse

    services = services_of(request)

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    name = body.get("username")
    password = body.get("password")
    if (
        not isinstance(name, str)
        or not isinstance(password, str)
        or not name
        or not password
        or len(name) > _MAX_NAME
        or len(password) > _MAX_PASSWORD
    ):
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    async with connection(request) as conn:
        try:
            result = await login(
                name=name,
                password=password,
                accounts=AccountStore(conn),
                passwords=services.passwords,
                sessions=SessionStore(conn),
                audit=AuditStore(conn),
                throttle=services.throttle,
                mirror=services.settings.audit_mirror_path,
            )
        except AuthUnavailable:
            # Hashing itself failed. That is a bug or a broken build, not a
            # denied credential; a 401 here would send the user to retype a
            # correct password.
            return JSONResponse({"error": "internal"}, status_code=500)

        # Inside the connection block, because the client needs the palette in
        # this response rather than in a request after first paint (D53), and the
        # row is already in reach here.
        preferences = (
            await _preferences(conn, result.account.name)
            if result.ok and result.account is not None
            else None
        )

    if not result.ok:
        status = 429 if result.reason == "throttled" else 401
        payload: dict[str, Any] = {"error": result.reason}
        headers = {}
        if result.retry_after_s:
            headers["retry-after"] = str(int(result.retry_after_s) + 1)
        return JSONResponse(payload, status_code=status, headers=headers)

    assert result.account is not None and result.token is not None
    assert preferences is not None
    response = JSONResponse(
        {
            "account": result.account.name,
            "csrf_token": result.csrf_token,
            "must_change_password": result.account.must_change_password,
            "is_admin": await _is_admin(services, result.account.name),
            **preferences,
        }
    )
    response.set_cookie(
        COOKIE_NAME,
        result.token,
        httponly=True,
        # Secure requires https, which is also why tests must use an https base
        # URL or the cookie is silently never sent.
        secure=True,
        samesite="strict",
        path="/",
    )
    return response


async def _logout(request: Request) -> Response:
    from starlette.responses import JSONResponse

    services = services_of(request)
    token = request.cookies.get(COOKIE_NAME)

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
            require_csrf(request, principal)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        if token:
            await logout(
                token,
                sessions=SessionStore(conn),
                audit=AuditStore(conn),
                actor=principal.name,
                mirror=services.settings.audit_mirror_path,
            )

    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


async def _brand(request: Request) -> Response:
    """What the appliance calls itself, before anyone signs in.

    Unauthenticated because the sign-in screen carries the name, and a renamed
    appliance whose door still says "Neuman" is the inconsistency this setting
    exists to remove. It answers with one operator-chosen word and nothing
    about accounts, policy or the database.
    """

    from starlette.responses import JSONResponse

    return JSONResponse({"name": services_of(request).agent_name})


async def _me(request: Request) -> Response:
    from starlette.responses import JSONResponse

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)
        preferences = await _preferences(conn, principal.name)

    return JSONResponse(
        {
            "account": principal.name,
            "csrf_token": principal.csrf_token,
            "must_change_password": principal.must_change_password,
            "is_admin": await _is_admin(services_of(request), principal.name),
            # What the appliance calls itself, so the window can brand itself
            # with the operator's name rather than a constant (D82). Sent with
            # the identity because the client draws its chrome before it draws
            # anything else.
            "agent_name": services_of(request).agent_name,
            **preferences,
        }
    )


async def _change_password(request: Request) -> Response:
    """Change the signed-in account's own password.

    Requires the current password even though the caller already holds a
    session: a borrowed laptop is the case this is for. A temporary password an
    admin set is changed here, which is also what clears
    `must_change_password`.
    """

    from starlette.responses import JSONResponse

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    current = body.get("current_password")
    new = body.get("new_password")
    if not isinstance(current, str) or not isinstance(new, str):
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    if len(new) < MIN_PASSWORD:
        return JSONResponse(
            {"error": "password_too_short", "min": MIN_PASSWORD}, status_code=400
        )
    if len(new) > _MAX_PASSWORD:
        return JSONResponse({"error": "password_too_long"}, status_code=400)

    services = services_of(request)
    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
            require_csrf(request, principal)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        accounts = AccountStore(conn)
        account = await accounts.by_name(principal.name)
        if account is None or not account.password_hash:
            return JSONResponse({"error": "invalid_credentials"}, status_code=401)
        if not await services.passwords.verify(account.password_hash, current):
            return JSONResponse({"error": "invalid_credentials"}, status_code=401)

        await accounts.set_password(
            name=account.name,
            password_hash=await services.passwords.hash(new),
            must_change_password=False,
        )
        await chain.append(
            AuditStore(conn),
            actor=principal.name,
            action="identity.password_changed",
            payload={"account": principal.name},
            mirror=services.settings.audit_mirror_path,
        )

    return JSONResponse({"ok": True})


async def _preferences(conn: Any, name: str) -> dict[str, Any]:
    """This account's own settings, for `/api/login` and `/api/me`.

    Read alongside the identity rather than from a route of its own, for the
    reason the palette exists on the `accounts` row at all: the client needs them
    in the response it already waits for before first paint. A second request
    would mean the page renders in the default palette and then corrects itself,
    which is a visible flash of the wrong theme (D53).

    `instructions` rides in the same response for a weaker version of that
    reason — the settings dialog should open with the text already in it rather
    than flashing an empty box — and because one read of the row answers both.

    One helper for both routes, because a field added to one and forgotten in the
    other gives a preference that applies on sign-in and reverts on reload.
    """

    row = await AccountStore(conn).by_name(name)
    if row is None:
        # Authenticated but no row is not reachable through `require_principal`,
        # which resolves the session *through* the account. Fall back to the
        # defaults rather than raising: a missing preference must never be the
        # thing that fails a login.
        return {
            "interface": {"palette": "dark", "collapse_reasoning": False, "active_minutes": 30},
            "instructions": "",
            "defaults": {"model": None, "reasoning": None},
        }
    return {
        "interface": {
            "palette": row.palette,
            "collapse_reasoning": row.collapse_reasoning,
            "active_minutes": row.active_minutes,
        },
        "instructions": row.instructions,
        "defaults": {"model": row.default_model, "reasoning": row.default_reasoning},
    }


async def _set_interface(request: Request) -> Response:
    """Change one or more of this account's client preferences.

    PATCH, not PUT: the client sends the control the user just touched, and a
    whole-record write would let the palette picker revert a reasoning checkbox
    changed in another tab. Absent means unchanged, which is also why none of
    the fields accepts null.

    Its own route rather than a field on some broader settings write, because
    this is the only user-writable preference surface and keeping it narrow is
    what lets the CSRF requirement and the validation sit in one obvious place.
    """

    from starlette.responses import JSONResponse

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    palette = body.get("palette")
    collapse = body.get("collapse_reasoning")
    minutes = body.get("active_minutes")

    # Typed before the store sees them. The CHECK constraint would catch a bad
    # palette and the column type would catch a non-boolean, but both arrive as
    # a 500 describing a database, and the client that sent it deserves to be
    # told which field it got wrong. `bool` is an `int` in Python, so it is
    # excluded by name: `true` is not a number of minutes.
    if palette is not None and not isinstance(palette, str):
        return JSONResponse({"error": "invalid_palette"}, status_code=400)
    if collapse is not None and not isinstance(collapse, bool):
        return JSONResponse({"error": "invalid_collapse_reasoning"}, status_code=400)
    if minutes is not None and (isinstance(minutes, bool) or not isinstance(minutes, int)):
        return JSONResponse({"error": "invalid_active_minutes"}, status_code=400)

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
            require_csrf(request, principal)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        accounts = AccountStore(conn)

        # The range is checked here rather than in the store because the bound is
        # what the refusal has to name. Read off the *instance* for the reason
        # the palette list is: the test harness replaces `AccountStore` with a
        # factory function, so a class attribute lookup raises instead of
        # answering.
        if minutes is not None and not 0 <= minutes <= accounts.ACTIVE_MINUTES_MAX:
            return JSONResponse(
                {"error": "invalid_active_minutes", "max": accounts.ACTIVE_MINUTES_MAX},
                status_code=400,
            )

        try:
            row = await accounts.set_interface(
                name=principal.name,
                palette=palette,
                collapse_reasoning=collapse,
                active_minutes=minutes,
            )
        except ValueError:
            # An unknown palette. The known set is small and public — it is in
            # the stylesheet the client already has — so naming it costs nothing
            # and saves a round of guessing.
            # Read off the *instance*, not the imported class: the test
            # harness replaces `AccountStore` with a factory function, so a
            # class attribute lookup here raises instead of answering. The
            # instance has it either way.
            return JSONResponse(
                {"error": "invalid_palette", "known": list(accounts.PALETTES)},
                status_code=400,
            )

        if row is None:
            return JSONResponse({"error": "no_such_account"}, status_code=404)

        return JSONResponse(
            {
                "palette": row.palette,
                "collapse_reasoning": row.collapse_reasoning,
                "active_minutes": row.active_minutes,
            }
        )


async def _set_defaults(request: Request) -> Response:
    """Set the model and reasoning a new branch starts with.

    Validated against the live catalog and the account's own grants, because a
    default that cannot be selected is worse than none: it would make every new
    conversation start in a state the composer has to correct. A later
    revocation is not repaired here — the client shows "no model" and says why.
    """

    from starlette.responses import JSONResponse

    from ...adapters.persistence import GrantStore
    from ...decision.policy.ops import preview_policy

    services = services_of(request)

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    model = body.get("model")
    reasoning = body.get("reasoning")
    if model is not None and (not isinstance(model, str) or not model):
        return JSONResponse({"error": "invalid_model"}, status_code=400)
    if reasoning is not None and (not isinstance(reasoning, str) or not reasoning):
        return JSONResponse({"error": "invalid_reasoning"}, status_code=400)
    if model is None and reasoning is not None:
        return JSONResponse({"error": "invalid_reasoning"}, status_code=400)

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
            require_csrf(request, principal)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        if model is not None:
            policy = await preview_policy(
                principal.name,
                source=services.policy_source(),
                directory=services.directory,
                grants=GrantStore(conn),
            )
            if model not in policy.models:
                return JSONResponse({"error": "model_not_granted"}, status_code=403)
            entry = services.model_catalog().describe([model]).get(model)
            if entry is None:
                return JSONResponse({"error": "model_unavailable"}, status_code=400)
            levels = (entry.get("reasoning") or {}).get("levels") or []
            if reasoning is not None and reasoning not in levels:
                return JSONResponse(
                    {"error": "invalid_reasoning", "allowed": list(levels)},
                    status_code=400,
                )
        elif reasoning is not None:
            return JSONResponse({"error": "invalid_reasoning"}, status_code=400)

        row = await AccountStore(conn).set_defaults(
            name=principal.name, model=model, reasoning=reasoning
        )
        if row is None:
            return JSONResponse({"error": "no_such_account"}, status_code=404)

    return JSONResponse(
        {"defaults": {"model": row.default_model, "reasoning": row.default_reasoning}}
    )


async def _set_instructions(request: Request) -> Response:
    """Replace this account's own standing instructions for the model.

    A whole value rather than a PATCH of controls: the client sends the whole
    textarea, because a textarea is one value. Empty clears them.

    Its own route for the reason `/api/me/interface` has one — this is a
    self-scoped write, so the CSRF check, the type check and the length bound sit
    in one obvious place. Not policy and not audited: it grants nothing, changes
    nothing about reach, and the text it produces appears verbatim in the
    system prompt of every request the harness logs, so the record exists where
    a reader would look for it (D65).
    """

    from starlette.responses import JSONResponse

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    text = body.get("instructions")
    if not isinstance(text, str):
        return JSONResponse({"error": "invalid_instructions"}, status_code=400)
    if "\x00" in text:
        # A JSON string can carry a NUL and Postgres TEXT cannot store one, so
        # without this the answer would be a 500 describing a database rather
        # than a 400 naming the field.
        return JSONResponse({"error": "invalid_instructions"}, status_code=400)

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
            require_csrf(request, principal)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        accounts = AccountStore(conn)
        try:
            row = await accounts.set_instructions(
                name=principal.name,
                instructions=text,
            )
        except ValueError:
            # Over the ceiling. Read off the *instance* for the reason the
            # palette's known set is: the test harness replaces `AccountStore`
            # with a factory function, so a class attribute lookup here raises
            # instead of answering.
            return JSONResponse(
                {"error": "instructions_too_long", "limit": accounts.MAX_INSTRUCTIONS},
                status_code=400,
            )

        if row is None:
            return JSONResponse({"error": "no_such_account"}, status_code=404)

        return JSONResponse({"instructions": row.instructions})
