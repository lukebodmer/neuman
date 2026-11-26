"""A user reading their own effective policy, and choosing their approval mode.

Mostly read-only and entirely self-scoped. People should be able to see what
they may do and which layer granted it — "why can't I write there?" is
otherwise unanswerable without an operator, and that makes the appliance feel
arbitrary.

**One field is writable: `auto_accept`.** How often a human is asked to approve
is a preference about their own attention, and routing it through an operator
made the appliance tiring in a way policy did not require. It is bounded rather
than free: `auto_accept_max` is the operator's ceiling, and a choice beyond it
is refused — so a role capped at "safe" can stop approving every `ls` and still
cannot authorise its own destructive work.

The write goes through `decision.policy.ops`, like every other policy mutation,
so it is validated, audited and generation-bumped by the same code (D8). The
rest of authoring remains an operator action on the admin surface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...adapters.persistence import GrantStore, UsageStore
from ...adapters.persistence.machines import MACHINE_LABEL
from ...decision.policy import gate
from ...decision.policy.fragments import AUTO_ACCEPT_MODES, FragmentError
from ...decision.policy.ops import choose_auto_accept, preview_policy
from ...decision.policy.paths import expand_paths
from .deps import (
    Unauthorized,
    acting_home,
    connection,
    require_csrf,
    require_principal,
    services_of,
)

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request
    from starlette.responses import Response

__all__ = ["policy_routes"]


def policy_routes() -> list[Any]:
    from starlette.routing import Route

    return [
        Route("/api/policy", _my_policy, methods=["GET"]),
        # The one writable field, on its own path rather than as a PATCH of
        # `/api/policy`. A route that accepted a policy document would have to
        # decide which of its fields a user may set; a route named for one field
        # cannot be asked the question.
        Route("/api/policy/auto-accept", _set_auto_accept, methods=["PUT"]),
        Route("/api/tools", _my_tools, methods=["GET"]),
        Route("/api/usage", _my_usage, methods=["GET"]),
    ]


async def _my_usage(request: Request) -> Response:
    """This account's token usage today, against its own cap.

    Today only, deliberately. `usage_daily` keeps a row per day so history is
    *there*, but the question this answers is "can I keep working" — and the
    cap it is measured against resets daily, so a week of totals would be a
    different question with a different denominator.

    `tokens_daily` comes from composed policy rather than the usage row: the cap
    is a policy fact and can change under a running day, so reading it from
    anywhere else would show a percentage against a limit no longer in force.
    None means uncapped, and the client must say so rather than dividing by it.
    """

    from starlette.responses import JSONResponse

    services = services_of(request)

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        policy = await preview_policy(
            principal.name,
            source=services.policy_source(),
            directory=services.directory,
            grants=GrantStore(conn),
        )
        totals = await UsageStore(conn).usage_for(principal.account_id)

    return JSONResponse(
        {
            # Settled is what completed turns actually cost; reserved is held
            # against calls still in flight. Reported apart rather than summed,
            # because a reservation is not yet a charge — it is released if the
            # turn fails — and a user watching one number jump and fall back
            # would reasonably think the accounting was wrong.
            "settled": totals["settled"],
            "reserved": totals["reserved"],
            "tokens_daily": policy.tokens_daily,
        }
    )


async def _set_auto_accept(request: Request) -> Response:
    """Choose this account's own approval mode, within its granted ceiling."""

    from starlette.responses import JSONResponse

    services = services_of(request)

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
            require_csrf(request, principal)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        mode = body.get("auto_accept") if isinstance(body, dict) else None
        if mode not in AUTO_ACCEPT_MODES:
            return JSONResponse(
                {"error": "invalid_request", "allowed": list(AUTO_ACCEPT_MODES)},
                status_code=400,
            )

        from ...adapters.persistence import AccountStore, AuditStore, GenerationStore

        try:
            change = await choose_auto_accept(
                principal.name,
                mode,
                source=services.policy_source(),
                directory=services.directory,
                accounts=AccountStore(conn),
                generations=GenerationStore(conn),
                audit=AuditStore(conn),
                grants=GrantStore(conn),
                mirror=services.settings.audit_mirror_path,
            )
        except FragmentError as exc:
            # Refused by name, because the fix is an operator raising the
            # ceiling — a different problem from a malformed request, and one
            # the user can act on by asking.
            return JSONResponse({"error": "above_ceiling", "detail": str(exc)}, status_code=403)

    return JSONResponse(
        {
            "auto_accept": change.policy.effective_auto_accept,
            "auto_accept_max": change.policy.auto_accept_max,
            "generation": change.generation,
        }
    )


