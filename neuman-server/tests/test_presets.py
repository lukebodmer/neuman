"""The shipped presets, what they mean for an entry, and the key files (D76, D77).

The preset tests read the real files under `neuman_server/presets/`. A preset is
a statement about a provider's model that every appliance running this release
will be held to, so the files themselves are what needs proving — a fixture
written beside the test would only prove the fixture.
"""

from __future__ import annotations

import copy
import json
import stat

import pytest

from neuman_server.adapters.weights import weights_state
from neuman_server.decision.harness.presets import (
    build_entry,
    catalog_problems,
    local_facts,
    local_policy,
    preset_problems,
    refresh_facts,
    route_facts,
)
from neuman_server.foundation import Settings
from neuman_server.foundation.state import (
    PresetError,
    SecretError,
    load_presets,
    sweep_secrets,
    write_secret,
)

PRESETS = load_presets()


def _document(
    *, provider_preset: str = "deepseek", model_preset: str = "deepseek-v4_1-flash-1017"
):
    """A baseline holding one provider and one model, both added from presets."""

    url = PRESETS["providers"][provider_preset]["url"]
    return {
        "schema_version": 2,
        "defaults": {},
        "roles": {},
        "users": [],
        "providers": {
            "p": {"url": url, "api_key_file": "/keys/p.key", "preset": provider_preset}
        },
        "models": {"m": build_entry(PRESETS, model_preset, provider_preset, "p")},
    }


# -- the shipped files --------------------------------------------------------


def test_the_shipped_presets_load_and_agree_with_themselves():
    """Every route is a shipped provider and every default is one of its levels."""

    assert PRESETS["providers"], "no provider presets shipped"
    assert PRESETS["models"], "no model presets shipped"
    assert preset_problems(PRESETS) == []


def test_every_route_of_every_preset_builds_an_entry_the_server_accepts():
    """Driven from the files, so a preset added tomorrow is covered today.

    Instantiate each model on every route it claims and check the result is
    complete, where "complete" is the rule a write is held to plus a valid
    baseline document. `runtimes-eval` does the same for the local presets.
    """

    from neuman_contracts import baseline as contract

    checked = 0
    for model_id, model in PRESETS["models"].items():
        for route in model.get("routes") or {}:
            document = _document(provider_preset=route, model_preset=model_id)
            contract.validate(document)
            assert catalog_problems(document, PRESETS) == [], (model_id, route)
            entry = document["models"]["m"]
            assert entry["provider_model"], (model_id, route)
            assert entry.get("context_tokens", 0) > 0, f"{model_id} on {route} has no window"
            checked += 1
    assert checked >= 4, f"only {checked} routes checked"


def test_a_route_sends_its_own_id_not_the_origins():
    """The bug the hosting split exists for: DeepSeek's id sent to OpenRouter is a 400."""

    direct = build_entry(PRESETS, "deepseek-v4_1-flash-1017", "deepseek", "d")
    routed = build_entry(PRESETS, "deepseek-v4_1-flash-1017", "openrouter", "o")

    assert direct["provider_model"] == "deepseek-flash"
    assert routed["provider_model"] == "deepseek/deepseek-v4.1-flash-1017"
    # The window is the model's, so it is the same either way.
    assert direct["context_tokens"] == routed["context_tokens"] == 1048576
    # The prices are the route's.
    assert direct["input_cost_per_m"] != routed["input_cost_per_m"]


def test_an_entry_records_its_preset_and_the_route_default():
    entry = build_entry(PRESETS, "deepseek-v4_1-flash-1017", "deepseek", "deepseek")

    assert entry["preset"] == "deepseek-v4_1-flash-1017"
    assert entry["provider"] == "deepseek"
    assert entry["reasoning"] == {
        "control": "budget",
        "levels": ["off", "low", "high", "max"],
        "default": "high",
    }


def test_a_model_with_no_route_at_a_provider_cannot_be_built():
    assert route_facts(PRESETS, "glm-5_3-flash", "deepseek") is None
    with pytest.raises(KeyError):
        build_entry(PRESETS, "glm-5_3-flash", "deepseek", "p")


def test_a_malformed_preset_file_stops_the_release(tmp_path):
    (tmp_path / "providers").mkdir()
    (tmp_path / "models").mkdir()
    (tmp_path / "providers" / "broken.json").write_text("{", encoding="utf-8")

    with pytest.raises(PresetError, match=r"broken\.json"):
        load_presets(tmp_path)


