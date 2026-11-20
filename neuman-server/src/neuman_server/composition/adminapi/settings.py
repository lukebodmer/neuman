"""Runtime settings: the policy/model baseline, the prompt, and events.

The baseline is one database document holding the policy layers and the model
catalog, so an operator adds a provider, a model, a role or a user without a
rebuild (D68). The prompt is the same story one field down.

A baseline write bumps every account's policy generation after storing it. The
policy engine re-reads on every step anyway; the generation is how a live
session is told its policy changed rather than discovering it one call later.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...adapters.persistence import AccountStore, AuditStore, GenerationStore, GrantStore
from ...adapters.persistence.events import ServerEventStore
from ...adapters.persistence.settings import SettingsStore
from ...decision.audit import chain
from ...decision.harness.presets import catalog_problems
from ...decision.harness.prompt import DEFAULT_NAME
from ...decision.identity.authz import Denied, require_admin
from ...decision.policy.fragments import SCALAR_DEFAULTS
from ...decision.policy.generations import record
from ...decision.policy.ops import preview_policy
from ...decision.policy.roles import RoleError, rename_in_baseline
from ...decision.policy.source import (
    PolicySource,
    baseline_role_conflicts,
    conflict_detail,
)
from ...foundation.state import sweep_secrets
from ..runtime import publish_runtime
from .deps import admin_actor, connection, services_of

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request
    from starlette.responses import Response

__all__ = ["admin_settings_routes", "record_generations", "sweep_keys"]


def admin_settings_routes() -> list[Any]:
    from starlette.routing import Route

    return [
        Route("/admin/baseline", _get_baseline, methods=["GET"]),
        Route("/admin/baseline", _put_baseline, methods=["PUT"]),
        Route("/admin/roles/{name}/rename", _rename_role, methods=["POST"]),
        Route("/admin/prompt", _get_prompt, methods=["GET"]),
        Route("/admin/prompt", _put_prompt, methods=["PUT"]),
        Route("/admin/prompt/compose", _compose_prompt, methods=["GET"]),
        Route("/admin/name", _put_name, methods=["PUT"]),
        Route("/admin/events", _events, methods=["GET"]),
    ]


async def _get_baseline(request: Request) -> Response:
    from starlette.responses import JSONResponse

    # The built-in values go with the document because the panel names them: a field
    # left unset is only as clear as what it falls back to.
    return JSONResponse(
        {"baseline": services_of(request).baseline, "built_in": SCALAR_DEFAULTS}
    )


async def _put_baseline(request: Request) -> Response:
    from jsonschema import ValidationError
    from neuman_contracts import baseline as contract
    from starlette.responses import JSONResponse

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    if not isinstance(body, dict) or not isinstance(body.get("baseline"), dict):
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    document = body["baseline"]
    # The document the edit was based on. The panel always sends it, so a tab
    # that has been open while another added a role gets 409 instead of
    # overwriting it. Omitted by scripts that mean "replace the baseline", and
    # the CLI is one: an operator setting the document whole is stating the
    # whole answer.
    base = body.get("base")
    if base is not None and not isinstance(base, dict):
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    try:
        contract.validate(document)
    except ValidationError as exc:
        return JSONResponse(
            {"error": "invalid_baseline", "detail": exc.message}, status_code=400
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

        conflicts = await baseline_role_conflicts(
            PolicySource(
                baseline=document,
                overlay_dir=str(services.settings.overlay_dir),
            ),
            accounts=AccountStore(conn),
            directory=services.directory,
            grants=GrantStore(conn),
        )
        if conflicts:
            detail = "; ".join(
                f"{name}: {conflict_detail(found)}" for name, found in conflicts.items()
            )
            return JSONResponse({"error": "role_conflict", "detail": detail}, status_code=400)

        problems = catalog_problems(document, services.presets)
        if problems:
            return JSONResponse(
                {"error": "invalid_catalog", "detail": "; ".join(problems)}, status_code=400
            )

        store = SettingsStore(conn)
        if base is None:
            await store.set("baseline", document)
        else:
            replaced = await store.replace_if("baseline", expected=base, value=document)
            if not replaced:
                return JSONResponse(
                    {
                        "error": "baseline_changed",
                        "detail": (
                            "the baseline changed since it was read; reload and "
                            "reapply the edit"
                        ),
                    },
                    status_code=409,
                )
        # Swap the dict rather than mutating it: a turn already holding the old
        # document keeps it for that step, and the next read sees the new one.
        services.baseline = document
        publish_runtime(services)

        await record_generations(conn, services)
        sweep_keys(services)

        await chain.append(
            AuditStore(conn),
            actor=actor,
            action="settings.baseline_set",
            payload={
                "users": len(document.get("users") or []),
                "models": len(document.get("models") or {}),
                "providers": len(document.get("providers") or {}),
            },
            mirror=services.settings.audit_mirror_path,
        )

    return JSONResponse({"baseline": document})


async def _rename_role(request: Request) -> Response:
    """Move a role name through the baseline, declared users and group grants."""

    from starlette.responses import JSONResponse

    old = request.path_params["name"]
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    if not isinstance(body, dict) or not isinstance(body.get("to"), str):
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    new = body["to"]
    base = body.get("base")
    if base is not None and not isinstance(base, dict):
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

        store = SettingsStore(conn)
        # `services.baseline` is the document every read already composes
        # from, and every baseline write swaps it. A missing one means startup
        # did not complete, not that the caller sent a bad request.
        current = services.baseline
        if not isinstance(current, dict):
            return JSONResponse({"error": "no_baseline"}, status_code=409)
        if base is not None and base != current:
            return JSONResponse(
                {
                    "error": "baseline_changed",
                    "detail": "the baseline changed since it was read; reload and reapply",
                },
                status_code=409,
            )

        try:
            document = rename_in_baseline(current, old, new)
        except RoleError as exc:
            return JSONResponse({"error": exc.reason, "detail": exc.detail}, status_code=400)

        grants = GrantStore(conn)
        async with conn.transaction():
            replaced = await store.replace_if("baseline", expected=current, value=document)
            if not replaced:
                return JSONResponse(
                    {
                        "error": "baseline_changed",
                        "detail": "the baseline changed since it was read; reload and reapply",
                    },
                    status_code=409,
                )
            moved = await grants.rename_role(old, new)

        # Swap only after the rename committed. A later failure leaves the old
        # document in memory rather than one the database rejected.
        services.baseline = document
        await record_generations(conn, services)
        await chain.append(
            AuditStore(conn),
            actor=actor,
            action="settings.role_renamed",
            payload={"from": old, "to": new, "grants": moved},
            mirror=services.settings.audit_mirror_path,
        )

    return JSONResponse({"baseline": document, "from": old, "to": new, "grants": moved})


def sweep_keys(services: Any) -> list[str]:
    """Remove the key files the current baseline no longer names (D77)."""

    keep = {
        provider.get("api_key_file")
        for provider in (services.baseline.get("providers") or {}).values()
        if provider.get("api_key_file")
    }
    return sweep_secrets(services.settings.secrets_dir, keep)


async def record_generations(conn: Any, services: Any) -> None:
    """Re-record every account's generation after a baseline change."""

    source = services.policy_source()
    accounts = AccountStore(conn)
    generations = GenerationStore(conn)
    for row in await accounts.list_all():
        policy = await preview_policy(
            row.name,
            source=source,
            directory=services.directory,
            grants=GrantStore(conn),
        )
        await record(generations, row.id, policy)