async def _my_policy(request: Request) -> Response:
    from starlette.responses import JSONResponse

    services = services_of(request)

    # Which computer `$HOME` expands for. The picker the window builds from this
    # response offers directories, and a directory is a path on one computer
    # (D112) — so the question is not optional, and answering it with "whichever
    # client is connected" is how the picker came to offer the other machine's
    # folders (N186).
    machine = request.query_params.get("machine")
    if not machine or not MACHINE_LABEL.fullmatch(machine):
        return JSONResponse(
            {
                "error": "invalid_request",
                "detail": "policy must be read for a machine: add ?machine=<label>",
            },
            status_code=400,
        )

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        policy = await preview_policy(
            principal.name,
            source=services.policy_source(),
            directory=services.directory,
            grants=GrantStore(conn),
        )

    # What a model picker needs to answer "can I use this now", scoped to the
    # granted names. `registered` is the catalog's answer — a granted name with no
    # entry cannot be selected — and `loaded` is only meaningful for a local
    # model. The panel is operator-only; this is the non-operator's read of the
    # same fact, and it is what lets a default model that has gone missing show
    # as "no model" instead of being silently replaced by the first granted one.
    catalog = services.model_catalog().describe(list(policy.models))
    status = await services.runtime.status()
    loaded = {m.get("name") for m in (status or {}).get("chat") or [] if m.get("loaded")}
    model_info = []
    for name in policy.models:
        entry = catalog.get(name)
        info = {
            "name": name,
            "registered": entry is not None,
            "local": bool((entry or {}).get("local")),
            "loaded": (name in loaded) if (entry or {}).get("local") else None,
            "vision": bool((entry or {}).get("vision", False)),
            "reasoning": (entry or {}).get("reasoning"),
        }
        model_info.append(info)

    return JSONResponse(
        {
            "account": principal.name,
            "policy": policy.to_json(),
            "model_info": model_info,
            "reach": sorted(policy.reach),
            # Which layer set each field. The whole reason composition tracks it.
            "provenance": policy.provenance,
            # The *expanded* paths, which are what is actually reachable and
            # therefore what a client may offer as a working directory. A grant
            # naming the home expands to the home the client reported, so
            # `policy.files_read` may contain the token — which is not a path a
            # dispatch can use — and a picker built from it would offer a path
            # the server then refuses.
            "readable": expand_paths(
                policy.files_read,
                home=acting_home(services, principal, machine),
            ),
            "writable": expand_paths(
                policy.files_write,
                home=acting_home(services, principal, machine),
            ),
            "machine": machine,
            # What the composer draws its thinking control from, for the models
            # this account may select. Here rather than on the model panel
            # because the panel is operator-only — it is appliance topology —
            # and picking how hard your own model thinks is not. A non-operator
            # who could not read this would get no control at all.
            #
            # Scoped to the granted names, so this cannot become a way to
            # enumerate the appliance's whole catalog.
            "reasoning": {
                name: entry["reasoning"]
                for name, entry in services.model_catalog()
                .describe(list(policy.models))
                .items()
                if entry.get("reasoning")
            },
        }
    )


async def _my_tools(request: Request) -> Response:
    """The tools this user may actually call right now.

    Diagnostics are omitted entirely (`diagnostic` in the manifest): they exist
    to test the appliance, no baseline grants one, and listing one as ungranted
    invites an operator to grant a tool that allocates memory until the cgroup
    stops it. Filtered here rather than in the client because the route is the
    one place that already reads every manifest, and a name list in the browser
    would be a second statement of the same fact.

    This is presentation, not authority. A diagnostic that somehow *is* granted
    still dispatches — the gate decides that, not this route — so omitting it
    here cannot hide a live capability from an operator reading policy.
    """

    from starlette.responses import JSONResponse

    services = services_of(request)

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        policy = await preview_policy(
            principal.name,
            source=services.policy_source(),
            directory=services.directory,
            grants=GrantStore(conn),
        )

    tools = []
    for name in services.registry.names():
        manifest = services.registry.get(name)
        assert manifest is not None
        decision = gate.check(name, policy, services.registry)
        # Hidden only while it is *also* ungranted, which is every normal
        # appliance. A diagnostic that has actually been granted is a tool this
        # account can really call, and a chooser that omitted it would be the
        # one thing worse than listing it: a capability in force with no control
        # over it and nothing on screen saying it exists.
        if manifest.diagnostic and not decision.allowed:
            continue
        tools.append(
            {
                "name": name,
                # Both, because they are for different readers: `summary` is the
                # line the tool chooser shows a person, `description` is the
                # model's instructions and is long on purpose. The client
                # prefers the short one and falls back to clipping the long one
                # for a tool that has not been given a summary yet.
                "summary": manifest.summary,
                "description": manifest.description,
                "kind": manifest.kind,
                # Only ever true for a *granted* diagnostic, since an ungranted
                # one never reaches here. The client marks it, so a tool that
                # exists to exhaust a cgroup does not look like ordinary work.
                "diagnostic": manifest.diagnostic,
                "mutating": manifest.mutating,
                "critical": manifest.critical,
                "needs": sorted(manifest.needs),
                "allowed": decision.allowed,
                "reason": decision.reason,
            }
        )

    return JSONResponse({"tools": tools})
