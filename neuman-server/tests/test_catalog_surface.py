"""The preset routes, through the real app (D76, D77).

What these prove that `test_presets.py` cannot: that a key in a request body
ends up in a `0600` file and nowhere else — not in the response, the baseline,
the audit payload or the log — and that every route refuses a caller without
admin authority before it writes anything.
"""

from __future__ import annotations

import json
import stat

import pytest
from surface import ORIGIN, build_surface
from test_http_surface import as_peer, authenticate

KEY = "sk-test-0123456789abcdef"


@pytest.fixture
def surface(monkeypatch, tmp_path):
    return build_surface(monkeypatch, tmp_path)


@pytest.fixture
def client(surface):
    with surface.client() as running:
        yield running


@pytest.fixture
def operator(monkeypatch, surface, client):
    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")
    return client


def _secrets(surface):
    return surface.services.settings.secrets_dir


def test_the_catalog_offers_the_shipped_presets(operator):
    body = operator.get("/admin/catalog").json()

    providers = {tile["id"]: tile for tile in body["providers"]}
    assert providers["deepseek"]["url"] == "https://api.deepseek.com/v1"
    assert providers["deepseek"]["added"] == []
    models = {tile["id"]: tile for tile in body["models"]}
    routes = {route["provider"] for route in models["deepseek-v4_1-flash-1017"]["routes"]}
    assert routes == {"deepseek", "openrouter"}


def test_a_local_tile_says_whether_its_weights_are_here_and_fit(surface, operator):
    def qwen():
        tiles = {tile["id"]: tile for tile in operator.get("/admin/catalog").json()["models"]}
        return tiles["qwen3_8-27b"]["local"]

    local = qwen()
    assert local["name"] == "Qwen3.8-27B-Q4"
    assert local["vision"] is True
    assert local["context_tokens"] == 65536
    assert local["weights"]["state"] == "absent"
    # No GPU sample: whether it fits is unknown, not guessed.
    assert local["fits"] is None

    settings = surface.services.settings
    settings.gpu_sample_path.write_text(
        json.dumps(
            {
                "available": True,
                "devices": [{"index": 0, "memory_total_mb": 24576}],
                "processes": [],
            }
        ),
        encoding="utf-8",
    )
    assert qwen()["fits"] is True
    settings.gpu_sample_path.write_text(
        json.dumps(
            {"available": True, "devices": [{"memory_total_mb": 16384}], "processes": []}
        ),
        encoding="utf-8",
    )
    assert qwen()["fits"] is False

    settings.models_dir.mkdir()
    (settings.models_dir / "mmproj-F16.gguf").write_bytes(b"short")
    assert qwen()["weights"]["state"] == "wrong"


def test_a_provider_is_added_from_a_preset_and_a_key(surface, operator, caplog):
    response = operator.post("/admin/providers", json={"preset": "deepseek", "api_key": KEY})
    assert response.status_code == 201, response.text

    key_file = _secrets(surface) / "deepseek.key"
    assert key_file.read_text(encoding="utf-8") == KEY
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600

    entry = surface.services.baseline["providers"]["deepseek"]
    assert entry == {
        "url": "https://api.deepseek.com/v1",
        "api_key_file": str(key_file),
        "preset": "deepseek",
    }

    # The key is in the file and nowhere a reader of anything else would find it.
    assert KEY not in response.text
    assert KEY not in json.dumps(surface.services.baseline)
    assert all(KEY not in json.dumps(row.payload) for row in surface.audit.rows)
    assert KEY not in caplog.text
    assert surface.audit.rows[-1].action == "settings.provider_added"
    assert surface.audit.rows[-1].payload["key"] is True

    tiles = operator.get("/admin/catalog").json()["providers"]
    deepseek = next(tile for tile in tiles if tile["id"] == "deepseek")
    assert deepseek["added"] == [{"name": "deepseek", "key": True}]


def test_a_preset_provider_without_a_key_is_refused(surface, operator):
    response = operator.post("/admin/providers", json={"preset": "openrouter"})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_key"
    assert "openrouter" not in (surface.services.baseline.get("providers") or {})


