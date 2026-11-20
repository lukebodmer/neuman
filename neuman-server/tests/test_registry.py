"""The tool registry: TOML in, contract-validated manifests out.

These exercise the real `contracts/schemas/tool-manifest.schema.json` through
the generated package, so a schema change that breaks the server fails here.
"""

from __future__ import annotations

import pytest

from neuman_server.adapters.registry import RegistryError, load_registry

VALID = """
[tool]
name = "fs_read"
kind = "client"
description = "Read a UTF-8 text file within your readable paths."
exec = "fs_read"
wait_ms = 5000
lifetime_ms = 5000
max_output_bytes = 262144
mutating = false
critical = false
needs = ["read"]

[tool.parameters]
type = "object"
required = ["path"]

[tool.parameters.properties.path]
type = "string"
"""


def _write(tmp_path, name: str, body: str):
    directory = tmp_path / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.toml").write_text(body)
    return directory


def test_a_summary_is_carried_and_is_optional(tmp_path):
    """The human label, beside the model's instructions (D50).

    Two fields because the audiences want opposite things: the model needs every
    caveat about what its environment lacks, and a person scanning the tool chooser
    needs a few words. A tool without one loads fine and the client clips the
    long text instead.
    """

    _write(tmp_path, "fs_read", VALID.replace("[tool]", '[tool]\nsummary = "Read a text file"'))
    assert load_registry(tmp_path).get("fs_read").summary == "Read a text file"

    _write(tmp_path, "fs_read", VALID)
    assert load_registry(tmp_path).get("fs_read").summary is None


def test_a_summary_that_is_a_paragraph_is_refused(tmp_path):
    """The cap is the point of the field.

    Without it the summary grows back into the description it exists to replace,
    and the chooser is a wall of prose again — which is the failure that
    prompted the field.
    """

    paragraph = "Run a shell command on your own machine, as you, " + "x" * 60
    _write(tmp_path, "fs_read", VALID.replace("[tool]", f'[tool]\nsummary = "{paragraph}"'))

    with pytest.raises(RegistryError):
        load_registry(tmp_path)


def test_a_valid_manifest_loads(tmp_path):
    _write(tmp_path, "fs_read", VALID)
    registry = load_registry(tmp_path)

    manifest = registry.get("fs_read")
    assert manifest is not None
    assert manifest.kind == "client"
    assert manifest.needs == frozenset({"read"})
    assert manifest.parameters["required"] == ["path"]


def test_an_empty_directory_fails_closed(tmp_path):
    with pytest.raises(RegistryError, match="no tool manifests"):
        load_registry(tmp_path)


def test_a_missing_directory_is_an_error(tmp_path):
    with pytest.raises(RegistryError, match="not found"):
        load_registry(tmp_path / "absent")


def test_the_name_must_match_the_directory(tmp_path):
    """Otherwise the server and the client disagree about what a name means."""

    _write(tmp_path, "something_else", VALID)
    with pytest.raises(RegistryError, match="does not match its directory"):
        load_registry(tmp_path)


def test_malformed_toml_is_reported(tmp_path):
    _write(tmp_path, "broken", "[tool\nname =")
    with pytest.raises(RegistryError, match="cannot read manifest"):
        load_registry(tmp_path)


def test_an_unknown_kind_is_rejected_by_the_contract(tmp_path):
    _write(tmp_path, "weird", VALID.replace('kind = "client"', 'kind = "magic"'))
    with pytest.raises(RegistryError, match="violates its schema"):
        load_registry(tmp_path)


def test_a_client_tool_without_exec_is_rejected(tmp_path):
    _write(tmp_path, "fs_read", VALID.replace('exec = "fs_read"\n', ""))
    with pytest.raises(RegistryError, match="violates its schema"):
        load_registry(tmp_path)


def test_a_native_tool_with_exec_is_rejected(tmp_path):
    """`kind` must be true of where the tool actually executes (docs/decisions.md D13)."""

    _write(
        tmp_path,
        "trash",
        VALID.replace('kind = "client"', 'kind = "server-native"').replace(
            'name = "fs_read"', 'name = "session_query"'
        ),
    )
    with pytest.raises(RegistryError, match="violates its schema"):
        load_registry(tmp_path)


def test_an_unknown_need_is_rejected(tmp_path):
    _write(tmp_path, "fs_read", VALID.replace('needs = ["read"]', 'needs = ["telepathy"]'))
    with pytest.raises(RegistryError, match="violates its schema"):
        load_registry(tmp_path)


