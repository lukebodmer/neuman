"""What a preset means for a baseline entry (D39, D76).

A preset states two kinds of value. **Facts** describe the model or the route to
it — the provider's id, the window, whether it sees, the reasoning dialect and
the levels the route forwards — and contradicting one does not change the model,
it only makes Neuman wrong about it, which surfaces as a provider error mid-turn
after a tool has already run (design principle 7). **Policy** is where an entry
starts and is the operator's to change.

So an entry added from a preset records it, and three things follow here:
`build_entry` makes the entry, `catalog_problems` refuses a write that
contradicts a recorded preset, and `refresh_facts` re-applies the facts at
startup so a fact corrected in a release reaches every appliance that runs it.
This was once an assertion the NixOS module made at evaluation time; it lives
here now that the catalog is a database row, and it is stronger for it, because
a row can be edited by something other than a rebuild.

Absent facts mean what they mean in the baseline: no `vision` is text-only, no
`context_tokens` is an unknown window, no dialect is a model not asked to
reason. An entry may not claim more than its preset does.

A model may also be offered on this machine (`local`). The local route's facts
are a pinned download and the flags the model needs; what the machine chooses —
the window, the offload, speculation, the cache type — is typed policy, so the runtime that
starts one builds its own command line and no stored document can extend it.
"""

from __future__ import annotations

import copy
import re
from typing import Any

__all__ = [
    "build_entry",
    "build_local_entry",
    "catalog_problems",
    "local_facts",
    "local_policy",
    "preset_problems",
    "refresh_facts",
    "route_facts",
    "route_policy",
]

# The provider fact a preset fixes. The key is the deployment's, so it is not
# here, and neither is the name, which the operator chooses.
_PROVIDER_FACTS = ("url",)


def local_facts(presets: dict[str, Any], model: str) -> dict[str, Any] | None:
    """The facts of `model` run on this machine, or None when it is not offered so.

    The model's shared reasoning dialect merged under the local route's own
    facts, and `vision` derived rather than stated: a model sees exactly when it
    is started with a projector, because the projector is what makes it so.
    """

    preset = (presets.get("models") or {}).get(model)
    if not isinstance(preset, dict) or not isinstance(preset.get("local"), dict):
        return None
    shared = {k: v for k, v in (preset.get("facts") or {}).items() if k == "reasoning"}
    facts = {**shared, **preset["local"]["facts"]}
    facts["vision"] = any(file["role"] == "mmproj" for file in facts["weights"]["files"])
    return facts


def local_policy(presets: dict[str, Any], model: str) -> dict[str, Any]:
    """Where a local entry's policy starts: the model's default level, then the route's."""

    preset = presets["models"][model]
    shared = {k: v for k, v in (preset.get("policy") or {}).items() if k == "reasoning_default"}
    return {**shared, **(preset["local"].get("policy") or {})}


def route_facts(presets: dict[str, Any], model: str, route: str) -> dict[str, Any] | None:
    """The facts of `model` reached through provider preset `route`, merged.

    None when either is unknown. The route's own facts win over the model's: a
    route decides what it forwards, so a level list stated once for the model
    and inherited is the shape that sends a level the route refuses.
    """

    preset = (presets.get("models") or {}).get(model)
    if not isinstance(preset, dict):
        return None
    variant = (preset.get("routes") or {}).get(route)
    if not isinstance(variant, dict):
        return None
    return {**(preset.get("facts") or {}), **(variant.get("facts") or {})}


def route_policy(presets: dict[str, Any], model: str, route: str) -> dict[str, Any]:
    """Where `model` on `route` starts: the model's policy, then the route's."""

    preset = presets["models"][model]
    return {**(preset.get("policy") or {}), **(preset["routes"][route].get("policy") or {})}