async def _get_prompt(request: Request) -> Response:
    from starlette.responses import JSONResponse

    return JSONResponse({"text": services_of(request).operator_prompt})


async def _put_prompt(request: Request) -> Response:
    from starlette.responses import JSONResponse

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    if not isinstance(body, dict) or not isinstance(body.get("text"), str):
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    text = body["text"]

    services = services_of(request)
    async with connection(request) as conn:
        actor = admin_actor(request)
        try:
            await require_admin(
                actor, directory=services.directory, policy=services.admin_policy
            )
        except Denied as exc:
            return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)

        await SettingsStore(conn).set("prompt", text)
        services.operator_prompt = text
        await chain.append(
            AuditStore(conn),
            actor=actor,
            action="settings.prompt_set",
            payload={"characters": len(text)},
            mirror=services.settings.audit_mirror_path,
        )

    return JSONResponse({"text": text})


# The ceiling on the appliance's name, in characters. A name is drawn in a
# window's chrome and interpolated into one sentence, so it is a word rather
# than an essay, and the bound keeps it from pushing the identity sentence onto
# a second line in a narrow window.
MAX_NAME = 64


async def _put_name(request: Request) -> Response:
    """Rename the appliance: the identity sentence and the window's brand.

    Voice, not authority (D82). The name is interpolated into the sentence above
    the reach list and no dispatch reads it, so an operator setting it changes
    what the agent is *called* and nothing about what it may do.

    An empty name is the default, not a refusal. The field is the appliance's
    wordmark, cleared to put the name back to what the release ships; a form
    that refused it made the operator retype the word they had just deleted
    (N175).
    """

    from starlette.responses import JSONResponse

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    if not isinstance(body, dict) or not isinstance(body.get("name"), str):
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    # Collapsed rather than refused: the prompt collapses it too, so a second
    # space is a typo and not an error to report. Control characters are
    # refused, because the name lands in a one-line sentence and in a window's
    # title, and neither has a place for one.
    name = " ".join(body["name"].split()) or DEFAULT_NAME
    # `detail` is the sentence the window shows when it puts the name back: an
    # operator who typed one word too many gets that, not `name_too_long`.
    if len(name) > MAX_NAME:
        return JSONResponse(
            {
                "error": "name_too_long",
                "detail": f"A name can be at most {MAX_NAME} characters.",
            },
            status_code=400,
        )
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in name):
        return JSONResponse(
            {"error": "name_invalid", "detail": "A name cannot contain control characters."},
            status_code=400,
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

        await SettingsStore(conn).set("name", name)
        services.agent_name = name
        await chain.append(
            AuditStore(conn),
            actor=actor,
            action="settings.name_set",
            payload={"name": name},
            mirror=services.settings.audit_mirror_path,
        )

    return JSONResponse({"name": name})


async def _compose_prompt(request: Request) -> Response:
    """The system prompt as one account would receive it, section by section.

    Read-only composition of what the code and the database already hold. The
    sections are named by *source* rather than by content, because the question
    an empty box raises is "where would this text come from" — and answering it
    in the page means not inferring the boundaries in JavaScript, which would
    be a second composition path that drifts from `system_prompt`.

    Two sections are missing and reported as such. The working directory's
    `AGENTS.md` and its development environment are reads on the acting machine
    (N152), so they arrive through a dispatch and not from here; composing them
    server-side would read *this host's* disk and quietly answer about the
    wrong machine. A past turn's logged request carries what was actually sent.
    """

    from starlette.responses import JSONResponse

    from ...decision.harness.prompt import prompt_sections
    from ...decision.policy.gate import grantable
    from ...decision.policy.paths import expand_paths

    services = services_of(request)
    name = (request.query_params.get("account") or "").strip()
    working_dir = (request.query_params.get("working_dir") or "").strip() or None
    if not name:
        return JSONResponse(
            {"error": "invalid_request", "detail": "account is required"},
            status_code=400,
        )

    async with connection(request) as conn:
        try:
            await require_admin(
                admin_actor(request),
                directory=services.directory,
                policy=services.admin_policy,
            )
        except Denied as exc:
            return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)

        row = await AccountStore(conn).by_name(name)
        if row is None:
            return JSONResponse({"error": "not_found", "detail": name}, status_code=404)

        policy = await preview_policy(
            row.name,
            source=services.policy_source(),
            directory=services.directory,
            grants=GrantStore(conn),
        )
        # `None` with no client connected, which expands `$HOME` to nothing —
        # the same answer a dispatch would get, so the preview does not promise
        # a path the turn could not reach (N145).
        home = services.execution.home(row.name)

    sections = prompt_sections(
        policy,
        user=row.name,
        tools=grantable(policy, services.registry),
        name=services.agent_name,
        working_dir=working_dir,
        operator_prompt=services.operator_prompt,
        account_prompt=row.instructions,
        expanded_read=expand_paths(
            policy.files_read, home=home, data_dirs=services.settings.home_data_dirs
        ),
        expanded_write=expand_paths(
            policy.files_write, home=home, data_dirs=services.settings.home_data_dirs
        ),
    )

    return JSONResponse(
        {
            "account": row.name,
            "sections": [
                {"source": source, "text": "\n".join(lines).strip("\n")}
                for source, lines in sections
                if any(line.strip() for line in lines)
            ],
            "omitted": ["project"],
            "note": (
                "The working directory's AGENTS.md and development environment "
                "are read on the acting machine, so they are not part of a "
                "preview. A finished turn's logged request has the exact text "
                "that was sent."
            ),
        }
    )


async def _events(request: Request) -> Response:
    from starlette.responses import JSONResponse

    try:
        limit = min(max(int(request.query_params.get("limit", "100")), 1), 500)
    except ValueError:
        limit = 100
    level = request.query_params.get("level") or None

    async with connection(request) as conn:
        rows = await ServerEventStore(conn).recent(limit=limit, level=level)
    return JSONResponse(
        {
            "events": [
                {
                    "id": row.id,
                    "at": row.at.isoformat(),
                    "level": row.level,
                    "logger": row.logger,
                    "message": row.message,
                    "fields": row.fields,
                }
                for row in rows
            ]
        }
    )
