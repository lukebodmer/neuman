"""Config, logging, state files, and the migration runner."""

from __future__ import annotations

import io
import json
import logging

import pytest

from neuman_server.foundation import ConfigError, Settings, configure_logging, get_logger
from neuman_server.foundation.db import latest_version, migrate
from neuman_server.foundation.db.migrate import MigrationError, migrations
from neuman_server.foundation.state import (
    OverlayError,
    delete_overlay,
    list_overlays,
    read_overlay,
    write_overlay,
)

# -- config -----------------------------------------------------------------


def test_defaults_are_usable():
    settings = Settings.from_env({})
    assert settings.port == 8443
    assert settings.overlay_dir == settings.state_dir / "policies"


def test_tls_cert_and_key_must_come_together():
    with pytest.raises(ConfigError, match="must be set together"):
        Settings.from_env({"NEUMAN_TLS_CERT": "/etc/cert.pem"})


def test_a_bad_port_is_rejected():
    with pytest.raises(ConfigError, match="out of range"):
        Settings.from_env({"NEUMAN_PORT": "70000"})


def test_a_non_numeric_value_names_its_variable():
    with pytest.raises(ConfigError, match="NEUMAN_PORT is not an integer"):
        Settings.from_env({"NEUMAN_PORT": "eight"})


def test_relative_paths_are_rejected():
    with pytest.raises(ConfigError, match="absolute path"):
        Settings.from_env({"NEUMAN_STATE_DIR": "var/lib/neuman"})


def test_pool_bounds_are_checked():
    with pytest.raises(ConfigError, match="invalid pool bounds"):
        Settings.from_env({"NEUMAN_POOL_MIN": "10", "NEUMAN_POOL_MAX": "2"})


# -- logging ----------------------------------------------------------------


def _emit(**kwargs):
    stream = io.StringIO()
    configure_logging(logging.DEBUG, stream)
    logger = get_logger("neuman.test")
    logger.info("a message", fields=kwargs)
    return json.loads(stream.getvalue().strip())


def test_a_record_matches_the_contract_shape():
    record = _emit(account="alice")
    assert set(record) >= {"ts", "level", "target", "message"}
    assert record["level"] == "info"
    assert record["target"] == "neuman.test"
    assert record["fields"]["account"] == "alice"


def test_a_reserved_logrecord_name_cannot_crash_the_emit():
    """v1 crashed at startup on exactly this."""

    record = _emit(created="yes", name="clash", module="x", args="y")
    assert record["fields"]["created"] == "yes"
    assert record["fields"]["name"] == "clash"
    assert record["target"] == "neuman.test"


def test_request_id_is_promoted_to_the_top_level():
    record = _emit(request_id="abc123")
    assert record["request_id"] == "abc123"
    assert "request_id" not in record.get("fields", {})


def test_non_scalar_fields_are_stringified_not_dropped():
    record = _emit(payload={"nested": True})
    assert isinstance(record["fields"]["payload"], str)


def test_timestamps_are_utc_with_a_trailing_z():
    assert _emit()["ts"].endswith("Z")


def test_critical_folds_into_error():
    stream = io.StringIO()
    configure_logging(logging.DEBUG, stream)
    logging.getLogger("neuman.test").critical("boom")
    assert json.loads(stream.getvalue().strip())["level"] == "error"


# -- overlay ----------------------------------------------------------------


def test_write_then_read_round_trips(tmp_path):
    write_overlay(tmp_path, "alice", {"tools": ["fs_read"]})
    assert read_overlay(tmp_path, "alice") == {"tools": ["fs_read"]}


def test_a_written_overlay_is_group_readable(tmp_path):
    """mkstemp yields 0600, which hides the file from operators."""

    path = write_overlay(tmp_path, "alice", {})
    assert oct(path.stat().st_mode)[-3:] == "640"


def test_a_missing_overlay_reads_as_none(tmp_path):
    assert read_overlay(tmp_path, "nobody") is None


def test_a_malformed_overlay_raises(tmp_path):
    """'No overlay' and 'an overlay we cannot parse' are different facts."""

    (tmp_path / "alice.json").write_text("{not json")
    with pytest.raises(OverlayError):
        read_overlay(tmp_path, "alice")


def test_a_name_cannot_escape_the_directory(tmp_path):
    with pytest.raises(OverlayError, match="invalid overlay name"):
        write_overlay(tmp_path, "../../etc/passwd", {})


def test_no_temp_files_survive_a_write(tmp_path):
    write_overlay(tmp_path, "alice", {"tools": []})
    assert [path.name for path in tmp_path.iterdir()] == ["alice.json"]


def test_delete_reports_whether_one_existed(tmp_path):
    write_overlay(tmp_path, "alice", {})
    assert delete_overlay(tmp_path, "alice") is True
    assert delete_overlay(tmp_path, "alice") is False


def test_listing_is_sorted(tmp_path):
    for name in ("carol", "alice", "bob"):
        write_overlay(tmp_path, name, {})
    assert list_overlays(tmp_path) == ["alice", "bob", "carol"]


# -- migrations -------------------------------------------------------------


def test_migrations_are_discovered_and_ordered():
    found = migrations()
    assert found
    assert [item.version for item in found] == sorted(item.version for item in found)


def test_latest_version_is_derived_not_hardcoded():
    """So adding a migration never requires editing an assertion."""

    assert latest_version() == migrations()[-1].version


def test_a_migration_checksum_is_stable():
    first, second = migrations()[0], migrations()[0]
    assert first.checksum == second.checksum


async def test_an_edited_applied_migration_is_a_hard_error():
    """An applied migration is immutable: a changed file is refused (D103)."""

    class Conn:
        def __init__(self):
            self.executed = []

        async def execute(self, sql, params=None):
            self.executed.append(sql)
            return self

        async def fetchall(self):
            # Report every migration as applied with a wrong checksum.
            return [{"version": item.version, "checksum": "wrong"} for item in migrations()]

        async def fetchone(self):
            return {"version": latest_version()}

    with pytest.raises(
        MigrationError,
        match=r"has changed since it was applied.*Restore from backup, or start over",
    ):
        await migrate(Conn())


def test_the_reply_budget_is_configurable_and_validated():
    """`NEUMAN_MAX_TOKENS`, because the default was the only reachable value.

    Validated rather than trusted: a zero or negative budget would be accepted
    by the dataclass and then sent to a provider, which answers with an error
    naming its own parameter — a misconfiguration reported as a model failure.
    """

    import os
    from unittest import mock

    from neuman_server.foundation.config import ConfigError, Settings

    assert Settings().max_tokens == 4096

    with mock.patch.dict(os.environ, {"NEUMAN_MAX_TOKENS": "8192"}):
        assert Settings.from_env().max_tokens == 8192

    for bad in ("0", "-1"):
        with mock.patch.dict(os.environ, {"NEUMAN_MAX_TOKENS": bad}):
            try:
                Settings.from_env()
            except ConfigError:
                continue
            raise AssertionError(f"{bad} was accepted as a reply budget")