def build_entry(
    presets: dict[str, Any], model: str, route: str, provider: str
) -> dict[str, Any]:
    """The baseline entry for preset `model` served by baseline provider `provider`.

    `route` is the provider preset that provider was added from. Raises KeyError
    when the model has no such route, which the caller has already checked and
    answers as a 400 naming the routes it does have.
    """

    facts = route_facts(presets, model, route)
    if facts is None:
        raise KeyError(f"{model} has no route {route}")
    policy = route_policy(presets, model, route)

    entry: dict[str, Any] = {
        "provider": provider,
        "preset": model,
        "provider_model": facts["provider_model"],
    }
    if "context_tokens" in facts:
        entry["context_tokens"] = facts["context_tokens"]
    if facts.get("vision"):
        entry["vision"] = True
    for field in (
        "max_tokens",
        "input_cost_per_m",
        "cached_input_cost_per_m",
        "output_cost_per_m",
    ):
        if field in policy:
            entry[field] = policy[field]
    if "reasoning" in facts:
        entry["reasoning"] = _reasoning(facts["reasoning"], policy.get("reasoning_default"))
    return entry


# What a local entry keeps under `local`: the runtime's own settings. The window
# and reply length are the entry's `context_tokens` and `max_tokens`, as for any
# model, and the reasoning default is the entry's too.
_LOCAL_KEYS = ("gpu_layers", "speculation", "kv_cache", "residency", "idle_minutes")


def build_local_entry(presets: dict[str, Any], model: str, name: str) -> dict[str, Any]:
    """The baseline entry for preset `model` run on this machine under `name`.

    No provider: the endpoint is the appliance's runtime host, a deployment
    fact rather than a document one. `provider_model` is the catalog name,
    because that is what the runtime's router knows the model as. Raises
    KeyError when the preset has no local route.
    """

    facts = local_facts(presets, model)
    if facts is None:
        raise KeyError(f"{model} does not run on this machine")
    policy = local_policy(presets, model)
    entry: dict[str, Any] = {
        "provider_model": name,
        "preset": model,
        "context_tokens": policy["context_size"],
        "local": {key: policy[key] for key in _LOCAL_KEYS if key in policy},
    }
    if facts["vision"]:
        entry["vision"] = True
    if "max_tokens" in policy:
        entry["max_tokens"] = policy["max_tokens"]
    if "reasoning" in facts:
        entry["reasoning"] = _reasoning(facts["reasoning"], policy.get("reasoning_default"))
    return entry


def _local_entry_facts(presets: dict[str, Any], model: str, name: str) -> dict[str, Any] | None:
    facts = local_facts(presets, model)
    if facts is None:
        return None
    return {
        "provider_model": name,
        "vision": facts["vision"],
        **({"reasoning": facts["reasoning"]} if "reasoning" in facts else {}),
    }


def _reasoning(dialect: dict[str, Any], default: str | None) -> dict[str, Any]:
    reasoning = {"control": dialect["control"], "levels": list(dialect["levels"])}
    if default and default in reasoning["levels"]:
        reasoning["default"] = default
    return reasoning


def _fact_mismatches(entry: dict[str, Any], facts: dict[str, Any]) -> list[str]:
    """Each fact of `entry` that is not the preset's, as `field is X, the preset says Y`."""

    stated: dict[str, Any] = {
        "provider_model": facts["provider_model"],
        "vision": bool(facts.get("vision", False)),
    }
    actual: dict[str, Any] = {
        "provider_model": entry.get("provider_model"),
        "vision": bool(entry.get("vision", False)),
    }
    if "local" not in entry:
        # A hosted window is the provider's. A local one is the operator's:
        # it is what the runtime is started with, a VRAM decision.
        stated["context_tokens"] = facts.get("context_tokens", 0)
        actual["context_tokens"] = entry.get("context_tokens", 0)

    reasoning = entry.get("reasoning") if isinstance(entry.get("reasoning"), dict) else None
    dialect = facts.get("reasoning")
    if dialect is None:
        stated["reasoning.control"] = "none"
        actual["reasoning.control"] = (reasoning or {}).get("control", "none")
    else:
        stated["reasoning.control"] = dialect["control"]
        stated["reasoning.levels"] = list(dialect["levels"])
        actual["reasoning.control"] = (reasoning or {}).get("control", "none")
        actual["reasoning.levels"] = list((reasoning or {}).get("levels") or [])

    return [
        f"{field} is {_show(actual[field])}, but the preset states {_show(value)}"
        for field, value in stated.items()
        if actual[field] != value
    ]