def test_a_second_provider_under_a_taken_name_is_refused(surface, operator):
    first = operator.post("/admin/providers", json={"preset": "deepseek", "api_key": KEY})
    assert first.status_code == 201
    again = operator.post(
        "/admin/providers", json={"preset": "deepseek", "api_key": "sk-other"}
    )

    assert again.status_code == 409
    assert again.json()["error"] == "provider_exists"
    # The first provider's key was not overwritten by the refused request.
    assert (_secrets(surface) / "deepseek.key").read_text(encoding="utf-8") == KEY


def test_a_second_account_at_one_provider_takes_a_name(surface, operator):
    operator.post("/admin/providers", json={"preset": "deepseek", "api_key": KEY})
    response = operator.post(
        "/admin/providers",
        json={"preset": "deepseek", "name": "deepseek-team", "api_key": "sk-2"},
    )

    assert response.status_code == 201, response.text
    assert (_secrets(surface) / "deepseek-team.key").read_text(encoding="utf-8") == "sk-2"


def test_a_custom_provider_needs_https_off_this_machine(surface, operator):
    plain = operator.post(
        "/admin/providers", json={"name": "lab", "url": "http://10.0.0.5/v1", "api_key": KEY}
    )
    assert plain.status_code == 400
    assert plain.json()["error"] == "invalid_url"
    assert not (_secrets(surface) / "lab.key").exists()

    loopback = operator.post(
        "/admin/providers", json={"name": "gpu", "url": "http://127.0.0.1:8081/v1"}
    )
    assert loopback.status_code == 201, loopback.text
    assert surface.services.baseline["providers"]["gpu"]["api_key_file"] is None
    assert "preset" not in surface.services.baseline["providers"]["gpu"]


def test_a_key_is_replaced_in_place(surface, operator):
    operator.post("/admin/providers", json={"preset": "deepseek", "api_key": KEY})
    response = operator.put("/admin/providers/deepseek/key", json={"api_key": "sk-rotated"})

    assert response.status_code == 200, response.text
    assert "sk-rotated" not in response.text
    assert (_secrets(surface) / "deepseek.key").read_text(encoding="utf-8") == "sk-rotated"
    assert surface.audit.rows[-1].action == "settings.provider_key_set"


def test_a_key_entered_for_a_hand_declared_provider_moves_it_to_the_managed_file(
    surface, operator
):
    operator.post("/admin/providers", json={"name": "lab", "url": "https://lab.test/v1"})
    response = operator.put("/admin/providers/lab/key", json={"api_key": KEY})

    assert response.status_code == 200, response.text
    assert surface.services.baseline["providers"]["lab"]["api_key_file"] == str(
        _secrets(surface) / "lab.key"
    )


def test_a_model_is_added_from_a_preset_on_a_provider_with_its_route(surface, operator):
    operator.post("/admin/providers", json={"preset": "openrouter", "api_key": KEY})
    response = operator.post(
        "/admin/models", json={"preset": "deepseek-v4_1-flash-1017", "provider": "openrouter"}
    )

    assert response.status_code == 201, response.text
    entry = surface.services.baseline["models"]["DeepSeek-V4.1-Flash-1017"]
    assert entry["provider"] == "openrouter"
    assert entry["provider_model"] == "deepseek/deepseek-v4.1-flash-1017"
    assert entry["preset"] == "deepseek-v4_1-flash-1017"
    assert surface.audit.rows[-1].action == "settings.model_added"

    # Adding a model grants it to nobody.
    assert "DeepSeek-V4.1-Flash-1017" not in json.dumps(surface.services.baseline["defaults"])


def test_a_model_on_a_provider_without_its_route_is_refused(surface, operator):
    operator.post("/admin/providers", json={"preset": "deepseek", "api_key": KEY})
    response = operator.post(
        "/admin/models", json={"preset": "glm-5_3-flash", "provider": "deepseek"}
    )

    assert response.status_code == 400
    assert response.json()["error"] == "no_route"
    assert "GLM-5.3-Flash" not in surface.services.baseline["models"]