def test_a_preset_violating_its_schema_is_refused_with_where(tmp_path):
    (tmp_path / "providers").mkdir()
    (tmp_path / "models").mkdir()
    (tmp_path / "providers" / "plain.json").write_text(
        json.dumps({"label": "Plain", "url": "http://plain.test/v1"}), encoding="utf-8"
    )

    with pytest.raises(PresetError, match=r"providers/plain/url"):
        load_presets(tmp_path)


def test_a_default_outside_the_routes_levels_is_a_preset_problem():
    presets = copy.deepcopy(PRESETS)
    presets["models"]["deepseek-v4_1-flash-1017"]["policy"]["reasoning_default"] = "medium"

    problems = preset_problems(presets)
    assert any("medium" in problem for problem in problems), problems


# -- models on this machine ---------------------------------------------------


def test_every_local_route_is_pinned_and_complete():
    """Driven from the files: a pin to a commit, one model file, a window."""

    local = {
        model_id: local_facts(PRESETS, model_id)
        for model_id in PRESETS["models"]
        if local_facts(PRESETS, model_id) is not None
    }
    assert "qwen3_8-27b" in local
    for model_id, facts in local.items():
        weights = facts["weights"]
        assert len(weights["revision"]) == 40, model_id
        assert all(len(file["sha256"]) == 64 for file in weights["files"]), model_id
        assert local_policy(PRESETS, model_id)["context_size"] > 0, model_id


def test_a_local_model_sees_because_it_has_a_projector():
    """Vision is derived from the files, never stated beside them."""

    assert local_facts(PRESETS, "qwen3_8-27b")["vision"] is True


def test_a_local_route_inherits_the_models_reasoning():
    facts = local_facts(PRESETS, "qwen3_8-27b")
    policy = local_policy(PRESETS, "qwen3_8-27b")

    assert facts["reasoning"]["levels"] == ["none", "low", "medium", "xhigh"]
    assert policy["reasoning_default"] == "medium"


def test_a_local_route_speaks_its_templates_dialect_not_the_hosted_one():
    """DeepSeek's API gates reasoning on a `thinking` envelope; its chat template
    under llama.cpp reads `reasoning_effort` alone, and `none` is its off."""

    hosted = route_facts(PRESETS, "deepseek-v4_1-flash-1017", "deepseek")
    local = local_facts(PRESETS, "deepseek-v4_1-flash-1017")

    assert hosted["reasoning"]["control"] == "budget"
    assert local["reasoning"] == {"control": "effort", "levels": ["none", "low", "high", "max"]}
    assert local_policy(PRESETS, "deepseek-v4_1-flash-1017")["reasoning_default"] == "high"


def test_the_local_and_hosted_entries_of_one_model_take_different_names():
    model = PRESETS["models"]["qwen3_8-27b"]
    assert model["local"]["name"] != model["name"]


@pytest.mark.parametrize(
    ("model_id", "edit", "named"),
    [
        # A model without a window is managed against a guess (N86).
        (
            "qwen3_8-27b",
            lambda m: m["local"]["policy"].pop("context_size"),
            "no `context_size`",
        ),
        (
            "qwen3_8-27b",
            lambda m: m["local"]["policy"].update(reasoning_default="high"),
            "`high` is not one of its levels",
        ),
        # Two entries under one name: one would answer as the other.
        ("qwen3_8-27b", lambda m: m["local"].update(name=m["name"]), "which is taken"),
        # The runtime is handed the first shard and finds the rest by name, so
        # a set out of order or one short starts the wrong file or fails.
        (
            "glm-5_3",
            lambda m: m["local"]["facts"]["weights"]["files"].reverse(),
            "not one split GGUF",
        ),
        (
            "glm-5_3",
            lambda m: m["local"]["facts"]["weights"]["files"].pop(),
            "not one split GGUF",
        ),
        # A flat modelsDir: a shared name would be one preset's download
        # refused as the other's wrong file, and deleted with it.
        (
            "glm-5_3-flash",
            lambda m: m["local"]["facts"]["weights"]["files"][-1].update(
                name="mmproj-F16.gguf"
            ),
            "`mmproj-F16.gguf`, which",
        ),
    ],
)
def test_a_local_route_that_would_start_the_wrong_thing_is_a_preset_problem(
    model_id, edit, named
):
    presets = copy.deepcopy(PRESETS)
    edit(presets["models"][model_id])

    problems = preset_problems(presets)
    assert any(named in problem for problem in problems), problems


