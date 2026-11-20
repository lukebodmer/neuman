"""Backup and restore: the round trip, and the refusals before it starts.

`decision/ops/backup.py` has said from the beginning that *a backup nobody has
restored is a hypothesis*, and until `restore.py` existed that was a fair
description of the appliance's recovery story: a `pg_dump` that had never been
fed to `pg_restore` is an assertion about a file format.

So the test that carries weight here is the round trip — back up a populated
appliance, wipe it, restore, and check that what comes back is what went in.
The rest are refusals, and they matter for a specific reason: a restore that
fails *before* touching anything is recoverable, and one that fails halfway
leaves the appliance in a state neither backup describes. Every check that can
happen before the first write is tested to happen before the first write.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes import FakeAudit, FakeDirectory

from neuman_server.decision.audit import chain
from neuman_server.decision.ops import (
    MANIFEST_NAME,
    BackupError,
    RestoreError,
    create_backup,
    restore_backup,
)

OPERATOR = "root"


async def noop_load(dump: Path) -> None:
    """A database load that succeeds without a database.

    Named rather than a lambda because `lambda dump: None` returns None, which
    cannot be awaited — the first version of this file did that and turned
    four chain assertions into identical RestoreErrors.
    """


@pytest.fixture
def directory():
    return FakeDirectory(
        groups={"root": {"neuman-admin"}, "alice": {"eng"}},
    )


@pytest.fixture
def appliance(tmp_path):
    """The three things a backup covers, populated."""

    overlay_dir = tmp_path / "state" / "policies"
    overlay_dir.mkdir(parents=True)
    (overlay_dir / "alice.json").write_text(json.dumps({"auto_accept": "never"}))
    (overlay_dir / "bob.json").write_text(json.dumps({"tools": ["fs_read"]}))

    mirror = tmp_path / "log" / "audit.jsonl"
    mirror.parent.mkdir(parents=True)
    mirror.write_text('{"seq": 1, "action": "identity.login"}\n')

    return {"overlay_dir": overlay_dir, "mirror": mirror, "root": tmp_path}


async def make_backup(appliance, directory, audit, destination, *, dump_body=b"DUMP"):
    async def run_dump(target: Path) -> Path:
        output = target / "neuman.dump"
        output.write_bytes(dump_body)
        return output

    return await create_backup(
        destination,
        actor=OPERATOR,
        directory=directory,
        audit=audit,
        run_dump=run_dump,
        audit_mirror=appliance["mirror"],
        overlay_dir=appliance["overlay_dir"],
    )


# -- backup -----------------------------------------------------------------


async def test_a_backup_writes_its_own_manifest(appliance, directory, tmp_path):
    """Without it, a restore reverse-engineers a directory layout."""

    destination = tmp_path / "backup"
    await make_backup(appliance, directory, FakeAudit(), destination)

    manifest = json.loads((destination / MANIFEST_NAME).read_text())
    assert manifest["overlay_files"] == ["alice.json", "bob.json"]
    assert manifest["database_dump"].endswith("neuman.dump")


async def test_a_backup_is_audited(appliance, directory, tmp_path):
    audit = FakeAudit()
    await make_backup(appliance, directory, audit, tmp_path / "backup")

    assert [row.action for row in audit.rows] == ["ops.backup"]
    assert audit.rows[0].actor == OPERATOR


async def test_a_destination_that_already_holds_a_backup_is_refused(
    appliance, directory, tmp_path
):
    """A retry must not write over the dump the first attempt left running.

    The operator's view of a slow backup was a timeout, so the natural retry
    was the same command again — and two `pg_dump`s into one file both exit 0
    and leave something that is neither backup (N166). Refusing before the
    first write is what makes the retry safe to attempt.
    """

    destination = tmp_path / "backup"
    await make_backup(appliance, directory, FakeAudit(), destination)

    with pytest.raises(BackupError):
        await make_backup(appliance, directory, FakeAudit(), destination, dump_body=b"SECOND")

    assert (destination / "neuman.dump").read_bytes() == b"DUMP", (
        "the refused attempt wrote something anyway"
    )
    manifest = json.loads((destination / MANIFEST_NAME).read_text())
    assert manifest["destination"].endswith("backup")


async def test_a_non_operator_cannot_back_up(appliance, directory, tmp_path):
    from neuman_server.decision.identity.authz import Denied

    with pytest.raises(Denied):
        await create_backup(
            tmp_path / "backup",
            actor="alice",
            directory=directory,
            audit=FakeAudit(),
            run_dump=noop_load,
            audit_mirror=appliance["mirror"],
            overlay_dir=appliance["overlay_dir"],
        )


# -- the round trip ---------------------------------------------------------


async def test_a_backup_restores_what_it_backed_up(appliance, directory, tmp_path):
    """The assertion the whole module exists for.

    Back up, destroy all three artifacts, restore, and compare. Anything less
    than destroying them first would let a restore that copies nothing pass.
    """

    destination = tmp_path / "backup"
    await make_backup(appliance, directory, FakeAudit(), destination)

    # Destroy the live state.
    for path in appliance["overlay_dir"].glob("*.json"):
        path.unlink()
    appliance["mirror"].unlink()
    assert not list(appliance["overlay_dir"].glob("*.json"))

    loaded: list[Path] = []

    async def run_load(dump: Path) -> None:
        loaded.append(dump)

    audit = FakeAudit()
    report = await restore_backup(
        destination,
        actor=OPERATOR,
        directory=directory,
        audit=audit,
        run_load=run_load,
        overlay_dir=appliance["overlay_dir"],
        audit_mirror=appliance["mirror"],
    )

    assert report.database_loaded
    assert loaded and loaded[0].name == "neuman.dump"
    assert sorted(report.overlay_files) == ["alice.json", "bob.json"]

    # The contents, not just the filenames: a restore that creates empty files
    # would satisfy every assertion above.
    restored = json.loads((appliance["overlay_dir"] / "alice.json").read_text())
    assert restored == {"auto_accept": "never"}
    assert appliance["mirror"].exists()


async def test_the_overlay_survives_a_failed_database_restore(appliance, directory, tmp_path):
    """The ordering, asserted rather than assumed.

    The overlay is restored first precisely so that a database load which
    fails does not also lose the runtime policy — "losing runtime policy to a
    restore is how an appliance silently reverts an operator's revocation"
    (`foundation/state/overlay.py`).
    """

    destination = tmp_path / "backup"
    await make_backup(appliance, directory, FakeAudit(), destination)
    for path in appliance["overlay_dir"].glob("*.json"):
        path.unlink()

    async def failing_load(dump: Path) -> None:
        raise RuntimeError("pg_restore exploded")

    with pytest.raises(RestoreError, match="pg_restore exploded"):
        await restore_backup(
            destination,
            actor=OPERATOR,
            directory=directory,
            audit=FakeAudit(),
            run_load=failing_load,
            overlay_dir=appliance["overlay_dir"],
            audit_mirror=appliance["mirror"],
        )

    assert (appliance["overlay_dir"] / "alice.json").exists(), (
        "the overlay must be in place before the database load is attempted"
    )


async def test_a_restore_verifies_the_chain(appliance, directory, tmp_path):
    """Verification happens here, not as a step a caller can skip.

    The failure that matters is not a restore that crashes — it is one that
    appears to work and silently drops or reorders governance events.
    """

    destination = tmp_path / "backup"
    await make_backup(appliance, directory, FakeAudit(), destination)

    audit = FakeAudit()
    await chain.append(audit, actor="alice", action="identity.login", payload={})
    await chain.append(audit, actor=OPERATOR, action="policy.set", payload={})

    report = await restore_backup(
        destination,
        actor=OPERATOR,
        directory=directory,
        audit=audit,
        run_load=noop_load,
        overlay_dir=appliance["overlay_dir"],
        audit_mirror=appliance["mirror"],
    )

    assert report.chain_ok
    assert report.chain_checked == 2, report.chain_checked


async def test_a_broken_chain_is_reported_not_hidden(appliance, directory, tmp_path):
    """A tampered record must not restore quietly."""

    destination = tmp_path / "backup"
    await make_backup(appliance, directory, FakeAudit(), destination)

    audit = FakeAudit()
    await chain.append(audit, actor="alice", action="identity.login", payload={})
    await chain.append(audit, actor="alice", action="identity.logout", payload={})
    # Replace a row's payload without recomputing the hashes — what an edit
    # against the database directly would look like, and exactly what the
    # chain exists to notice. The row is frozen, so this substitutes a new one
    # rather than mutating, which is also closer to an UPDATE.
    import dataclasses

    audit.rows[0] = dataclasses.replace(audit.rows[0], payload={"tampered": True})

    report = await restore_backup(
        destination,
        actor=OPERATOR,
        directory=directory,
        audit=audit,
        run_load=noop_load,
        overlay_dir=appliance["overlay_dir"],
        audit_mirror=appliance["mirror"],
    )

    assert not report.chain_ok
    assert report.chain_first_bad_seq == 1, report.chain_first_bad_seq


async def test_a_restore_is_audited(appliance, directory, tmp_path):
    destination = tmp_path / "backup"
    await make_backup(appliance, directory, FakeAudit(), destination)

    audit = FakeAudit()
    await restore_backup(
        destination,
        actor=OPERATOR,
        directory=directory,
        audit=audit,
        run_load=noop_load,
        overlay_dir=appliance["overlay_dir"],
        audit_mirror=appliance["mirror"],
    )

    assert [row.action for row in audit.rows] == ["ops.restore"]


async def test_the_restore_event_is_appended_after_verification(appliance, directory, tmp_path):
    """So the chain that was checked is the restored one.

    Appending first would mean the verification counts a row this very call
    added, which is a different claim from "what was restored is intact".
    """

    destination = tmp_path / "backup"
    await make_backup(appliance, directory, FakeAudit(), destination)

    audit = FakeAudit()
    await chain.append(audit, actor="alice", action="identity.login", payload={})

    report = await restore_backup(
        destination,
        actor=OPERATOR,
        directory=directory,
        audit=audit,
        run_load=noop_load,
        overlay_dir=appliance["overlay_dir"],
        audit_mirror=appliance["mirror"],
    )

    assert report.chain_checked == 1, "the restore's own event was counted"
    assert len(audit.rows) == 2


# -- refusals, all of them before the first write ---------------------------


async def test_a_directory_that_is_not_a_backup_is_refused(directory, tmp_path, appliance):
    plain = tmp_path / "just-a-directory"
    plain.mkdir()

    with pytest.raises(RestoreError, match="missing"):
        await restore_backup(
            plain,
            actor=OPERATOR,
            directory=directory,
            audit=FakeAudit(),
            run_load=noop_load,
            overlay_dir=appliance["overlay_dir"],
            audit_mirror=appliance["mirror"],
        )


async def test_a_missing_source_is_refused(directory, tmp_path, appliance):
    with pytest.raises(RestoreError, match="not a directory"):
        await restore_backup(
            tmp_path / "nowhere",
            actor=OPERATOR,
            directory=directory,
            audit=FakeAudit(),
            run_load=noop_load,
            overlay_dir=appliance["overlay_dir"],
            audit_mirror=appliance["mirror"],
        )


async def test_a_truncated_backup_is_refused_before_anything_is_written(
    appliance, directory, tmp_path
):
    """A manifest naming files that are not there means an incomplete copy.

    Refusing is the only safe answer: restoring the half that survived would
    produce an appliance whose policy and database disagree, with nothing
    recording that it happened.
    """

    destination = tmp_path / "backup"
    await make_backup(appliance, directory, FakeAudit(), destination)
    (destination / "neuman.dump").unlink()

    for path in appliance["overlay_dir"].glob("*.json"):
        path.unlink()

    with pytest.raises(RestoreError, match="not present"):
        await restore_backup(
            destination,
            actor=OPERATOR,
            directory=directory,
            audit=FakeAudit(),
            run_load=noop_load,
            overlay_dir=appliance["overlay_dir"],
            audit_mirror=appliance["mirror"],
        )

    assert not list(appliance["overlay_dir"].glob("*.json")), (
        "nothing may be written when the backup is refused"
    )


async def test_a_missing_overlay_file_is_refused(appliance, directory, tmp_path):
    destination = tmp_path / "backup"
    await make_backup(appliance, directory, FakeAudit(), destination)
    (destination / "policies" / "alice.json").unlink()

    with pytest.raises(RestoreError, match="missing"):
        await restore_backup(
            destination,
            actor=OPERATOR,
            directory=directory,
            audit=FakeAudit(),
            run_load=noop_load,
            overlay_dir=appliance["overlay_dir"],
            audit_mirror=appliance["mirror"],
        )


async def test_an_unreadable_manifest_is_refused(appliance, directory, tmp_path):
    destination = tmp_path / "backup"
    await make_backup(appliance, directory, FakeAudit(), destination)
    (destination / MANIFEST_NAME).write_text("{not json")

    with pytest.raises(RestoreError, match="unreadable"):
        await restore_backup(
            destination,
            actor=OPERATOR,
            directory=directory,
            audit=FakeAudit(),
            run_load=noop_load,
            overlay_dir=appliance["overlay_dir"],
            audit_mirror=appliance["mirror"],
        )


async def test_a_non_operator_cannot_restore(appliance, directory, tmp_path):
    """Checked before the manifest is even read."""

    from neuman_server.decision.identity.authz import Denied

    destination = tmp_path / "backup"
    await make_backup(appliance, directory, FakeAudit(), destination)

    with pytest.raises(Denied):
        await restore_backup(
            destination,
            actor="alice",
            directory=directory,
            audit=FakeAudit(),
            run_load=noop_load,
            overlay_dir=appliance["overlay_dir"],
            audit_mirror=appliance["mirror"],
        )