def test_one_model_on_two_providers_needs_a_second_name(surface, operator):
    operator.post("/admin/providers", json={"preset": "deepseek", "api_key": KEY})
    operator.post("/admin/providers", json={"preset": "openrouter", "api_key": KEY})
    first = operator.post(
        "/admin/models", json={"preset": "deepseek-v4_1-flash-1017", "provider": "deepseek"}
    )
    clash = operator.post(
        "/admin/models", json={"preset": "deepseek-v4_1-flash-1017", "provider": "openrouter"}
    )
    named = operator.post(
        "/admin/models",
        json={
            "preset": "deepseek-v4_1-flash-1017",
            "provider": "openrouter",
            "name": "Flash-cheap",
        },
    )

    assert first.status_code == 201
    assert clash.status_code == 409
    assert clash.json()["error"] == "model_exists"
    assert named.status_code == 201, named.text


def test_a_baseline_write_contradicting_a_preset_fact_is_refused(surface, operator):
    operator.post("/admin/providers", json={"preset": "deepseek", "api_key": KEY})
    operator.post(
        "/admin/models", json={"preset": "deepseek-v4_1-flash-1017", "provider": "deepseek"}
    )

    current = operator.get("/admin/baseline").json()["baseline"]
    document = json.loads(json.dumps(current))
    document["models"]["DeepSeek-V4.1-Flash-1017"]["context_tokens"] = 262144
    response = operator.put("/admin/baseline", json={"baseline": document, "base": current})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_catalog"
    assert "context_tokens" in response.json()["detail"]


def test_removing_a_provider_through_the_baseline_takes_its_key(surface, operator):
    operator.post("/admin/providers", json={"preset": "deepseek", "api_key": KEY})
    assert (_secrets(surface) / "deepseek.key").exists()

    current = operator.get("/admin/baseline").json()["baseline"]
    document = json.loads(json.dumps(current))
    del document["providers"]["deepseek"]
    response = operator.put("/admin/baseline", json={"baseline": document, "base": current})

    assert response.status_code == 200, response.text
    assert not (_secrets(surface) / "deepseek.key").exists()


def test_a_provider_a_model_still_uses_cannot_be_removed(surface, operator):
    operator.post("/admin/providers", json={"preset": "deepseek", "api_key": KEY})
    operator.post(
        "/admin/models", json={"preset": "deepseek-v4_1-flash-1017", "provider": "deepseek"}
    )

    current = operator.get("/admin/baseline").json()["baseline"]
    document = json.loads(json.dumps(current))
    del document["providers"]["deepseek"]
    response = operator.put("/admin/baseline", json={"baseline": document, "base": current})

    assert response.status_code == 400
    assert "names provider `deepseek`, which is not declared" in response.json()["detail"]
    assert (_secrets(surface) / "deepseek.key").exists()


def test_a_refused_commit_leaves_no_key_behind(monkeypatch, surface, operator):
    from neuman_server.composition.adminapi import catalog

    async def stale(store, key, *, expected, value):
        return False

    monkeypatch.setattr(catalog.SettingsStore, "replace_if", stale)
    response = operator.post("/admin/providers", json={"preset": "deepseek", "api_key": KEY})

    assert response.status_code == 409
    assert response.json()["error"] == "baseline_changed"
    assert not (_secrets(surface) / "deepseek.key").exists()


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/admin/providers", {"preset": "deepseek", "api_key": KEY}),
        (
            "post",
            "/admin/models",
            {"preset": "deepseek-v4_1-flash-1017", "provider": "deepseek"},
        ),
        ("post", "/admin/models/local/qwen", {}),
    ],
)
def test_a_peer_without_authority_writes_nothing(
    monkeypatch, surface, client, method, path, body
):
    as_peer(monkeypatch, client, 1002, "bob")
    before = json.dumps(surface.services.baseline, sort_keys=True)

    response = getattr(client, method)(path, json=body)

    assert response.status_code in (403, 404), response.text
    assert json.dumps(surface.services.baseline, sort_keys=True) == before
    assert not _secrets(surface).exists() or not any(_secrets(surface).iterdir())


