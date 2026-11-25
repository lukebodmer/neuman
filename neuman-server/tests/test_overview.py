"""The overview: what needs the operator, what is happening, what it costs.

Each attention check is tested by making the thing it reports true and finding
the line, and the quiet case by finding no line: a check that never fires and a
check that always fires look the same from a passing suite.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeAuditRow, FakeRuntime
from surface import build_surface
from test_http_surface import as_peer

from neuman_server.composition.app import RunningTurn
from neuman_server.decision.harness.inbox import PendingApproval


class _Running:
    """A task that has not finished, for the registry's `done()` check."""

    def done(self) -> bool:
        return False


def _baseline(**extra):
    base = {
        "schema_version": 2,
        "defaults": {"models": ["m"]},
        "roles": {},
        "users": [],
        "providers": {"p": {"url": "https://p.test/v1", "api_key_file": None}},
        "models": {"m": {"provider": "p", "provider_model": "m"}},
    }
    base.update(extra)
    return base


@pytest.fixture
def make(monkeypatch, tmp_path):
    def build(baseline=None, runtime=None):
        surface = build_surface(
            monkeypatch, tmp_path, baseline=baseline or _baseline(), runtime=runtime
        )
        surface.accounts.groups["neuman-admin"].add("alice")
        # A backup exists unless a test is about backups, so the list holds
        # only what the test made true.
        surface.audit.rows.append(
            FakeAuditRow(
                seq=1,
                ts=datetime.now(UTC),
                actor="alice",
                action="ops.backup",
                payload={},
                prev_hash="",
                hash="",
            )
        )
        return surface

    return build


def _overview(monkeypatch, surface):
    with surface.client() as client:
        as_peer(monkeypatch, client, 1001, "alice")
        response = client.get("/admin/overview")
        assert response.status_code == 200, response.text
        return response.json()


def _texts(body):
    return [item["text"] for item in body["attention"]]


def test_a_healthy_appliance_needs_nothing(monkeypatch, make):
    assert _overview(monkeypatch, make())["attention"] == []


def test_a_model_nobody_may_choose_and_a_grant_of_nothing_are_named(monkeypatch, make):
    baseline = _baseline(
        defaults={"models": ["ghost"]},
        models={"m": {"provider": "p", "provider_model": "m"}},
    )
    body = _overview(monkeypatch, make(baseline))
    texts = _texts(body)
    assert any("m is granted to no role" in t for t in texts), texts
    assert any("Roles grant ghost, which is not in the catalog" in t for t in texts), texts
    assert all(item["tab"] == "roles" for item in body["attention"])


def test_a_preset_provider_whose_key_is_gone_is_an_error(monkeypatch, make, tmp_path):
    baseline = _baseline(
        providers={
            "deepseek": {
                "url": "https://api.deepseek.com/v1",
                "api_key_file": str(tmp_path / "gone.key"),
                "preset": "deepseek",
            }
        },
        models={"m": {"provider": "deepseek", "provider_model": "m"}},
    )
    body = _overview(monkeypatch, make(baseline))
    item = body["attention"][0]
    assert item["level"] == "error" and item["tab"] == "providers", body
    assert "deepseek's key file is missing" in item["text"]


def test_a_local_model_without_its_weights_and_a_runtime_problem_are_errors(monkeypatch, make):
    baseline = _baseline(
        defaults={"models": ["q"]},
        models={
            "q": {
                "provider_model": "q",
                "preset": "qwen3_8-27b",
                "context_tokens": 65536,
                "local": {},
            }
        },
    )
    runtime = FakeRuntime(
        {
            "problem": "the chat entry has fields this host does not take",
            "chat": [{"name": "q", "loaded": False, "error": "the GPU ran out of memory"}],
        }
    )
    texts = _texts(_overview(monkeypatch, make(baseline, runtime)))
    assert any("q's weights are absent; it cannot load" in t for t in texts), texts
    assert any("refused its configuration" in t for t in texts), texts
    assert any("q could not load: the GPU ran out of memory" in t for t in texts), texts