def test_weights_are_present_by_name_and_size(tmp_path):
    files = [
        {"name": "a.gguf", "size": 3},
        {"name": "b.gguf", "size": 2},
    ]
    assert weights_state(tmp_path, files)["state"] == "absent"

    (tmp_path / "a.gguf").write_bytes(b"abc")
    (tmp_path / "b.gguf.part").write_bytes(b"x")
    state = weights_state(tmp_path, files)
    assert state["state"] == "partial"
    assert [file["state"] for file in state["files"]] == ["present", "partial"]

    (tmp_path / "b.gguf").write_bytes(b"xy")
    assert weights_state(tmp_path, files)["state"] == "present"
    assert weights_state(tmp_path, files)["bytes"] == 5

    # Somebody else's download under the name a runtime would open.
    (tmp_path / "b.gguf").write_bytes(b"xyz")
    assert weights_state(tmp_path, files)["state"] == "wrong"


# -- holding an entry to its preset -------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "named"),
    [
        ("context_tokens", 262144, "context_tokens"),
        ("vision", False, "vision"),
        ("provider_model", "deepseek-chat", "provider_model"),
    ],
)
def test_a_write_contradicting_a_fact_is_refused_by_name(field, value, named):
    document = _document()
    document["models"]["m"][field] = value

    problems = catalog_problems(document, PRESETS)
    assert len(problems) == 1, problems
    assert problems[0].startswith(f"model `m`: {named} is"), problems


def test_a_route_level_list_is_a_fact():
    """A level the route does not forward is a 400 mid-turn; adding one is refused."""

    document = _document()
    document["models"]["m"]["reasoning"]["levels"].append("medium")

    problems = catalog_problems(document, PRESETS)
    assert any("reasoning.levels" in problem for problem in problems), problems


def test_policy_on_a_preset_entry_is_the_operators():
    """Reply length, prices and the default level are where an entry starts."""

    document = _document()
    entry = document["models"]["m"]
    entry["max_tokens"] = 8192
    entry["input_cost_per_m"] = 9.0
    entry["reasoning"]["default"] = "low"

    assert catalog_problems(document, PRESETS) == []


def test_a_reasoning_default_outside_the_levels_is_refused_for_any_model():
    document = _document()
    document["models"]["hand"] = {
        "provider": "p",
        "provider_model": "x",
        "reasoning": {"control": "effort", "levels": ["low", "high"], "default": "max"},
    }

    problems = catalog_problems(document, PRESETS)
    assert problems == ["model `hand`: reasoning default `max` is not one of its levels"]


def test_a_model_naming_an_undeclared_provider_is_refused():
    document = _document()
    document["models"]["m"]["provider"] = "gone"

    assert catalog_problems(document, PRESETS) == [
        "model `m` names provider `gone`, which is not declared"
    ]


def test_a_preset_model_on_a_provider_it_has_no_route_at_is_refused():
    document = _document(provider_preset="openrouter", model_preset="glm-5_3-flash")
    document["providers"]["p"] = {
        "url": PRESETS["providers"]["deepseek"]["url"],
        "api_key_file": None,
        "preset": "deepseek",
    }

    problems = catalog_problems(document, PRESETS)
    assert problems and "is served by openrouter" in problems[0], problems


def test_a_preset_providers_url_is_a_fact():
    document = _document()
    document["providers"]["p"]["url"] = "https://elsewhere.test/v1"

    problems = catalog_problems(document, PRESETS)
    assert problems == [
        "provider `p`: url is 'https://elsewhere.test/v1', but the preset states "
        "'https://api.deepseek.com/v1'"
    ]


def test_an_unknown_preset_is_refused():
    document = _document()
    document["models"]["m"]["preset"] = "imaginary"

    assert catalog_problems(document, PRESETS) == [
        "model `m` names preset `imaginary`, which Neuman does not ship"
    ]


def test_a_hand_declared_entry_carries_its_own_claims():
    """No preset, no check beyond resolving: the exposure D39 describes, kept available."""

    document = _document()
    document["models"]["hand"] = {
        "provider": "p",
        "provider_model": "anything",
        "context_tokens": 7,
        "vision": True,
    }

    assert catalog_problems(document, PRESETS) == []