def test_a_key_is_not_written_for_a_peer_without_authority(monkeypatch, surface, client):
    """The file is the change, so the authority check has to come before it."""

    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")
    client.post("/admin/providers", json={"preset": "deepseek", "api_key": KEY})

    # Revoked between the two requests: the peer still reaches the socket, and
    # the request must be refused before the key on disk is touched. The guard
    # in front of every `/admin` route is what refuses it; the route's own check
    # is the second layer, as on every other write.
    surface.accounts.groups["neuman-admin"].discard("alice")
    response = client.put("/admin/providers/deepseek/key", json={"api_key": "sk-stolen"})

    assert response.status_code == 403
    assert (_secrets(surface) / "deepseek.key").read_text(encoding="utf-8") == KEY


def test_startup_reapplies_a_presets_facts_and_stores_them(monkeypatch, tmp_path):
    """A window corrected in a release reaches an entry added under an older one."""

    from neuman_server.decision.harness.presets import build_entry
    from neuman_server.foundation.state import load_presets

    presets = load_presets()
    stale = build_entry(presets, "deepseek-v4_1-flash-1017", "deepseek", "deepseek")
    stale["context_tokens"] = 131072
    stale["max_tokens"] = 4096
    baseline = {
        "schema_version": 2,
        "defaults": {},
        "roles": {},
        "users": [],
        "providers": {
            "deepseek": {
                "url": presets["providers"]["deepseek"]["url"],
                "api_key_file": None,
                "preset": "deepseek",
            }
        },
        "models": {"DeepSeek-V4.1-Flash-1017": stale},
    }

    surface = build_surface(monkeypatch, tmp_path, baseline=baseline)
    with surface.client():
        entry = surface.services.baseline["models"]["DeepSeek-V4.1-Flash-1017"]

    assert entry["context_tokens"] == 1048576
    assert entry["max_tokens"] == 4096
    # Stored, not only held in memory: the next restart reads the database.
    stored = surface.settings.values["baseline"]["models"]["DeepSeek-V4.1-Flash-1017"]
    assert stored["context_tokens"] == 1048576


def test_a_fresh_database_starts_with_an_empty_baseline_and_prompt(monkeypatch, tmp_path):
    """No seed file exists to read (D68): a new appliance is configured in the panel."""

    surface = build_surface(monkeypatch, tmp_path)
    surface.settings.values.clear()
    with surface.client():
        assert surface.services.baseline == {
            "schema_version": 2,
            "defaults": {},
            "roles": {},
            "users": [],
        }
        assert surface.services.operator_prompt == ""

    assert surface.settings.values == {
        "baseline": surface.services.baseline,
        "prompt": "",
        # The name is written rather than left absent, so the panel reads back
        # the name in force instead of an empty box for a field that is never
        # empty (D82).
        "name": "Neuman",
    }


def test_the_panel_adds_a_provider_over_its_session(surface, client):
    """The tiles are drawn by the client, which reaches these routes as `/api/admin`."""

    surface.accounts.groups["neuman-admin"].add("alice")
    csrf = authenticate(surface, client)

    refused = client.post(
        "/api/admin/providers",
        json={"preset": "deepseek", "api_key": KEY},
        headers={"origin": ORIGIN},
    )
    assert refused.status_code == 403, refused.text
    assert not (_secrets(surface) / "deepseek.key").exists()

    added = client.post(
        "/api/admin/providers",
        json={"preset": "deepseek", "api_key": KEY},
        headers={"origin": ORIGIN, "x-csrf-token": csrf},
    )
    assert added.status_code == 201, added.text
    assert surface.audit.rows[-1].actor == "alice"


# -- models on this machine, and weights ---------------------------------------


