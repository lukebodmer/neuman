"""The model panel: what exists, what is loaded, what it costs.

**Operator-only, including the reads.** How full the GPU is and which models an
appliance serves is its topology, and an ordinary user has no business enumerating
it — they select a model from the names policy grants them and need nothing else.
The check is `authorize()` from `decision/identity/authz.py`, and the test here
asserts that an *authenticated but unprivileged* session is refused, not merely an
anonymous one (N32).

**Loading and unloading go through the runtime host** (D78), which holds the GPU
and is the only thing that knows what is resident. The path parameter is a name
the host matches against what it was told to run; it never becomes a unit, a
command or a file, so a request naming anything else is a 404 from the host as
well as from here.

**A load that would not fit is refused, not attempted.** The model's measured
need is compared with what the GPU sample says is free, and the answer names
what is using the rest. Trying anyway is not harmless: a load that runs out of
memory partway can take a model that was working down with it, and the error it
leaves is llama.cpp's "failed to init model/context", which says nothing an
operator can act on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...adapters.persistence import GrantStore
from ...decision.identity.authz import Denied, require_admin
from ...decision.policy.ops import preview_policy
from ..waiting import until_newer_sample
from .deps import Unauthorized, connection, require_csrf, require_principal, services_of

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request
    from starlette.responses import Response

__all__ = ["model_routes"]


def model_routes() -> list[Any]:
    from starlette.routing import Route

    return [
        Route("/api/models/state", _state, methods=["GET"]),
        Route("/api/models/{name}/load", _load, methods=["POST"]),
        Route("/api/models/{name}/unload", _unload, methods=["POST"]),
    ]


async def _operator(request: Request, *, unsafe: bool) -> Any:
    """Resolve the caller and require operator authority.

    Raises `Unauthorized` for "not signed in" and `Denied` for "signed in without
    authority" — two different answers, because conflating them tells an ordinary
    user their session is broken.
    """

    services = services_of(request)
    async with connection(request) as conn:
        principal = await require_principal(request, conn)
        if unsafe:
            require_csrf(request, principal)

    await require_admin(
        principal.name,
        directory=services.directory,
        policy=services.admin_policy,
    )
    return principal


async def _state(request: Request) -> Response:
    from starlette.responses import JSONResponse

    services = services_of(request)

    try:
        principal = await _operator(request, unsafe=False)
    except Unauthorized as exc:
        return JSONResponse({"error": exc.reason}, status_code=exc.status)
    except Denied as exc:
        return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)

    # `?after=<sampled_at>`: answer when there is something newer than what the
    # panel already drew. Waited out before any database work, so a held
    # request holds no connection.
    after = request.query_params.get("after")
    if after:
        try:
            await until_newer_sample(services.gpu, float(after))
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

    # What this account may *select*, and what the catalog knows about each. The panel
    # replaces the header dropdown, so it has to answer the question that dropdown
    # answered — and answer it better: "deepseek or qwen" says nothing about which
    # runs on your own GPU and which is billed per token.
    #
    # Policy first, catalog second. The panel shows what the user may choose, never
    # the appliance's whole catalog.
    async with connection(request) as conn:
        policy = await preview_policy(
            principal.name,
            source=services.policy_source(),
            directory=services.directory,
            grants=GrantStore(conn),
        )
        granted = list(policy.models)

    # Outside the connection block: the catalog is a file now, not a table.
    catalog = services.model_catalog().describe(granted)

    models = _local_models(services, await services.runtime.status())
    gpu = services.gpu.gpu()
    # Attribute VRAM to a model where we can. `nvidia-smi` reports process names,
    # not models, so this is a hint rather than an accounting: a loaded model is
    # credited with what a runtime process holds only when exactly one model is
    # loaded and one runtime process holds memory.
    runtimes = [p for p in gpu.get("processes", []) if _is_runtime(p.get("name", ""))]
    loaded = [m for m in models if m["loaded"]]
    if len(runtimes) == 1 and len(loaded) == 1:
        loaded[0]["vram_mb"] = runtimes[0].get("memory_mb")

    # Chat models the user may select, which is a different list from the local
    # models above: a cloud model is selectable and has nothing to load, and a local
    # model may be declared but not granted.
    by_name = {m["name"]: m for m in models}
    chat = []
    for name in granted:
        entry = catalog.get(name, {})
        local = bool(entry.get("local"))
        state = by_name.get(name, {})
        chat.append(
            {
                "name": name,
                # Whether the runtime host serves it: a local entry is one with no
                # provider, which the catalog states rather than this guessing it
                # from a loopback URL.
                "local": local,
                "vision": entry.get("vision", False),
                # Zero for a local model, and zero is the point — it is what makes the
                # comparison with a cloud model legible.
                "input_cost_per_m": entry.get("input_cost_per_m", 0.0),
                "output_cost_per_m": entry.get("output_cost_per_m", 0.0),
                "enabled": entry.get("enabled", True),
                # Only meaningful for a local model; None for cloud, where residency is
                # not a concept.
                "loaded": state.get("loaded") if local else None,
                "size_mb": state.get("size_mb") if local else None,
                "vram_mb": state.get("vram_mb") if local else None,
                "error": state.get("error") if local else None,
                # A granted name with no catalog row cannot be selected — the harness
                # would answer `model_error` — so the panel says so rather than
                # offering it.
                "registered": name in catalog,
                # What the client draws the thinking control from. Null for a
                # model that declares none, which is the signal to draw nothing
                # at all: a control that cannot work is absent rather than
                # present and broken.
                "reasoning": entry.get("reasoning"),
            }
        )

    return JSONResponse({"models": models, "chat": chat, "gpu": gpu})


async def _load(request: Request) -> Response:
    return await _act(request, "load")


async def _unload(request: Request) -> Response:
    return await _act(request, "unload")


async def _act(request: Request, verb: str) -> Response:
    from starlette.responses import JSONResponse

    services = services_of(request)
    name = request.path_params["name"]

    try:
        await _operator(request, unsafe=True)
    except Unauthorized as exc:
        return JSONResponse({"error": exc.reason}, status_code=exc.status)
    except Denied as exc:
        return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)

    model = (services.baseline.get("models") or {}).get(name)
    if not isinstance(model, dict) or "local" not in model:
        return JSONResponse({"error": "not_found"}, status_code=404)

    if verb == "load":
        refusal = await _no_room(services, name)
        if refusal is not None:
            return JSONResponse({"error": "no_room", "detail": refusal}, status_code=409)

    act = services.runtime.load if verb == "load" else services.runtime.unload
    status, body = await act(name)
    if status != 200:
        return JSONResponse(
            {"error": f"{verb}_failed", "detail": str(body.get("detail") or body.get("error"))},
            status_code=502 if status != 404 else 404,
        )
    # A load returns before the weights are resident; the panel's next poll
    # reports the truth.
    return JSONResponse({"name": name, "state": body.get("state", "unloaded")})


async def _no_room(services: Any, name: str) -> str | None:
    """Why `name` cannot be loaded now, or None when it can or nobody can tell.

    Unknown is not a refusal: a model with no measurement, settings that differ
    from the ones it was measured at, or no GPU sample all let the load go
    ahead and the runtime report what happened.
    """

    status = await services.runtime.status()
    if status is None:
        return None
    chat = status.get("chat") or []
    if any(m.get("name") == name and m.get("loaded") for m in chat):
        return None
    need = _need_mib(services, name)
    free = _free_mib(services.gpu.gpu())
    if need is None or free is None or need <= free:
        return None

    holders = [model["name"] for model in chat if model.get("loaded")]
    elsewhere = (
        f"Unload {', '.join(holders)} first."
        if holders
        else "Something outside Neuman is using the rest."
    )
    return (
        f"There isn't room on the GPU for {name}: it needs {_gib(need)} and "
        f"{_gib(free)} is free. {elsewhere}"
    )


def _need_mib(services: Any, name: str) -> int | None:
    """The preset's measured VRAM, when this entry runs as it was measured."""

    entry = (services.baseline.get("models") or {}).get(name) or {}
    preset = (services.presets.get("models") or {}).get(entry.get("preset"))
    if not isinstance(preset, dict) or not isinstance(preset.get("local"), dict):
        return None
    need = (preset["local"]["facts"].get("vram") or {}).get("mib")
    # Measured at the preset's starting window, offload, speculation and cache
    # type. A window lowered or a cache compacted to fit a smaller card is
    # exactly the case a stale number would refuse wrongly, so a changed
    # setting means unknown.
    starting = preset["local"].get("policy") or {}
    local = entry.get("local") or {}
    if (
        entry.get("context_tokens") != starting.get("context_size")
        or local.get("speculation", "none") != starting.get("speculation", "none")
        or local.get("kv_cache", "f16") != starting.get("kv_cache", "f16")
        or local.get("gpu_layers", 99) != starting.get("gpu_layers", 99)
    ):
        return None
    return need if isinstance(need, int) else None