# -- re-applying facts at startup -----------------------------------------------


def test_a_corrected_fact_reaches_an_existing_entry_and_policy_survives():
    document = _document()
    entry = document["models"]["m"]
    entry["context_tokens"] = 131072  # what an older release said
    entry["max_tokens"] = 4096  # the operator's choice

    refreshed, changes = refresh_facts(document, PRESETS)

    assert refreshed["models"]["m"]["context_tokens"] == 1048576
    assert refreshed["models"]["m"]["max_tokens"] == 4096
    assert changes == ["model `m`: facts updated from preset `deepseek-v4_1-flash-1017`"]
    # The input is not modified: the caller decides whether to store the result.
    assert document["models"]["m"]["context_tokens"] == 131072


def test_a_default_the_route_no_longer_accepts_falls_back_to_the_presets():
    document = _document()
    document["models"]["m"]["reasoning"] = {
        "control": "budget",
        "levels": ["off", "medium"],
        "default": "medium",
    }

    refreshed, _ = refresh_facts(document, PRESETS)

    assert refreshed["models"]["m"]["reasoning"]["default"] == "high"
    assert catalog_problems(refreshed, PRESETS) == []


def test_an_operators_default_survives_a_refresh_when_still_offered():
    document = _document()
    document["models"]["m"]["context_tokens"] = 1
    document["models"]["m"]["reasoning"]["default"] = "max"

    refreshed, _ = refresh_facts(document, PRESETS)

    assert refreshed["models"]["m"]["reasoning"]["default"] == "max"


def test_an_entry_whose_preset_is_gone_keeps_its_values_and_loses_the_record():
    document = _document()
    document["models"]["m"]["preset"] = "retired"

    refreshed, changes = refresh_facts(document, PRESETS)

    assert "preset" not in refreshed["models"]["m"]
    assert refreshed["models"]["m"]["provider_model"] == "deepseek-flash"
    assert changes == ["model `m`: preset `retired` is no longer shipped for its provider"]


def test_an_up_to_date_document_changes_nothing():
    refreshed, changes = refresh_facts(_document(), PRESETS)

    assert changes == []
    assert refreshed == _document()


# -- key files ------------------------------------------------------------------


def test_a_key_is_written_only_readable_by_the_server(tmp_path):
    path = write_secret(tmp_path / "secrets", "deepseek", "sk-abc123\n")

    assert path == tmp_path / "secrets" / "deepseek.key"
    assert path.read_text(encoding="utf-8") == "sk-abc123"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_a_replaced_key_leaves_no_temporary_beside_it(tmp_path):
    write_secret(tmp_path, "p", "first")
    write_secret(tmp_path, "p", "second")

    assert [file.name for file in tmp_path.iterdir()] == ["p.key"]
    assert (tmp_path / "p.key").read_text(encoding="utf-8") == "second"


@pytest.mark.parametrize(
    "value",
    ["", "   \n", "two words", "tab\there", "nul\x00byte", "x" * 4097],
)
def test_a_value_that_is_not_a_key_is_refused(tmp_path, value):
    with pytest.raises(SecretError):
        write_secret(tmp_path, "p", value)
    assert not (tmp_path / "p.key").exists()


@pytest.mark.parametrize("name", ["../escape", "a/b", ".", "..", "", ".hidden"])
def test_a_name_that_could_leave_the_directory_is_refused(tmp_path, name):
    with pytest.raises(SecretError):
        write_secret(tmp_path / "secrets", name, "sk-abc")


def test_a_sweep_removes_what_nothing_names(tmp_path):
    kept = write_secret(tmp_path, "kept", "sk-1")
    write_secret(tmp_path, "orphan", "sk-2")

    assert sweep_secrets(tmp_path, {str(kept)}) == ["orphan"]
    assert [file.name for file in tmp_path.iterdir()] == ["kept.key"]


def test_the_secrets_directory_is_made_private_even_if_it_existed(tmp_path):
    settings = Settings(
        state_dir=tmp_path / "state", audit_mirror_path=tmp_path / "log" / "audit.jsonl"
    )
    settings.secrets_dir.mkdir(parents=True, mode=0o755)
    settings.secrets_dir.chmod(0o755)

    settings.ensure_dirs()

    assert stat.S_IMODE(settings.secrets_dir.stat().st_mode) == 0o700