def test_errors_come_before_warnings(monkeypatch, make, tmp_path):
    baseline = _baseline(
        defaults={"models": []},
        providers={
            "deepseek": {
                "url": "https://api.deepseek.com/v1",
                "api_key_file": None,
                "preset": "deepseek",
            }
        },
        models={"m": {"provider": "deepseek", "provider_model": "m"}},
    )
    levels = [item["level"] for item in _overview(monkeypatch, make(baseline))["attention"]]
    assert levels == sorted(levels, key=lambda level: level != "error"), levels
    assert levels[0] == "error"


def test_a_missing_and_a_stale_backup_are_named(monkeypatch, make):
    surface = make()
    surface.audit.rows.clear()
    missing = _texts(_overview(monkeypatch, surface))
    assert any("No backup has been made" in t for t in missing)
    # "A backup" names an act, not the thing at risk: the line says what one
    # holds, because "a backup of what?" is the question it raises.
    assert any("the database, the audit log and the policy overlay" in t for t in missing)

    surface.audit.rows.append(
        FakeAuditRow(
            seq=1,
            ts=datetime.now(UTC) - timedelta(days=9),
            actor="alice",
            action="ops.backup",
            payload={},
            prev_hash="",
            hash="",
        )
    )
    stale = _texts(_overview(monkeypatch, surface))
    assert any("9 days ago" in t for t in stale)
    assert any("the database, the audit log and the policy overlay" in t for t in stale)


def test_an_account_that_must_change_its_password_is_named(monkeypatch, make):
    surface = make()
    surface.accounts.rows["bob"].must_change_password = True
    texts = _texts(_overview(monkeypatch, surface))
    assert any("bob must still change their password" in t for t in texts), texts


def test_now_reports_people_turns_approvals_and_the_gpu(monkeypatch, make):
    runtime = FakeRuntime(
        {
            "problem": None,
            "chat": [{"name": "q", "loaded": True}, {"name": "r", "loaded": False}],
        }
    )
    surface = make(runtime=runtime)
    surface.accounts.rows["bob"].last_seen = datetime.now(UTC) - timedelta(minutes=3)
    surface.accounts.rows["carol"].last_seen = datetime.now(UTC) - timedelta(hours=2)
    surface.services.execution.connected_names = ["bob"]
    inbox = surface.services.inbox_for("s1")
    surface.services.turns["s1"] = RunningTurn(
        account="bob",
        account_id="acct-bob",
        session_id="s1",
        inbox=inbox,
        task=_Running(),
    )
    # A call waiting on a person, as the inbox holds one while a turn awaits an
    # approval; set directly because the waiting itself is a turn's coroutine.
    inbox._prompts["call-1"] = PendingApproval(tool_call_id="call-1", tool="fs_write")
    surface.services.settings.gpu_sample_path.write_text(
        json.dumps(
            {
                "available": True,
                "sampled_at": 10.0,
                "devices": [{"name": "RTX", "memory_used_mb": 23000, "memory_total_mb": 24576}],
            }
        ),
        encoding="utf-8",
    )

    now = _overview(monkeypatch, surface)["now"]
    assert "bob" in now["active"] and "carol" not in now["active"], now
    assert now["clients"] == ["bob"]
    assert now["client_versions"] == [{"account": "bob", "release": "0.1.0", "rpc": 1}]
    assert now["turns"] == [{"branch_id": "s1", "account": "bob"}]
    assert now["awaiting"] == [{"branch_id": "s1", "account": "bob", "calls": 1}]
    assert now["loaded"] == ["q"]
    assert now["gpu"]["used_mb"] == 23000