def _free_mib(sample: dict[str, Any]) -> int | None:
    """Free memory on the largest device, or None without a sample."""

    if not sample.get("available"):
        return None
    free = [
        device["memory_total_mb"] - device["memory_used_mb"]
        for device in sample.get("devices") or []
        if isinstance(device.get("memory_total_mb"), int)
        and isinstance(device.get("memory_used_mb"), int)
    ]
    return max(free) if free else None


def _gib(mib: int) -> str:
    return f"{mib / 1024:.1f} GiB"


def _local_models(services: Any, status: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Every model this machine runs, for the panel.

    `loaded` is None when the host does not answer: unknown, which the panel
    draws differently from "off".
    """

    baseline = services.baseline
    presets = services.presets.get("models") or {}
    by_name = {m["name"]: m for m in (status or {}).get("chat") or []}
    out: list[dict[str, Any]] = []
    for name, model in sorted((baseline.get("models") or {}).items()):
        local = model.get("local")
        if not isinstance(local, dict):
            continue
        reported = by_name.get(name)
        out.append(
            {
                "name": name,
                "residency": local.get("residency", "on-demand"),
                "loaded": None if status is None else bool(reported and reported["loaded"]),
                "size_mb": _weights_mb(presets.get(model.get("preset"))),
                "vram_mb": None,
                # Why its last load failed, when the host saw why: the router
                # itself says only "failed to load".
                "error": (reported or {}).get("error"),
            }
        )
    return out


def _is_runtime(name: str) -> bool:
    """Whether a GPU process looks like a model runtime rather than a desktop."""

    return "llama" in name


def _weights_mb(preset: Any) -> int | None:
    """The preset's download size: an estimate of VRAM, labelled as one in the UI."""

    if not isinstance(preset, dict) or not isinstance(preset.get("local"), dict):
        return None
    files = preset["local"]["facts"]["weights"]["files"]
    return sum(file["size"] for file in files) // (1024 * 1024)
