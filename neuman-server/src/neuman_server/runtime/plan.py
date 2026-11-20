"""What to run, from the server's request and the shipped presets.

The server writes one JSON document naming which local models are in use and
their typed policy (`composition/runtime.py` writes it). This module is the
only reader, and it trusts none of it: every name must match a shipped preset
that runs on this machine, every number is bounded, and the files
are the preset's, joined to `modelsDir` here. What comes out is a `Plan` —
argument lists and an INI file the router reads — built entirely from values
that are either a preset's or checked by this code.

A document that fails is refused whole, and the host keeps running what it ran
before: a half-applied plan would be a GPU running something nobody chose.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "PRESETS_DIR",
    "Chat",
    "Plan",
    "PlanError",
    "load_local_presets",
    "parse_plan",
]

PRESETS_DIR = Path(__file__).resolve().parents[1] / "presets"

# A catalog name, used as an INI section header and the router's model id. The
# same rule the panel holds a name to, which also keeps `[`, `]`, `=` and line
# breaks out of the file this builds.
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$")


class PlanError(ValueError):
    """The server asked for something the presets do not allow."""


@dataclass(frozen=True, slots=True)
class Chat:
    name: str
    preset: str
    model: Path
    mmproj: Path | None
    model_flags: tuple[str, ...]
    context_size: int
    gpu_layers: int
    speculation: str
    kv_cache: str
    residency: str
    idle_minutes: int


@dataclass(frozen=True, slots=True)
class Plan:
    chat: tuple[Chat, ...] = ()

    def router_ini(self) -> str:
        """The `--models-preset` file: one section per chat model.

        Keys are `llama-server` long options without the dashes, which is how
        the router passes them to the child it starts (measured: the spawned
        argv lists each). `parallel = 1` so the window is the window rather
        than a pool divided among four slots — 4x the KV cache for nothing,
        since a branch runs one turn at a time and a second branch queues behind
        it rather than needing a slot of its own.

        The cache type is written as a pair from one value: K and V of
        different types have no flash-attention kernel on this build and run
        attention on the CPU, silently (N100).
        """

        lines: list[str] = []
        for model in self.chat:
            lines += [
                f"[{model.name}]",
                f"model = {model.model}",
                f"ctx-size = {model.context_size}",
                "parallel = 1",
                f"n-gpu-layers = {model.gpu_layers}",
            ]
            if model.mmproj is not None:
                lines.append(f"mmproj = {model.mmproj}")
            if model.speculation != "none":
                lines.append(f"spec-type = {model.speculation}")
            if model.kv_cache != "f16":
                lines += [
                    f"cache-type-k = {model.kv_cache}",
                    f"cache-type-v = {model.kv_cache}",
                ]
            if model.residency == "on-demand":
                lines.append(f"sleep-idle-seconds = {model.idle_minutes * 60}")
            lines += [f"{flag.lstrip('-')} = true" for flag in model.model_flags]
            lines.append("")
        return "\n".join(lines)


def load_local_presets(directory: Path = PRESETS_DIR) -> dict[str, dict[str, Any]]:
    """Every model preset with a local route, by id.

    The server validated these against their schema before it would start, and
    they are files in this same package; the checks below are the ones that
    decide what is executed, repeated here rather than trusted.
    """

    presets: dict[str, dict[str, Any]] = {}
    for file in sorted((directory / "models").glob("*.json")):
        preset = json.loads(file.read_text(encoding="utf-8"))
        if isinstance(preset.get("local"), dict):
            presets[file.stem] = preset
    return presets


def parse_plan(document: Any, presets: dict[str, dict[str, Any]], models_dir: Path) -> Plan:
    """The plan `document` asks for, or `PlanError` naming what is wrong."""

    if not isinstance(document, dict):
        raise PlanError("the runtime document is not an object")
    unknown = set(document) - {"chat"}
    if unknown:
        raise PlanError(f"unknown fields: {', '.join(sorted(unknown))}")

    chat: list[Chat] = []
    names: set[str] = set()
    for item in _list(document.get("chat"), "chat"):
        name = item.get("name")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise PlanError(f"chat model name {name!r} is not a catalog name")
        if name in names:
            raise PlanError(f"chat model `{name}` is named twice")
        names.add(name)
        preset_id, local, policy = _preset(item, presets)
        files = _files(local, models_dir)
        chat.append(
            Chat(
                name=name,
                preset=preset_id,
                model=files["model"],
                mmproj=files.get("mmproj"),
                model_flags=tuple(local["facts"].get("model_flags", ())),
                context_size=_int(policy, "context_size", 512, 1 << 21),
                gpu_layers=_int(policy, "gpu_layers", 0, 999, default=99),
                speculation=_enum(policy, "speculation", ("none", "draft-mtp"), "none"),
                kv_cache=_enum(policy, "kv_cache", ("f16", "q8_0"), "f16"),
                residency=_enum(policy, "residency", ("always", "on-demand"), "on-demand"),
                idle_minutes=_int(policy, "idle_minutes", 1, 24 * 60, default=15),
            )
        )

    return Plan(chat=tuple(chat))


_CHAT_FIELDS = {
    "name",
    "preset",
    "context_size",
    "gpu_layers",
    "speculation",
    "kv_cache",
    "residency",
    "idle_minutes",
}


def _list(value: Any, what: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise PlanError(f"`{what}` is not a list of objects")
    return value


def _preset(
    item: dict[str, Any], presets: dict[str, dict[str, Any]]
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """The preset `item` names, and `item` as policy."""

    extra = set(item) - _CHAT_FIELDS
    if extra:
        # Not ignored: an unknown key is either a server newer than this host or a
        # flag somebody hoped would reach the command line.
        raise PlanError(f"the chat entry has fields this host does not take: {sorted(extra)}")
    preset_id = item.get("preset")
    preset = presets.get(preset_id) if isinstance(preset_id, str) else None
    if preset is None:
        raise PlanError(f"no shipped preset `{preset_id}` runs on this machine")
    return preset_id, preset["local"], item


def _files(local: dict[str, Any], models_dir: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for file in local["facts"]["weights"]["files"]:
        name = file["name"]
        # The schema already forbids a separator; a second check costs nothing
        # beside a path handed to a program holding the GPU.
        if "/" in name or name in {".", ".."}:
            raise PlanError(f"weights file {name!r} would leave modelsDir")
        # The first `model` file only: a split GGUF is started from its first
        # shard and llama.cpp opens the rest beside it by their numbered names.
        files.setdefault(file["role"], models_dir / name)
    if "model" not in files:
        raise PlanError("the preset names no model file")
    return files


def _int(
    policy: dict[str, Any], key: str, low: int, high: int, *, default: int | None = None
) -> int:
    value = policy.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise PlanError(f"`{key}` must be a whole number from {low} to {high}")
    return value


def _enum(policy: dict[str, Any], key: str, allowed: tuple[str, ...], default: str) -> str:
    value = policy.get(key, default)
    if value not in allowed:
        raise PlanError(f"`{key}` must be one of {', '.join(allowed)}")
    return str(value)