def _show(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(str(item) for item in value) + "]"
    return repr(value)


def catalog_problems(document: dict[str, Any], presets: dict[str, Any]) -> list[str]:
    """Every reason `document`'s catalog cannot be stored, as sentences.

    Empty means it may be written. Checked on every write of the baseline,
    whichever route made it, so the panel's form, a script's `baseline set` and
    the tile flow are held to one rule.
    """

    providers = document.get("providers") or {}
    models = document.get("models") or {}
    problems: list[str] = []

    for name, provider in sorted(providers.items()):
        preset = provider.get("preset")
        if preset is None:
            continue
        stated = (presets.get("providers") or {}).get(preset)
        if stated is None:
            problems.append(
                f"provider `{name}` names preset `{preset}`, which Neuman does not ship"
            )
            continue
        for field in _PROVIDER_FACTS:
            if provider.get(field) != stated[field]:
                problems.append(
                    f"provider `{name}`: {field} is {_show(provider.get(field))}, "
                    f"but the preset states {_show(stated[field])}"
                )

    for name, model in sorted(models.items()):
        if "local" in model:
            problems.extend(_local_entry_problems(name, model, presets))
            continue
        provider = providers.get(model.get("provider"))
        if provider is None:
            # The resolver would refuse this at turn time anyway; refusing the
            # write says so to the person who can fix it.
            problems.append(
                f"model `{name}` names provider `{model.get('provider')}`, which is not declared"
            )
            continue

        reasoning = model.get("reasoning")
        if isinstance(reasoning, dict) and reasoning.get("default") and reasoning.get("levels"):
            if reasoning["default"] not in reasoning["levels"]:
                # A user who picks no level would be refused against a default
                # they never chose.
                problems.append(
                    f"model `{name}`: reasoning default `{reasoning['default']}` is not "
                    "one of its levels"
                )

        preset = model.get("preset")
        if preset is None:
            continue
        stated_model = (presets.get("models") or {}).get(preset)
        if stated_model is None:
            problems.append(
                f"model `{name}` names preset `{preset}`, which Neuman does not ship"
            )
            continue
        route = provider.get("preset")
        facts = route_facts(presets, preset, route) if route else None
        if facts is None:
            routes = ", ".join(sorted(stated_model.get("routes") or {})) or "no provider"
            problems.append(
                f"model `{name}`: preset `{preset}` is served by {routes}, and provider "
                f"`{model['provider']}` is not one of them"
            )
            continue
        problems.extend(f"model `{name}`: {line}" for line in _fact_mismatches(model, facts))

    return problems


def _local_entry_problems(
    name: str, model: dict[str, Any], presets: dict[str, Any]
) -> list[str]:
    """Why a local entry cannot be stored: the runtime starts only what a preset describes."""

    preset = model.get("preset")
    facts = _local_entry_facts(presets, preset, name) if isinstance(preset, str) else None
    stated = (presets.get("models") or {}).get(preset) if isinstance(preset, str) else None
    if facts is None or stated is None:
        return [
            f"model `{name}` runs on this machine, so it must name a preset that does; "
            f"`{preset}` is not one"
        ]
    problems = [f"model `{name}`: {line}" for line in _fact_mismatches(model, facts)]
    if not model.get("context_tokens"):
        # The runtime is started with this window; zero would be llama.cpp's
        # own default, which nobody chose and the context manager cannot see.
        problems.append(f"model `{name}`: a model on this machine needs `context_tokens`")
    reasoning = model.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("default"):
        if reasoning["default"] not in (reasoning.get("levels") or []):
            problems.append(
                f"model `{name}`: reasoning default `{reasoning['default']}` is not "
                "one of its levels"
            )
    return problems


def refresh_facts(
    document: dict[str, Any], presets: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """`document` with every recorded preset's facts re-applied, and what changed.

    Run at startup. A fact corrected in a release — a window that grew, a route
    that stopped forwarding a level — reaches an appliance with that release
    rather than waiting for someone to re-add the model. Policy is untouched,
    except a reasoning default the route no longer accepts, which falls back to
    the preset's own.

    An entry whose preset the release no longer ships keeps its values and
    loses the record: it becomes a hand-declared entry rather than one held to a
    preset that cannot be read.
    """

    updated = copy.deepcopy(document)
    changes: list[str] = []
    providers = updated.get("providers") or {}

    for name, provider in providers.items():
        preset = provider.get("preset")
        if preset is None:
            continue
        stated = (presets.get("providers") or {}).get(preset)
        if stated is None:
            del provider["preset"]
            changes.append(f"provider `{name}`: preset `{preset}` is no longer shipped")
            continue
        for field in _PROVIDER_FACTS:
            if provider.get(field) != stated[field]:
                provider[field] = stated[field]
                changes.append(f"provider `{name}`: {field} updated")

    models = updated.get("models") or {}
    for name in sorted(models):
        model = models[name]
        if "local" not in model:
            continue
        facts = _local_entry_facts(presets, model.get("preset") or "", name)
        if facts is None:
            # The runtime would refuse to start it, so it cannot stay an entry a
            # user selects and every turn on it fails.
            del models[name]
            changes.append(
                f"model `{name}`: preset `{model.get('preset')}` no longer runs here"
            )
            continue
        if _fact_mismatches(model, facts):
            model["provider_model"] = name
            if facts["vision"]:
                model["vision"] = True
            else:
                model.pop("vision", None)
            if "reasoning" in facts:
                chosen = (model.get("reasoning") or {}).get("default")
                if chosen not in facts["reasoning"]["levels"]:
                    chosen = local_policy(presets, model["preset"]).get("reasoning_default")
                model["reasoning"] = _reasoning(facts["reasoning"], chosen)
            else:
                model.pop("reasoning", None)
            changes.append(f"model `{name}`: facts updated from preset `{model['preset']}`")

    for name, model in models.items():
        preset = model.get("preset")
        if preset is None or "local" in model:
            continue
        provider = providers.get(model.get("provider")) or {}
        facts = route_facts(presets, preset, provider.get("preset") or "")
        if facts is None:
            del model["preset"]
            changes.append(
                f"model `{name}`: preset `{preset}` is no longer shipped for its provider"
            )
            continue
        if not _fact_mismatches(model, facts):
            continue

        model["provider_model"] = facts["provider_model"]
        for field in ("context_tokens", "vision"):
            if field in facts:
                model[field] = facts[field]
            else:
                model.pop(field, None)
        dialect = facts.get("reasoning")
        if dialect is None:
            model.pop("reasoning", None)
        else:
            chosen = (model.get("reasoning") or {}).get("default")
            if chosen not in dialect["levels"]:
                chosen = route_policy(presets, preset, provider["preset"]).get(
                    "reasoning_default"
                )
            model["reasoning"] = _reasoning(dialect, chosen)
        changes.append(f"model `{name}`: facts updated from preset `{preset}`")

    return updated, changes


def preset_problems(presets: dict[str, Any]) -> list[str]:
    """What the schema cannot say about the shipped presets.

    A route must be a provider Neuman ships, and a policy default must be one of
    the route's levels. Both are cross-references, which a JSON schema cannot
    express; checked at startup and by the tests over the real files. A local
    route is checked for what the runtime can start (`_local_problems`), and no
    two entries the presets can add may share a name, nor two local routes a
    weights file.
    """

    problems: list[str] = []
    providers = presets.get("providers") or {}
    names: dict[str, str] = {}
    # `modelsDir` is flat and a file is fetched, verified and deleted by its
    # name, so two presets naming one file would refuse each other's download
    # and delete each other's weights. Repositories reuse names like
    # `mmproj-F16.gguf`, which is what `path` is for.
    weights: dict[str, str] = {}
    for model_id, model in sorted((presets.get("models") or {}).items()):
        offered = [model["name"]] if model.get("routes") else []
        if isinstance(model.get("local"), dict):
            offered.append(model["local"].get("name", model["name"]))
            problems.extend(_local_problems(presets, model_id))
            for file in model["local"]["facts"]["weights"]["files"]:
                if weights.setdefault(file["name"], model_id) != model_id:
                    problems.append(
                        f"model preset `{model_id}` on this machine names weights file "
                        f"`{file['name']}`, which `{weights[file['name']]}` also names"
                    )
        for name in offered:
            if names.setdefault(name, model_id) != model_id or offered.count(name) > 1:
                problems.append(f"model preset `{model_id}` adds `{name}`, which is taken")
        for route in sorted(model.get("routes") or {}):
            if route not in providers:
                problems.append(
                    f"model preset `{model_id}` has a route at unknown provider `{route}`"
                )
                continue
            facts = route_facts(presets, model_id, route) or {}
            policy = route_policy(presets, model_id, route)
            default = policy.get("reasoning_default")
            dialect = facts.get("reasoning")
            if default and (dialect is None or default not in dialect["levels"]):
                problems.append(
                    f"model preset `{model_id}` on `{route}`: reasoning default `{default}` "
                    "is not one of the route's levels"
                )
    return problems


def _local_problems(presets: dict[str, Any], model_id: str) -> list[str]:
    """What is wrong with `model_id`'s local route, as sentences."""

    facts = local_facts(presets, model_id) or {}
    policy = local_policy(presets, model_id)
    where = f"model preset `{model_id}` on this machine"
    problems: list[str] = []

    files = facts["weights"]["files"]
    roles = [file["role"] for file in files]
    shards = [file["name"] for file in files if file["role"] == "model"]
    if not shards:
        problems.append(f"{where}: names no model file")
    elif len(shards) > 1 and not _is_split(shards):
        # The runtime is handed the first and llama.cpp derives the others'
        # names from it, so a set that is not exactly 1..N in that naming
        # starts a model with a shard missing, or none at all.
        problems.append(
            f"{where}: names {len(shards)} model files that are not one split GGUF, "
            "-00001-of-N to -N-of-N in order"
        )
    if roles.count("mmproj") > 1:
        problems.append(
            f"{where}: names {roles.count('mmproj')} projectors; a runtime loads one"
        )
    if len({file["name"] for file in files}) != len(files):
        problems.append(f"{where}: names one file twice")

    if "context_size" not in policy:
        # The window is what the context manager budgets against; an entry
        # without one would be managed against a guess (N86).
        problems.append(f"{where}: states no `context_size`")

    default = policy.get("reasoning_default")
    dialect = facts.get("reasoning")
    if default and (dialect is None or default not in dialect["levels"]):
        problems.append(f"{where}: reasoning default `{default}` is not one of its levels")
    return problems


# llama.cpp's split naming (`gguf-split`): `<prefix>-00001-of-00011.gguf`.
_SHARD = re.compile(r"^(?P<prefix>.+)-(?P<index>\d{5})-of-(?P<count>\d{5})\.gguf$")


def _is_split(names: list[str]) -> bool:
    """Whether `names` are every shard of one split GGUF, first to last."""

    matches = [_SHARD.fullmatch(name) for name in names]
    if not all(matches):
        return False
    return all(
        match["prefix"] == matches[0]["prefix"]
        and int(match["index"]) == position
        and int(match["count"]) == len(names)
        for position, match in enumerate(matches, start=1)
    )
