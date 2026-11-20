"""Tool-manifest and tool-package loader and validator.

One manifest contract, two installations: the server's registry (here) and the
client's own set, loaded from its own store path. The client's suite loads
`tools/` and asserts it accepts the same manifests (docs/decisions.md D2, D3). TOML is only the authoring syntax — the decoded
object is validated against ``contracts/schemas/tool-manifest.schema.json``,
which is the actual contract.

This closes v1's two open manifest problems:

* **One validator, and it is the schema.** v1 validated with a Pydantic model
  because the JSON Schema "would
  enter the codegen pipeline, which expects JSON-Schema-first, not TOML", and
  kept ``needs``/``parameters`` as undeclared extensions. Both are now declared
  fields of the shared schema and validated against it.
* **N4** — the schema types ``parameters`` as a bare object with a prose note
  that it is a draft 2020-12 schema. Nothing checked that it *is* one, so a
  malformed ``parameters`` would surface only when advertised to the model, or
  be silently permissive. :func:`load_registry` runs ``check_schema`` on every
  manifest's parameters.

Packages (D101) load from `packages/` beside the manifests, against
``contracts/schemas/tool-package.schema.json``. The loader returns the whole
catalog; `Registry.installed()` is the view of what an appliance has enabled,
which is the one everything that gates and advertises reads.

Loading fails closed: one bad manifest fails the whole registry rather than
yielding a partial tool set. A silently-missing tool is a confusing outage; a
silently-*present* malformed one is worse.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..foundation.logging import get_logger

__all__ = [
    "InstalledRegistry",
    "Registry",
    "RegistryError",
    "ToolManifest",
    "ToolPackage",
    "load_registry",
]

_log = get_logger("neuman.server.adapters.registry")

# Where a tool runs (D13). `client` is the acting human's own machine and needs
# an `exec`; `server-native` is answered by the decision core and never
# dispatched. Execution no longer has a third place (SPEC section 2).
KINDS = ("client", "server-native")


class RegistryError(RuntimeError):
    """A manifest could not be read or validated."""


@dataclass(frozen=True, slots=True)
class ToolManifest:
    """Implements `decision.policy.ports.ToolManifest`."""

    name: str
    kind: str
    description: str
    wait_ms: int
    lifetime_ms: int
    max_output_bytes: int
    mutating: bool
    critical: bool
    needs: frozenset[str]
    parameters: dict[str, Any]
    exec: str | None = None
    # A short line for a person, where `description` is instructions for the
    # model. Optional: a tool without one falls back to its description,
    # truncated by the client.
    summary: str | None = None
    # Appliance test scaffolding rather than a user's capability. Presentation
    # only: the client omits it from the tool chooser. The gate is unaffected.
    diagnostic: bool = False
    # Answered by the client daemon with no process (`job_output`, `job_kill`).
    daemon: bool = False
    # Stdout is one picture the core lifts into an image for the model (D28).
    returns_image: bool = False

    @property
    def detaches(self) -> bool:
        """Whether a call can outlive its wait as a background job (D98)."""

        return self.lifetime_ms > self.wait_ms

    @classmethod
    def from_contract(cls, data: dict[str, Any]) -> ToolManifest:
        """Build from a schema-valid ``tool`` object."""

        return cls(
            name=data["name"],
            kind=data["kind"],
            description=data["description"],
            wait_ms=int(data["wait_ms"]),
            lifetime_ms=int(data["lifetime_ms"]),
            max_output_bytes=int(data["max_output_bytes"]),
            mutating=bool(data["mutating"]),
            critical=bool(data["critical"]),
            needs=frozenset(data["needs"]),
            parameters=dict(data["parameters"]),
            exec=data.get("exec"),
            summary=data.get("summary"),
            diagnostic=bool(data.get("diagnostic", False)),
            daemon=bool(data.get("daemon", False)),
            returns_image=bool(data.get("returns_image", False)),
        )


@dataclass(frozen=True, slots=True)
class ToolPackage:
    """A set of tools an operator enables together (D101).

    Part of the release, like every manifest: enabling one changes which of the
    shipped tools are present, and never adds code to the server or a client.
    """

    id: str
    title: str
    summary: str
    description: str
    publisher: str
    tools: tuple[str, ...]
    # A program the user installs themselves: {"program", "name", "url"}.
    requires: dict[str, str] | None = None
    # A companion the client starts on demand: {"exec"}.
    host: dict[str, str] | None = None

    @classmethod
    def from_contract(cls, data: dict[str, Any]) -> ToolPackage:
        return cls(
            id=data["id"],
            title=data["title"],
            summary=data["summary"],
            description=data["description"],
            publisher=data["publisher"],
            tools=tuple(data["tools"]),
            requires=dict(data["requires"]) if "requires" in data else None,
            host=dict(data["host"]) if "host" in data else None,
        )


class Registry:
    """Every tool and package the release ships: the catalog.

    What the administration page offers. The tools an appliance actually has are
    `installed()`, which is what everything that gates, advertises or
    dispatches reads.
    """

    __slots__ = ("_order", "_package_of", "_packages", "_tools")

    def __init__(
        self,
        tools: dict[str, ToolManifest],
        packages: dict[str, ToolPackage] | None = None,
    ) -> None:
        self._tools = tools
        self._order = tuple(sorted(tools))
        self._packages = dict(packages or {})
        self._package_of = {
            tool: package.id for package in self._packages.values() for tool in package.tools
        }

    def get(self, name: str) -> ToolManifest | None:
        return self._tools.get(name)

    def names(self) -> Sequence[str]:
        return self._order

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def packages(self) -> Sequence[ToolPackage]:
        return tuple(self._packages[key] for key in sorted(self._packages))

    def package(self, package_id: str) -> ToolPackage | None:
        return self._packages.get(package_id)

    def package_of(self, name: str) -> str | None:
        """The package holding `name`, or None for a built-in tool."""

        return self._package_of.get(name)

    def installed(self, enabled: Callable[[], Iterable[str]]) -> InstalledRegistry:
        return InstalledRegistry(self, enabled)


class InstalledRegistry:
    """Implements `decision.policy.ports.ToolRegistry`: the tools present now.

    Built-in tools, and the tools of every package `enabled()` names. `enabled`
    is called on every read rather than once, because it reads the live
    baseline: a package disabled in the panel is gone from the next step's gate
    and advertisement, the way a revoked grant is (docs/security-model.md), rather
    than from the next turn.
    """

    __slots__ = ("_catalog", "_enabled")

    def __init__(self, catalog: Registry, enabled: Callable[[], Iterable[str]]) -> None:
        self._catalog = catalog
        self._enabled = enabled

    @property
    def catalog(self) -> Registry:
        return self._catalog

    def _present(self, name: str) -> bool:
        package = self._catalog.package_of(name)
        return package is None or package in set(self._enabled())

    def get(self, name: str) -> ToolManifest | None:
        manifest = self._catalog.get(name)
        return manifest if manifest is not None and self._present(name) else None

    def names(self) -> Sequence[str]:
        enabled = set(self._enabled())
        return tuple(
            name
            for name in self._catalog.names()
            if (package := self._catalog.package_of(name)) is None or package in enabled
        )

    def withheld(self, name: str) -> str | None:
        """The package `name` belongs to when that package is not enabled.

        So the gate can say *why* a tool is absent — an operator acts on "the
        Blender package is not enabled", and not on "no manifest".
        """

        package = self._catalog.package_of(name)
        if package is None or package in set(self._enabled()):
            return None
        return package

    def __len__(self) -> int:
        return len(self.names())

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.get(name) is not None


def _validate_against_contract(document: dict[str, Any], path: Path, kind: str) -> None:
    """Validate a decoded manifest or package against its shared schema."""

    try:
        from neuman_contracts import tool_manifest, tool_package
    except ImportError:  # pragma: no cover - contracts always present in prod
        _log.warning("contracts package unavailable; manifests not schema-checked")
        return

    from jsonschema import ValidationError

    contract = tool_package if kind == "package" else tool_manifest
    try:
        contract.validate(document)
    except ValidationError as exc:
        raise RegistryError(f"{path}: {kind} violates its schema: {exc.message}") from exc


def _validate_parameters(manifest: ToolManifest, path: Path) -> None:
    """Confirm ``parameters`` is itself a usable JSON Schema (gotchas N4)."""

    if not manifest.parameters:
        # `{}` is explicitly allowed by the contract: a tool that takes no
        # arguments.
        return

    try:
        from jsonschema import Draft202012Validator, SchemaError
    except ImportError:  # pragma: no cover
        return

    try:
        Draft202012Validator.check_schema(manifest.parameters)
    except SchemaError as exc:
        raise RegistryError(
            f"{path}: tool {manifest.name!r} has a `parameters` block that is not a "
            f"valid draft 2020-12 schema: {exc.message}"
        ) from exc


def load_registry(directory: str | Path) -> Registry:
    """Load every ``<directory>/<name>/manifest.toml`` and ``<directory>/packages/*.toml``."""

    base = Path(directory)
    if not base.is_dir():
        raise RegistryError(f"registry directory not found: {base}")

    tools: dict[str, ToolManifest] = {}

    for path in sorted(base.glob("*/manifest.toml")):
        try:
            document = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise RegistryError(f"{path}: cannot read manifest: {exc}") from exc

        _validate_against_contract(document, path, "manifest")
        manifest = ToolManifest.from_contract(document["tool"])

        # The contract cannot express "name equals the directory it lives in",
        # but the client resolves tools by directory, so a mismatch would mean
        # the two installations disagree about what a name refers to (D3).
        if manifest.name != path.parent.name:
            raise RegistryError(
                f"{path}: tool name {manifest.name!r} does not match its directory "
                f"{path.parent.name!r}"
            )
        if manifest.name in tools:
            raise RegistryError(f"{path}: duplicate tool {manifest.name!r}")
        # Also beyond the schema: two fields compared. A lifetime shorter than
        # the wait would kill every call before the wait it promised.
        if manifest.lifetime_ms < manifest.wait_ms:
            raise RegistryError(
                f"{path}: tool {manifest.name!r} has lifetime_ms below its wait_ms"
            )

        _validate_parameters(manifest, path)
        tools[manifest.name] = manifest

    if not tools:
        raise RegistryError(f"no tool manifests under {base}")

    packages = _load_packages(base / "packages", tools)

    _log.info(
        "tool registry loaded",
        fields={"count": len(tools), "packages": len(packages), "directory": str(base)},
    )
    return Registry(tools, packages)


def _load_packages(directory: Path, tools: dict[str, ToolManifest]) -> dict[str, ToolPackage]:
    """Load every `packages/<id>.toml`, failing the registry on any inconsistency.

    Beyond the schema, three cross-file rules: the id is the file's name, every
    tool a package names has a manifest, and no tool is in two packages — a tool
    in two would be present while either is enabled, which is neither package's
    statement about it.
    """

    packages: dict[str, ToolPackage] = {}
    owner: dict[str, str] = {}
    for path in sorted(directory.glob("*.toml")):
        try:
            document = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise RegistryError(f"{path}: cannot read package: {exc}") from exc
        _validate_against_contract(document, path, "package")
        package = ToolPackage.from_contract(document["package"])
        if package.id != path.stem:
            raise RegistryError(
                f"{path}: package id {package.id!r} does not match its file {path.stem!r}"
            )
        for tool in package.tools:
            if tool not in tools:
                raise RegistryError(f"{path}: package names {tool!r}, which has no manifest")
            if tool in owner:
                raise RegistryError(
                    f"{path}: {tool!r} is already in the {owner[tool]!r} package"
                )
            owner[tool] = package.id
        packages[package.id] = package
    return packages
