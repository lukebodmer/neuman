"""Adding providers, models and tool packages from what Neuman ships (D76, D77, D101).

The administration page offers the presets as tiles: a provider is added by
pasting its key, and a model by choosing it on a provider the appliance has.
These routes are how. Each builds the new baseline from the current one, checks
it with the same rule `PUT /admin/baseline` applies, and stores it with a
compare-and-set against what this process last read, so a tile clicked while
another tab saved a role is refused rather than erasing the role.

A model on this machine is added the same way once its weights are verified.
The weights themselves are fetched by `neuman-fetch` (D79); these routes only
ask it to, naming a preset.

A tool package is enabled the same way: a baseline write naming it in
`packages`, which puts the release's own tools for it in front of the gate and
grants them to nobody (D101). Nothing is downloaded — what a package needs on a
user's machine is that user's to install.

A key arrives in a request body and goes straight to a file the server alone can
read; the baseline holds its path. No response, log line or audit payload
carries it. Adding a model grants it to nobody — who may select it is a role's
`models` grant, and the two stay separate decisions.
"""

from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from jsonschema import ValidationError
from neuman_contracts import baseline as contract

from ...adapters.persistence import AuditStore
from ...adapters.persistence.settings import SettingsStore
from ...adapters.weights import weights_state
from ...decision.audit import chain
from ...decision.harness.presets import (
    build_entry,
    build_local_entry,
    catalog_problems,
    local_facts,
    local_policy,
)
from ...decision.identity.authz import Denied, require_admin
from ...foundation.state import SecretError, write_secret
from ..runtime import publish_runtime
from .deps import admin_actor, connection, services_of
from .settings import record_generations, sweep_keys

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request
    from starlette.responses import Response

__all__ = ["admin_catalog_routes"]

# The panel's rule for a name a user selects or a provider is keyed by.
_NAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*$")

# Where `http://` is not a network. The client holds the core to the same rule
# (`neuman_client/cli.py`): a key over plain HTTP is readable by anything on the
# path, and it would cross on every request.
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def admin_catalog_routes() -> list[Any]:
    from starlette.routing import Route

    return [
        Route("/admin/catalog", _catalog, methods=["GET"]),
        Route("/admin/providers", _add_provider, methods=["POST"]),
        Route("/admin/providers/{name}/key", _set_key, methods=["PUT"]),
        Route("/admin/models", _add_model, methods=["POST"]),
        Route("/admin/models/local", _add_local, methods=["POST"]),
        Route("/admin/weights/{preset}/fetch", _fetch, methods=["POST"]),
        Route("/admin/weights/{preset}/cancel", _cancel_fetch, methods=["POST"]),
        Route("/admin/weights/{preset}/delete", _delete_weights, methods=["POST"]),
        Route("/admin/tool-packages", _tool_packages, methods=["GET"]),
        Route("/admin/tool-packages/{package}", _set_tool_package, methods=["PUT"]),
    ]


class _Refused(Exception):
    """A request this module answers with a status and a sentence."""

    def __init__(self, status: int, error: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.error = error
        self.detail = detail


def _error(refused: _Refused) -> Response:
    from starlette.responses import JSONResponse

    return JSONResponse(
        {"error": refused.error, "detail": refused.detail}, status_code=refused.status
    )


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError as exc:
        raise _Refused(400, "invalid_request", "the body is not JSON") from exc
    if not isinstance(body, dict):
        raise _Refused(400, "invalid_request", "the body is not an object")
    return body


def _name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value) or value in {".", ".."}:
        raise _Refused(400, "invalid_name", f"{what} must be letters, digits, `_`, `-` or `.`")
    return value


async def _require_admin(request: Request) -> str:
    services = services_of(request)
    actor = admin_actor(request)
    try:
        await require_admin(actor, directory=services.directory, policy=services.admin_policy)
    except Denied as exc:
        raise _Refused(403, "forbidden", str(exc)) from exc
    return actor