def test_a_malformed_parameters_schema_is_rejected(tmp_path):
    """The contract types `parameters` as an object but never checked it was a
    usable JSON Schema; a bad one would surface only at advertisement time, or
    be silently permissive (gotchas N4)."""

    broken = VALID.replace('[tool.parameters]\ntype = "object"', "[tool.parameters]\ntype = 42")
    _write(tmp_path, "fs_read", broken)
    with pytest.raises(RegistryError):
        load_registry(tmp_path)


def test_empty_parameters_are_allowed(tmp_path):
    body = VALID.split("[tool.parameters]")[0] + "parameters = {}\n"
    _write(tmp_path, "fs_read", body)
    registry = load_registry(tmp_path)
    assert registry.get("fs_read").parameters == {}


def test_names_are_returned_in_a_stable_order(tmp_path):
    for name in ("zeta", "alpha"):
        _write(tmp_path, name, VALID.replace('name = "fs_read"', f'name = "{name}"'))
    assert list(load_registry(tmp_path).names()) == ["alpha", "zeta"]


# -- tool packages (D101) ------------------------------------------------------

PACKAGE = """
[package]
id = "reading"
title = "Reading"
summary = "Read files"
description = "One tool."
publisher = "neuman"
tools = ["fs_read"]
"""


def _package(tmp_path, name: str, body: str) -> None:
    directory = tmp_path / "packages"
    directory.mkdir(exist_ok=True)
    (directory / f"{name}.toml").write_text(body)


def test_a_package_tool_is_present_only_while_its_package_is_enabled(tmp_path):
    """The live view follows `enabled()` on every read, not once (docs/security-model.md).

    A package switched off in the panel must be gone from the next step's gate,
    the way a revoked grant is; a view that captured the set at construction
    would keep it until a restart.
    """

    _write(tmp_path, "fs_read", VALID)
    _write(tmp_path, "fs_list", VALID.replace('"fs_read"', '"fs_list"'))
    _package(tmp_path, "reading", PACKAGE)
    enabled: list[str] = []
    installed = load_registry(tmp_path).installed(lambda: enabled)

    assert list(installed.names()) == ["fs_list"]
    assert installed.get("fs_read") is None
    assert installed.withheld("fs_read") == "reading"
    assert installed.withheld("fs_list") is None

    enabled.append("reading")
    assert list(installed.names()) == ["fs_list", "fs_read"]
    assert installed.get("fs_read") is not None
    assert installed.withheld("fs_read") is None


def test_a_granted_tool_in_a_disabled_package_is_refused_by_name(tmp_path):
    """`package_not_enabled`, not `unknown_tool`: the remedy is the Tools page."""

    from neuman_server.decision.policy import gate
    from neuman_server.decision.policy.compose import EffectivePolicy

    _write(tmp_path, "fs_read", VALID)
    _package(tmp_path, "reading", PACKAGE)
    enabled: list[str] = []
    installed = load_registry(tmp_path).installed(lambda: enabled)
    policy = EffectivePolicy(tools=("fs_read",), files_read=("/srv",))

    decision = gate.check("fs_read", policy, installed)
    assert decision.reason == "package_not_enabled", decision
    assert gate.grantable(policy, installed) == []

    enabled.append("reading")
    assert gate.check("fs_read", policy, installed).allowed


@pytest.mark.parametrize(
    ("file", "body", "problem"),
    [
        ("other", PACKAGE, "does not match its file"),
        ("reading", PACKAGE.replace('["fs_read"]', '["fs_missing"]'), "has no manifest"),
        ("reading", PACKAGE.replace('"neuman"', '"acme"'), "violates its schema"),
    ],
)
def test_a_package_that_does_not_fit_the_tree_fails_the_registry(tmp_path, file, body, problem):
    """Fails closed as a whole, as a bad manifest does."""

    _write(tmp_path, "fs_read", VALID)
    _package(tmp_path, file, body)
    with pytest.raises(RegistryError, match=problem):
        load_registry(tmp_path)


def test_a_tool_in_two_packages_fails_the_registry(tmp_path):
    """It would be present while either is on, which neither package says."""

    _write(tmp_path, "fs_read", VALID)
    _package(tmp_path, "reading", PACKAGE)
    _package(tmp_path, "again", PACKAGE.replace('id = "reading"', 'id = "again"'))
    with pytest.raises(RegistryError, match="already in"):
        load_registry(tmp_path)