def test_today_shows_tokens_and_dollars_and_says_which_tokens_were_unpriced(monkeypatch, make):
    surface = make(
        _baseline(
            models={
                "m": {"provider": "p", "provider_model": "m"},
                "q": {
                    "provider_model": "q",
                    "preset": "qwen3_8-27b",
                    "context_tokens": 65536,
                    "local": {},
                },
            },
            defaults={"models": ["m", "q"]},
        )
    )
    today = datetime.now(UTC).date()
    surface.usage.records = [
        ("alice", today, 900_000, 0),
        ("bob", today, 300_000, 0),
        ("alice", today - timedelta(days=2), 500_000, 0),
    ]
    surface.usage.by_model = [
        {"model": "m", "tokens": 900_000, "usd": 0.84, "unpriced_tokens": 0, "estimated": True},
        {
            "model": "q",
            "tokens": 300_000,
            "usd": None,
            "unpriced_tokens": 300_000,
            "estimated": False,
        },
    ]

    day = _overview(monkeypatch, surface)["today"]
    assert day["tokens"] == 1_200_000
    assert day["usd"] == 0.84 and day["usd_estimated"] is True
    assert day["unpriced_tokens"] == 300_000
    assert [d["tokens"] for d in day["days"]] == [0, 0, 0, 0, 500_000, 0, 1_200_000]
    assert day["accounts"][0] == {"name": "alice", "tokens": 900_000}
    assert day["models"][1] == {"name": "q", "tokens": 300_000, "usd": None, "local": True}


def test_no_priced_tokens_is_no_dollar_figure_rather_than_zero(monkeypatch, make):
    """$0.00 for a day of unpriced local use is the kind of number that gets believed."""

    surface = make()
    surface.usage.by_model = [
        {"model": "q", "tokens": 1000, "usd": None, "unpriced_tokens": 1000, "estimated": False}
    ]
    assert _overview(monkeypatch, surface)["today"]["usd"] is None


def test_recently_changed_is_configuration_in_words_not_tool_calls(monkeypatch, make):
    surface = make()
    now = datetime.now(UTC)
    for seq, (action, payload) in enumerate(
        [
            ("tool.dispatch", {"tool": "fs_read"}),
            ("settings.local_model_added", {"name": "Qwen3.8-27B-Q4", "preset": "qwen3_8-27b"}),
            ("identity.login", {"account": "bob"}),
            ("policy.role_granted", {"group": "eng", "role": "full-access"}),
        ],
        start=2,
    ):
        surface.audit.rows.append(
            FakeAuditRow(
                seq=seq,
                ts=now,
                actor="alice",
                action=action,
                payload=payload,
                prev_hash="",
                hash="",
            )
        )

    recent = [item["summary"] for item in _overview(monkeypatch, surface)["recent"]]
    assert recent[:2] == ["granted full-access to eng", "added Qwen3.8-27B-Q4 on this machine"]
    assert "made a backup" in recent
    assert not any("fs_read" in item or "bob" in item for item in recent), recent


def test_a_schema_the_build_does_not_expect_is_an_error(monkeypatch, make):
    from neuman_server.composition.adminapi import overview

    async def behind(_conn):
        return 0

    monkeypatch.setattr(overview, "schema_version", behind)
    item = _overview(monkeypatch, make())["attention"][0]
    assert item["level"] == "error" and "schema 0" in item["text"], item


def test_an_open_overview_reads_the_usage_once_a_minute_not_on_every_refresh(monkeypatch, make):
    """The reply scan measured ~73 ms on a million events; an open overview
    refreshes on every GPU change."""

    surface = make()
    reads = []
    original = surface.usage.by_model_since

    async def counted(moment):
        reads.append(moment)
        return await original(moment)

    surface.usage.by_model_since = counted
    with surface.client() as client:
        as_peer(monkeypatch, client, 1001, "alice")
        for _ in range(3):
            assert client.get("/admin/overview").status_code == 200
    assert len(reads) == 1, reads


def test_recent_changes_name_presets_by_label(monkeypatch, make):
    surface = make()
    now = datetime.now(UTC)
    for seq, (action, payload) in enumerate(
        [("weights.fetch_requested", {"preset": "qwen3_8-27b"})],
        start=2,
    ):
        surface.audit.rows.append(
            FakeAuditRow(
                seq=seq,
                ts=now,
                actor="alice",
                action=action,
                payload=payload,
                prev_hash="",
                hash="",
            )
        )
    recent = [item["summary"] for item in _overview(monkeypatch, surface)["recent"]]
    assert recent[0] == "downloaded Qwen3.8 27B"