def _verify(surface, preset_id):
    """Put a preset's files in `modelsDir` with the fetcher's record, as a fetch leaves them.

    Tiny stand-ins at the pinned names: what the route checks is the record and
    the size, and writing 16 GB to prove that would prove nothing more.
    """

    preset = surface.services.presets["models"][preset_id]
    models_dir = surface.services.settings.models_dir
    (models_dir / ".verified").mkdir(parents=True, exist_ok=True)
    for file in preset["local"]["facts"]["weights"]["files"]:
        path = models_dir / file["name"]
        with path.open("wb") as out:
            out.truncate(file["size"])
        stat = path.stat()
        (models_dir / ".verified" / f"{file['name']}.json").write_text(
            json.dumps(
                {"sha256": file["sha256"], "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
            ),
            encoding="utf-8",
        )


def test_a_local_model_waits_for_verified_weights(surface, operator):
    """Present is not enough: a file of the right size could be anything."""

    response = operator.post("/admin/models/local", json={"preset": "qwen3_8-27b"})
    assert response.status_code == 409, response.text
    assert response.json()["error"] == "weights_not_verified"


def test_a_local_model_is_added_and_the_runtime_is_told(surface, operator):
    _verify(surface, "qwen3_8-27b")

    response = operator.post("/admin/models/local", json={"preset": "qwen3_8-27b"})
    assert response.status_code == 201, response.text

    entry = surface.services.baseline["models"]["Qwen3.8-27B-Q4"]
    assert "provider" not in entry
    assert entry["provider_model"] == "Qwen3.8-27B-Q4"
    assert entry["context_tokens"] == 65536
    assert entry["vision"] is True
    assert entry["local"] == {
        "gpu_layers": 99,
        "speculation": "draft-mtp",
        "residency": "on-demand",
        "idle_minutes": 15,
    }
    assert surface.audit.rows[-1].action == "settings.local_model_added"

    # What the runtime host will read, written with the baseline.
    document = json.loads(surface.services.settings.runtime_config.read_text(encoding="utf-8"))
    assert document["chat"] == [
        {
            "name": "Qwen3.8-27B-Q4",
            "preset": "qwen3_8-27b",
            "context_size": 65536,
            "gpu_layers": 99,
            "speculation": "draft-mtp",
            "residency": "on-demand",
            "idle_minutes": 15,
        }
    ]

    # And it resolves to the runtime's router, with no key.
    import asyncio

    target = asyncio.run(surface.services.model_catalog().resolve("Qwen3.8-27B-Q4"))
    assert target.endpoint == surface.services.settings.runtime_chat_url
    assert target.api_key_file is None

    tiles = {tile["id"]: tile for tile in operator.get("/admin/catalog").json()["models"]}
    assert tiles["qwen3_8-27b"]["local"]["added"] == ["Qwen3.8-27B-Q4"]
    assert tiles["qwen3_8-27b"]["local"]["weights"]["state"] == "verified"


def test_a_local_entry_cannot_carry_a_flag_or_contradict_its_preset(surface, operator):
    """The baseline route is held to the same rule as the tile."""

    _verify(surface, "qwen3_8-27b")
    operator.post("/admin/models/local", json={"preset": "qwen3_8-27b"})
    document = json.loads(json.dumps(surface.services.baseline))

    document["models"]["Qwen3.8-27B-Q4"]["local"]["extra_flags"] = ["--model", "/etc/shadow"]
    response = operator.put("/admin/baseline", json={"baseline": document})
    assert response.status_code == 400, response.text

    del document["models"]["Qwen3.8-27B-Q4"]["local"]["extra_flags"]
    document["models"]["Qwen3.8-27B-Q4"]["vision"] = False
    response = operator.put("/admin/baseline", json={"baseline": document})
    assert response.status_code == 400, response.text
    assert "vision" in response.json()["detail"]


def test_a_fetch_names_only_a_shipped_preset(surface, operator):
    fetch = surface.services.fetch

    response = operator.post("/admin/weights/qwen3_8-27b/fetch", json={})
    assert response.status_code == 202, response.text
    assert surface.audit.rows[-1].action == "weights.fetch_requested"

    # A hosted-only model has nothing to fetch, and an invented one is not a
    # way to make the fetcher download something.
    for preset in ("kimi-k3_1-pro", "someone-evil-GGUF"):
        assert operator.post(f"/admin/weights/{preset}/fetch", json={}).status_code == 404
    assert fetch.calls == [("fetch", "qwen3_8-27b")]

    tiles = {tile["id"]: tile for tile in operator.get("/admin/catalog").json()["models"]}
    assert tiles["qwen3_8-27b"]["local"]["fetch"]["state"] == "fetching"


def test_weights_in_use_are_not_deleted(surface, operator):
    """Deleting the file under a model leaves one users can select and none can run."""

    _verify(surface, "qwen3_8-27b")
    operator.post("/admin/models/local", json={"preset": "qwen3_8-27b"})

    response = operator.post("/admin/weights/qwen3_8-27b/delete", json={})
    assert response.status_code == 409, response.text
    assert "Qwen3.8-27B-Q4" in response.json()["detail"]
    assert surface.services.fetch.calls == []

    document = json.loads(json.dumps(surface.services.baseline))
    del document["models"]["Qwen3.8-27B-Q4"]
    assert operator.put("/admin/baseline", json={"baseline": document}).status_code == 200
    assert operator.post("/admin/weights/qwen3_8-27b/delete", json={}).status_code == 200
    assert surface.services.fetch.calls == [("delete", "qwen3_8-27b")]
    assert surface.audit.rows[-1].action == "weights.deleted"


def test_a_local_entry_does_not_mark_the_hosted_tile_added(surface, operator):
    _verify(surface, "qwen3_8-27b")
    operator.post("/admin/models/local", json={"preset": "qwen3_8-27b"})

    tile = next(
        t for t in operator.get("/admin/catalog").json()["models"] if t["id"] == "qwen3_8-27b"
    )
    assert tile["added"] == []
    assert tile["local"]["added"] == ["Qwen3.8-27B-Q4"]


def test_a_composed_prompt_names_where_each_section_came_from(surface, operator):
    """The page shows the prompt the turn would send, with each part's source.

    Ids rather than prose, because the page owns the sentence describing a
    source (D81). What matters here is that the composed list is the same set
    of sections `system_prompt` joins, so the page cannot show text the model
    never receives.
    """

    response = operator.get(
        "/admin/prompt/compose", params={"account": "bob", "machine": "ernest"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["account"] == "bob"
    # No operator text and no account instructions are set, so those tiers add
    # nothing — an empty tier is absent, not an empty box on the page.
    assert [section["source"] for section in body["sections"]] == [
        "identity",
        "untrusted",
        "reach",
        "tools",
    ]
    assert "bob" in body["sections"][0]["text"]
    assert "Read on the acting machine" not in body["sections"][0]["text"]
    # Reported rather than left as a gap: a blank section reads as "the prompt
    # says nothing here", which is the opposite of a dispatch-only one.
    assert body["omitted"] == ["project"]
    assert "acting machine" in body["note"]


def test_composing_a_prompt_for_an_unknown_account_is_a_404(surface, operator):
    response = operator.get(
        "/admin/prompt/compose", params={"account": "nobody", "machine": "ernest"}
    )
    assert response.status_code == 404


def test_composing_a_prompt_needs_admin_authority(surface, client, monkeypatch):
    """The route reads another account's policy, so it is an operator's to use."""

    as_peer(monkeypatch, client, 1001, "bob")
    response = client.get(
        "/admin/prompt/compose", params={"account": "bob", "machine": "ernest"}
    )
    assert response.status_code == 403


def test_renaming_the_appliance_reaches_the_prompt(surface, operator):
    """The name is voice, but it is *the* voice: the identity sentence names it.

    A rename that does not reach the prompt would leave the agent introducing
    itself under a name the operator had replaced, which is worse than not
    being able to rename it at all.
    """

    assert operator.put("/admin/name", json={"name": "Acme Assistant"}).status_code == 200

    body = operator.get(
        "/admin/prompt/compose", params={"account": "bob", "machine": "ernest"}
    ).json()
    identity = next(s for s in body["sections"] if s["source"] == "identity")
    assert (
        "You are Acme Assistant, an assistant running on a governed appliance."
        in (identity["text"])
    )
    assert surface.services.agent_name == "Acme Assistant"


def test_a_name_is_collapsed_bounded_and_defaulted(surface, operator):
    """Whitespace is a typo to fix, not an error to report; length is refused.

    An empty name is the default rather than a refusal: the wordmark is a field
    an operator clears to put the appliance's name back, and a form that refused
    it made them find the word they had just deleted (N175).
    """

    collapsed = operator.put("/admin/name", json={"name": "  Acme   Agent  "})
    assert collapsed.json()["name"] == "Acme Agent"
    assert surface.services.agent_name == "Acme Agent"

    cleared = operator.put("/admin/name", json={"name": "   "})
    assert cleared.json()["name"] == "Neuman"
    assert surface.services.agent_name == "Neuman"

    assert operator.put("/admin/name", json={"name": "x" * 65}).status_code == 400
    assert operator.put("/admin/name", json={"name": "ok"}).status_code == 200


def test_the_brand_is_readable_before_signing_in(client):
    """The sign-in screen is branded, so its name cannot require a session."""

    response = client.get("/api/brand")

    assert response.status_code == 200
    assert response.json() == {"name": "Neuman"}


def test_a_model_names_its_maker(operator):
    """A preset's logo is how the panel colours a model; a hand-added one has none."""

    operator.post("/admin/providers", json={"preset": "deepseek", "api_key": KEY})
    added = operator.post(
        "/admin/models", json={"preset": "deepseek-v4_1-flash-1017", "provider": "deepseek"}
    )
    assert added.status_code == 201, added.text

    models = {row["name"]: row for row in operator.get("/admin/models").json()["models"]}

    assert models["DeepSeek-V4.1-Flash-1017"]["logo"] == "deepseek"


# -- tool packages (D101) ------------------------------------------------------


def test_the_tools_page_lists_built_in_tools_and_the_shipped_packages(operator):
    """From the shipped `tools/` tree, not a fixture: what the operator is offered."""

    body = operator.get("/admin/tool-packages").json()

    blender = {package["id"]: package for package in body["packages"]}["blender"]
    assert blender["enabled"] is False
    assert blender["requires"]["name"] == "Blender"
    assert blender["host"] is True
    tools = {tool["name"]: tool for tool in blender["tools"]}
    assert set(tools) == {"blender", "blender_scene", "blender_view"}
    assert tools["blender"]["mutating"] and not tools["blender_view"]["mutating"]
    assert tools["blender_view"]["returns_image"]

    builtin = {tool["name"] for tool in body["builtin"]}
    assert "fs_read" in builtin and "blender" not in builtin
    assert "env_probe" not in builtin, "a diagnostic is not something an operator enables"


def test_enabling_a_package_writes_the_baseline_and_grants_nothing(surface, operator):
    """`packages` gains the id, every role is untouched, and the change is recorded."""

    roles_before = surface.services.baseline.get("roles")

    response = operator.put("/admin/tool-packages/blender", json={"enabled": True})
    assert response.status_code == 200, response.text
    assert surface.services.baseline["packages"] == ["blender"]
    assert surface.services.baseline.get("roles") == roles_before
    assert surface.audit.rows[-1].action == "settings.tool_package_enabled"
    listed = operator.get("/admin/tool-packages").json()["packages"]
    assert {package["id"]: package for package in listed}["blender"]["enabled"] is True

    response = operator.put("/admin/tool-packages/blender", json={"enabled": False})
    assert response.status_code == 200, response.text
    assert surface.services.baseline["packages"] == []
    assert surface.audit.rows[-1].action == "settings.tool_package_disabled"


def test_a_package_neuman_does_not_ship_cannot_be_enabled(surface, operator):
    response = operator.put("/admin/tool-packages/blender2", json={"enabled": True})
    assert response.status_code == 404
    assert (
        "packages" not in surface.services.baseline or not surface.services.baseline["packages"]
    )


def test_only_an_operator_enables_a_package(monkeypatch, surface, client):
    as_peer(monkeypatch, client, 1001, "alice")
    response = client.put("/admin/tool-packages/blender", json={"enabled": True})
    assert response.status_code == 403