async def _commit(
    request: Request,
    mutate: Any,
    *,
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Apply `mutate` to a copy of the baseline and store it, or refuse.

    `mutate` raises `_Refused` for a request that cannot apply to the current
    document. Admin authority is checked first, before anything is read or
    written, so a refusal never reveals what the catalog holds.
    """

    services = services_of(request)
    actor = await _require_admin(request)
    async with connection(request) as conn:
        current = services.baseline
        document = copy.deepcopy(current)
        document.setdefault("providers", {})
        document.setdefault("models", {})
        mutate(document)

        # The same contract `PUT /admin/baseline` checks. These routes build
        # their entries rather than accepting them, but a document that the
        # schema refuses would be refused again by the next panel save, and the
        # operator who could fix it would be told by the wrong request.
        try:
            contract.validate(document)
        except ValidationError as exc:
            raise _Refused(400, "invalid_baseline", exc.message) from exc

        problems = catalog_problems(document, services.presets)
        if problems:
            raise _Refused(400, "invalid_catalog", "; ".join(problems))

        stored = await SettingsStore(conn).replace_if(
            "baseline", expected=current, value=document
        )
        if not stored:
            raise _Refused(
                409,
                "baseline_changed",
                "the baseline changed since it was read; reload and try again",
            )
        services.baseline = document
        publish_runtime(services)
        await record_generations(conn, services)
        await chain.append(
            AuditStore(conn),
            actor=actor,
            action=action,
            payload=payload,
            mirror=services.settings.audit_mirror_path,
        )
    return document


async def _catalog(request: Request) -> Response:
    """The presets, and which of them this appliance already has.

    `added` lists the baseline names that came from each preset, so the page can
    mark a tile without a second request. A provider preset reports whether its
    entries hold a key and never what the key is; a model preset's routes say
    which providers here could serve it, and its `local`, when it has one, says
    whether the weights are in `modelsDir`, how far a download has got, whether
    they fit the GPU, and which catalog names use it.
    """

    from starlette.responses import JSONResponse

    services = services_of(request)
    presets = services.presets
    providers = services.baseline.get("providers") or {}
    models = services.baseline.get("models") or {}

    provider_tiles = []
    for preset_id, preset in sorted(presets["providers"].items()):
        added = sorted(
            name for name, entry in providers.items() if entry.get("preset") == preset_id
        )
        provider_tiles.append(
            {
                "id": preset_id,
                **{
                    key: preset[key]
                    for key in ("label", "logo", "notes", "url", "key_url")
                    if key in preset
                },
                "added": [
                    {"name": name, "key": bool(providers[name].get("api_key_file"))}
                    for name in added
                ],
            }
        )

    vram = _largest_gpu_mib(services)
    fetching = await services.fetch.status() if services.fetch is not None else None
    model_tiles = []
    for preset_id, preset in sorted(presets["models"].items()):
        routes = []
        for route, variant in sorted((preset.get("routes") or {}).items()):
            facts = {**(preset.get("facts") or {}), **variant["facts"]}
            routes.append(
                {
                    "provider": route,
                    "notes": variant.get("notes"),
                    "verified": variant["verified"],
                    "context_tokens": facts.get("context_tokens", 0),
                    "vision": bool(facts.get("vision", False)),
                    "reasoning": facts.get("reasoning"),
                    "policy": {**(preset.get("policy") or {}), **(variant.get("policy") or {})},
                    # The providers here this route can be added on.
                    "available": sorted(
                        name
                        for name, entry in providers.items()
                        if entry.get("preset") == route
                    ),
                }
            )
        model_tiles.append(
            {
                "id": preset_id,
                **{
                    key: preset[key]
                    for key in ("name", "label", "logo", "notes")
                    if key in preset
                },
                "routes": routes,
                "local": _local_tile(services, preset_id, vram, fetching),
                # Hosted entries only: one on this machine is the local route's
                # `added`, and counting it here drew the hosted tile as added.
                "added": sorted(
                    name
                    for name, entry in models.items()
                    if entry.get("preset") == preset_id and "local" not in entry
                ),
            }
        )

    return JSONResponse({"providers": provider_tiles, "models": model_tiles})


def _largest_gpu_mib(services: Any) -> int | None:
    """The biggest device's memory, or None when there is no GPU sample to read."""

    sample = services.gpu.gpu() if services.gpu is not None else {}
    totals = [
        device.get("memory_total_mb")
        for device in sample.get("devices") or []
        if isinstance(device, dict) and isinstance(device.get("memory_total_mb"), int)
    ]
    return max(totals) if sample.get("available") and totals else None


def _local_tile(
    services: Any, preset_id: str, vram: int | None, fetching: dict[str, Any] | None
) -> dict[str, Any] | None:
    """What the panel needs to offer a model on this machine, or None.

    `fits` compares the preset's measured VRAM at its default policy with the
    largest GPU here: a proposal, since a smaller window fits a smaller card,
    and None when either number is unknown rather than a guess either way.
    """

    facts = local_facts(services.presets, preset_id)
    if facts is None:
        return None
    local = services.presets["models"][preset_id]["local"]
    policy = local_policy(services.presets, preset_id)
    needs = (facts.get("vram") or {}).get("mib")
    models = services.baseline.get("models") or {}
    return {
        "name": local.get("name", services.presets["models"][preset_id]["name"]),
        "notes": local.get("notes"),
        "verified": local["verified"],
        "vision": facts["vision"],
        "reasoning": facts.get("reasoning"),
        "context_tokens": policy.get("context_size", 0),
        "policy": policy,
        "repo": facts["weights"]["repo"],
        "revision": facts["weights"]["revision"],
        "weights": weights_state(services.settings.models_dir, facts["weights"]["files"]),
        "vram_mib": needs,
        "gpu_mib": vram,
        "fits": None if needs is None or vram is None else needs <= vram,
        # The fetcher's job for this preset, or None: none started since it
        # last restarted, or no fetcher on this appliance.
        "fetch": (fetching or {}).get(preset_id),
        "added": sorted(
            name
            for name, entry in models.items()
            if "local" in entry and entry.get("preset") == preset_id
        ),
    }


async def _add_provider(request: Request) -> Response:
    """Add a provider from a preset and a key, or by hand from a URL.

    `{"preset": "deepseek", "api_key": "…"}` is the tile; `name` defaults to the
    preset's id and is needed only for a second account at one provider.
    `{"url": …, "name": …, "api_key"?: …}` is the custom form, for an endpoint
    Neuman does not ship, and a key is optional there because a loopback
    runtime has none.
    """

    from starlette.responses import JSONResponse

    services = services_of(request)
    try:
        body = await _body(request)
        preset_id = body.get("preset")
        if preset_id is not None:
            preset = services.presets["providers"].get(preset_id)
            if preset is None:
                raise _Refused(404, "unknown_preset", f"Neuman ships no provider `{preset_id}`")
            name = _name(body.get("name", preset_id), "name")
            url = preset["url"]
            if not body.get("api_key"):
                raise _Refused(400, "invalid_key", f"{preset['label']} needs an API key")
        else:
            name = _name(body.get("name"), "name")
            url = _url(body.get("url"))

        if name in (services.baseline.get("providers") or {}):
            raise _Refused(409, "provider_exists", f"a provider named `{name}` already exists")

        key_file = None
        if body.get("api_key"):
            key_file = _write_key(services, name, body["api_key"])

        entry: dict[str, Any] = {"url": url, "api_key_file": key_file}
        if preset_id is not None:
            entry["preset"] = preset_id

        def mutate(document: dict[str, Any]) -> None:
            if name in document["providers"]:
                raise _Refused(
                    409, "provider_exists", f"a provider named `{name}` already exists"
                )
            document["providers"][name] = entry

        try:
            document = await _commit(
                request,
                mutate,
                action="settings.provider_added",
                payload={
                    "name": name,
                    "preset": preset_id,
                    "url": url,
                    "key": key_file is not None,
                },
            )
        except _Refused:
            # The file was written before the document was; one that was never
            # stored names nothing, so it goes with the refusal.
            sweep_keys(services)
            raise
    except _Refused as refused:
        return _error(refused)

    return JSONResponse(
        {"provider": {"name": name, **entry}, "baseline": document}, status_code=201
    )


async def _set_key(request: Request) -> Response:
    """Replace a provider's key. The previous one is overwritten, not kept."""

    from starlette.responses import JSONResponse

    services = services_of(request)
    name = request.path_params["name"]
    try:
        body = await _body(request)
        if not body.get("api_key"):
            raise _Refused(400, "invalid_key", "the key is empty")
        if name not in (services.baseline.get("providers") or {}):
            raise _Refused(404, "not_found", f"no provider named `{name}`")

        # Admin first: writing the file is the change, so it must not happen for
        # a caller the commit below would refuse.
        actor = await _require_admin(request)

        path = _write_key(services, name, body["api_key"])
        document = services.baseline
        if document["providers"][name].get("api_key_file") != path:
            # A provider declared by hand may point at a secret manager's file.
            # A key entered here replaces that, so the entry names this file.
            def mutate(updated: dict[str, Any]) -> None:
                updated["providers"][name]["api_key_file"] = path

            document = await _commit(
                request, mutate, action="settings.provider_key_set", payload={"name": name}
            )
        else:
            async with connection(request) as conn:
                await chain.append(
                    AuditStore(conn),
                    actor=actor,
                    action="settings.provider_key_set",
                    payload={"name": name},
                    mirror=services.settings.audit_mirror_path,
                )
    except _Refused as refused:
        return _error(refused)

    return JSONResponse({"provider": {"name": name, "key": True}, "baseline": document})


async def _add_model(request: Request) -> Response:
    """Add preset `preset` served by baseline provider `provider`.

    The entry is the preset's facts for the route that provider was added from,
    and the preset's starting policy. `name` defaults to the preset's own and is
    needed only to add one model twice — on two providers, say.
    """

    from starlette.responses import JSONResponse

    services = services_of(request)
    try:
        body = await _body(request)
        preset_id = body.get("preset")
        preset = (
            services.presets["models"].get(preset_id) if isinstance(preset_id, str) else None
        )
        if preset is None:
            raise _Refused(404, "unknown_preset", f"Neuman ships no model `{preset_id}`")
        provider = body.get("provider")
        stated = (services.baseline.get("providers") or {}).get(provider)
        if stated is None:
            raise _Refused(404, "not_found", f"no provider named `{provider}`")
        route = stated.get("preset")
        if route not in (preset.get("routes") or {}):
            routes = ", ".join(sorted(preset.get("routes") or {})) or "no provider"
            raise _Refused(
                400,
                "no_route",
                f"{preset['label']} is served by {routes}; `{provider}` is not one of them",
            )
        name = _name(body.get("name", preset["name"]), "name")
        entry = build_entry(services.presets, preset_id, route, provider)

        def mutate(document: dict[str, Any]) -> None:
            if name in document["models"]:
                raise _Refused(
                    409, "model_exists", f"a model named `{name}` already exists; choose a name"
                )
            document["models"][name] = entry

        document = await _commit(
            request,
            mutate,
            action="settings.model_added",
            payload={"name": name, "preset": preset_id, "provider": provider},
        )
    except _Refused as refused:
        return _error(refused)

    return JSONResponse(
        {"model": {"name": name, **entry}, "baseline": document}, status_code=201
    )


def _verified_local(services: Any, preset_id: Any) -> dict[str, Any]:
    """The preset `preset_id`, refused unless it runs here and its weights are verified.

    Verified, not merely present: a file with the right name and size could be
    anything, and this is the moment a runtime is about to be pointed at it. The
    fetcher verifies — or adopts, for a file fetched by hand — in one pass.
    """

    preset = services.presets["models"].get(preset_id) if isinstance(preset_id, str) else None
    if preset is None or local_facts(services.presets, preset_id) is None:
        raise _Refused(404, "unknown_preset", f"Neuman runs no model `{preset_id}` here")
    files = preset["local"]["facts"]["weights"]["files"]
    state = weights_state(services.settings.models_dir, files)["state"]
    if state != "verified":
        raise _Refused(
            409,
            "weights_not_verified",
            f"{preset['label']}'s weights are {state}; fetch them first, which also "
            "verifies files already there",
        )
    return preset


async def _add_local(request: Request) -> Response:
    """Add preset `preset` run on this machine, under its local name or `name`."""

    from starlette.responses import JSONResponse

    services = services_of(request)
    try:
        body = await _body(request)
        preset_id = body.get("preset")
        preset = _verified_local(services, preset_id)
        name = _name(body.get("name", preset["local"].get("name", preset["name"])), "name")
        entry = build_local_entry(services.presets, preset_id, name)

        def mutate(document: dict[str, Any]) -> None:
            if name in document["models"]:
                raise _Refused(
                    409, "model_exists", f"a model named `{name}` already exists; choose a name"
                )
            document["models"][name] = entry

        document = await _commit(
            request,
            mutate,
            action="settings.local_model_added",
            payload={"name": name, "preset": preset_id},
        )
    except _Refused as refused:
        return _error(refused)

    return JSONResponse(
        {"model": {"name": name, **entry}, "baseline": document}, status_code=201
    )


async def _fetch(request: Request) -> Response:
    """Ask `neuman-fetch` to download, or verify, a preset's weights."""

    return await _ask_fetcher(request, "fetch")


async def _cancel_fetch(request: Request) -> Response:
    return await _ask_fetcher(request, "cancel")


async def _delete_weights(request: Request) -> Response:
    """Remove a preset's weights, refused while anything uses them.

    In use means a catalog entry names the preset: deleting the file under it
    would leave a model every user can select and none can run, failing at the
    first turn rather than here.
    """

    services = services_of(request)
    preset_id = request.path_params["preset"]
    baseline = services.baseline
    users = sorted(
        name
        for name, entry in (baseline.get("models") or {}).items()
        if "local" in entry and entry.get("preset") == preset_id
    )
    if users:
        return _error(
            _Refused(
                409,
                "weights_in_use",
                f"{', '.join(users)} use these weights; remove {'it' if len(users) == 1 else 'them'} first",
            )
        )
    return await _ask_fetcher(request, "delete")


async def _ask_fetcher(request: Request, verb: str) -> Response:
    from starlette.responses import JSONResponse

    services = services_of(request)
    preset_id = request.path_params["preset"]
    try:
        actor = await _require_admin(request)
        if local_facts(services.presets, preset_id) is None:
            raise _Refused(404, "unknown_preset", f"Neuman runs no model `{preset_id}` here")
        if services.fetch is None:
            raise _Refused(503, "no_fetcher", "this appliance has no fetcher")
        act = {
            "fetch": services.fetch.fetch,
            "cancel": services.fetch.cancel,
            "delete": services.fetch.delete,
        }[verb]
        status, body = await act(preset_id)
        if status >= 400:
            raise _Refused(
                status if status in (404, 409) else 502,
                f"{verb}_failed",
                str(body.get("detail") or body.get("error")),
            )
        if verb in ("fetch", "delete"):
            async with connection(request) as conn:
                await chain.append(
                    AuditStore(conn),
                    actor=actor,
                    action="weights.fetch_requested" if verb == "fetch" else "weights.deleted",
                    payload={"preset": preset_id},
                    mirror=services.settings.audit_mirror_path,
                )
    except _Refused as refused:
        return _error(refused)
    return JSONResponse(
        {"preset": preset_id, "fetch": body}, status_code=202 if verb == "fetch" else 200
    )


def _url(value: Any) -> str:
    """A custom provider's base URL: https, or http to this machine."""

    if not isinstance(value, str) or not value:
        raise _Refused(400, "invalid_url", "a provider needs a URL")
    parts = urlsplit(value)
    if parts.scheme == "https" and parts.hostname:
        return value
    if parts.scheme == "http" and parts.hostname in _LOOPBACK:
        return value
    raise _Refused(
        400,
        "invalid_url",
        f"{value} is not https and not on this machine; the key would cross the network in clear",
    )


def _write_key(services: Any, name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise _Refused(400, "invalid_key", "the key must be a string")
    try:
        return str(write_secret(services.settings.secrets_dir, name, value))
    except SecretError as exc:
        raise _Refused(400, "invalid_key", str(exc)) from exc


# -- tool packages -------------------------------------------------------------


def _granted_by(baseline: dict[str, Any], tool: str) -> list[str]:
    """The layers naming `tool` in their `tools`: `defaults` and role names."""

    layers = [("defaults", baseline.get("defaults") or {})]
    layers += sorted((baseline.get("roles") or {}).items())
    return [name for name, fragment in layers if tool in (fragment.get("tools") or [])]


def _tool_row(manifest: Any, baseline: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": manifest.name,
        "summary": manifest.summary or manifest.description[:160],
        "needs": sorted(manifest.needs),
        "mutating": bool(manifest.mutating),
        "critical": bool(manifest.critical),
        "returns_image": bool(manifest.returns_image),
        "granted_by": _granted_by(baseline, manifest.name),
    }


async def _tool_packages(request: Request) -> Response:
    """The Tools page: what is built in, and every package with whether it is on.

    Each tool carries what decides its exposure — the reach it needs, whether it
    asks, whether it returns a picture — and the layers granting it, so enabling
    a package is decided with the same facts a role edit is. Diagnostics are
    left out of the built-in list, as they are out of every chooser (D33).
    """

    from starlette.responses import JSONResponse

    services = services_of(request)
    try:
        await _require_admin(request)
    except _Refused as refused:
        return _error(refused)

    catalog = services.registry.catalog
    baseline = services.baseline or {}
    enabled = set(baseline.get("packages") or [])
    builtin = [
        _tool_row(manifest, baseline)
        for name in catalog.names()
        if catalog.package_of(name) is None
        and (manifest := catalog.get(name)) is not None
        and not manifest.diagnostic
    ]
    packages = [
        {
            "id": package.id,
            "title": package.title,
            "summary": package.summary,
            "description": package.description,
            "publisher": package.publisher,
            "enabled": package.id in enabled,
            "requires": package.requires,
            "host": package.host is not None,
            "tools": [
                _tool_row(manifest, baseline)
                for name in package.tools
                if (manifest := catalog.get(name)) is not None
            ],
        }
        for package in catalog.packages()
    ]
    return JSONResponse({"builtin": builtin, "packages": packages})


async def _set_tool_package(request: Request) -> Response:
    """Enable or disable one package for the whole appliance (D101).

    Enabling grants nothing: a role's `tools` decides who may use each tool,
    and the page says which roles already name them. Disabling withdraws the
    tools from every account at its next step, while the roles that name them
    keep the names — so enabling it again restores what they had.
    """

    from starlette.responses import JSONResponse

    services = services_of(request)
    package_id = request.path_params["package"]
    try:
        if services.registry.catalog.package(package_id) is None:
            raise _Refused(
                404, "unknown_package", f"Neuman ships no tool package `{package_id}`"
            )
        body = await _body(request)
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            raise _Refused(400, "invalid_request", "`enabled` must be true or false")

        def mutate(document: dict[str, Any]) -> None:
            current = [name for name in document.get("packages") or [] if name != package_id]
            document["packages"] = sorted([*current, package_id] if enabled else current)

        document = await _commit(
            request,
            mutate,
            action="settings.tool_package_enabled"
            if enabled
            else "settings.tool_package_disabled",
            payload={"package": package_id},
        )
    except _Refused as refused:
        return _error(refused)

    return JSONResponse({"package": package_id, "enabled": enabled, "baseline": document})
