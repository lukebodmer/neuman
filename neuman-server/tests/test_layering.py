"""The layering is a checked rule, not a convention (docs/decisions.md D7).

It is enforced mechanically rather than by convention, because "a boundary only a
convention protects will rot". This is that enforcement.

It is a small AST walk rather than `import-linter` because that package is not
in nixpkgs, and the rules below are specific enough that a purpose-built check
is shorter than the configuration would be — the same reasoning that produced
our own schema codegen instead of fighting `quicktype`.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "neuman_server"

# What each layer may import, by layer name. Dependency points inward.
ALLOWED: dict[str, frozenset[str]] = {
    "foundation": frozenset(),
    "decision": frozenset({"foundation"}),
    "adapters": frozenset({"decision", "foundation"}),
    "composition": frozenset({"adapters", "decision", "foundation"}),
}

# Third-party packages the decision layer must never learn about. Adapters
# exist precisely so it cannot.
FORBIDDEN_IN_DECISION = frozenset({"psycopg", "starlette", "uvicorn", "httpx", "psycopg_pool"})

# Decision domains are independent of each other, with two stated exceptions.
DOMAINS = frozenset({"policy", "harness", "audit", "identity"})


def _modules() -> Iterator[tuple[Path, ast.Module]]:
    for path in sorted(SRC.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _layer_of(path: Path) -> str | None:
    parts = path.relative_to(SRC).parts
    return parts[0] if parts and parts[0] in ALLOWED else None


def _domain_of(path: Path) -> str | None:
    parts = path.relative_to(SRC).parts
    if len(parts) >= 2 and parts[0] == "decision" and parts[1] in DOMAINS:
        return parts[1]
    return None


def _imported_targets(path: Path, tree: ast.Module) -> Iterator[str]:
    """Yield each import as a dotted path rooted at `neuman_server`.

    Relative imports are resolved against the importing module's package, so
    `from ...foundation.state import x` inside `decision/policy/ops.py` yields
    `foundation.state`.
    """

    package = path.relative_to(SRC).parts[:-1]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    yield node.module
                continue
            base = list(package[: len(package) - (node.level - 1)])
            if node.module:
                base.extend(node.module.split("."))
            yield ".".join(base)


def test_layers_point_inward():
    """foundation → nothing; decision → foundation; adapters → decision; composition → all."""

    violations: list[str] = []

    for path, tree in _modules():
        layer = _layer_of(path)
        if layer is None:
            continue
        for target in _imported_targets(path, tree):
            head = target.split(".")[0]
            if head in ALLOWED and head != layer and head not in ALLOWED[layer]:
                violations.append(
                    f"{path.relative_to(SRC)}: {layer} may not import {head} ({target})"
                )

    assert not violations, "layering violations:\n" + "\n".join(violations)


def test_decision_knows_no_transport():
    """A provider API or a socket library in the decision path defeats the ports."""

    violations: list[str] = []

    for path, tree in _modules():
        if _layer_of(path) != "decision":
            continue
        for target in _imported_targets(path, tree):
            if target.split(".")[0] in FORBIDDEN_IN_DECISION:
                violations.append(f"{path.relative_to(SRC)}: imports {target}")

    assert not violations, "transport leaked into decision:\n" + "\n".join(violations)


def test_decision_domains_stay_independent():
    """A domain reaching into another's internals stops being readable alone.

    Three intended couplings are exempt, each named rather than waived:

    * every domain emits governance events, so `audit.chain` and `audit.ports`
      are shared vocabulary rather than a peer domain reached sideways (D8);
    * the harness gates every dispatch against live policy and derives the
      confinement profile from it — a harness that could not read policy could
      not be governed by it;
    * policy reads `identity.directory`, because "identity is policy"
      (docs/decisions.md D16): grants are keyed to groups. The dependency runs one way only —
      identity never imports policy — which is why the port lives in identity.
    """

    violations: list[str] = []

    for path, tree in _modules():
        domain = _domain_of(path)
        if domain is None:
            continue

        for target in _imported_targets(path, tree):
            parts = target.split(".")
            if len(parts) < 2 or parts[0] != "decision" or parts[1] not in DOMAINS:
                continue
            other = parts[1]
            if other == domain:
                continue

            if other == "audit" and (len(parts) < 3 or parts[2] in {"chain", "ports"}):
                continue
            if domain == "harness" and other == "policy":
                continue
            if (
                domain == "policy"
                and other == "identity"
                and parts[2] in {"authz", "directory"}
            ):
                continue

            violations.append(f"{path.relative_to(SRC)}: {domain} imports {target}")

    assert not violations, "cross-domain coupling:\n" + "\n".join(violations)


def test_identity_does_not_depend_on_the_harness():
    """Who you are cannot depend on what you are running."""

    violations: list[str] = []

    for path, tree in _modules():
        if _domain_of(path) != "identity":
            continue
        for target in _imported_targets(path, tree):
            if target.startswith("decision.harness"):
                violations.append(f"{path.relative_to(SRC)}: imports {target}")

    assert not violations, "\n".join(violations)


# The process entry point. It is *above* composition rather than beside it —
# its whole job is to build the app and hand it to an ASGI server — so it is
# the one file outside `composition/` that may import it.
#
# Named here rather than waived, like every other intended coupling in this
# file. The rule it is exempt from exists so that a *layer* cannot reach
# sideways into wiring; a `__main__` that cannot call `create_app` could not
# start the program at all.
ENTRY_POINT = "__main__.py"


def test_nothing_imports_composition():
    violations: list[str] = []

    for path, tree in _modules():
        if _layer_of(path) == "composition":
            continue
        if path.name == ENTRY_POINT and path.parent == SRC:
            continue
        for target in _imported_targets(path, tree):
            if target.startswith("composition"):
                violations.append(f"{path.relative_to(SRC)}: imports {target}")

    assert not violations, "\n".join(violations)


def test_the_entry_point_only_wires():
    """`__main__` may build the app; it may not make decisions.

    The exemption above is narrow on purpose. An entry point that reached into
    `decision` or `adapters` would be a second composition root, and the one in
    `composition/app.py` would stop being the single place startup ordering is
    decided.
    """

    entry = SRC / ENTRY_POINT
    if not entry.is_file():
        return

    import ast as _ast

    tree = _ast.parse(entry.read_text(encoding="utf-8"), filename=str(entry))
    reached = {
        target.split(".")[0]
        for target in _imported_targets(entry, tree)
        if target.split(".")[0] in ALLOWED
    }
    assert reached <= {"composition", "foundation"}, (
        f"the entry point reaches {sorted(reached)}; it may only wire "
        "(composition) and read settings (foundation)"
    )


@pytest.mark.parametrize("layer", sorted(ALLOWED))
def test_every_layer_has_a_readme(layer):
    """Each part directory states its purpose and its surfaces (docs/decisions.md (D7))."""

    assert (SRC / layer / "README.md").is_file()
