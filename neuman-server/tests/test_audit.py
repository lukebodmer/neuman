"""The hash chain: append, verify, tamper detection, and the disk mirror."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from fakes import FakeAudit, FakeAuditRow

from neuman_server.decision.audit import chain, purge_audit


async def test_the_first_row_chains_from_genesis():
    audit = FakeAudit()
    await chain.append(audit, actor="root", action="test", payload={})
    assert audit.rows[0].prev_hash == chain.GENESIS


async def test_each_row_commits_to_its_predecessor():
    audit = FakeAudit()
    first = await chain.append(audit, actor="root", action="one", payload={})
    await chain.append(audit, actor="root", action="two", payload={})
    assert audit.rows[1].prev_hash == first.hash


async def test_a_clean_chain_verifies():
    audit = FakeAudit()
    for index in range(5):
        await chain.append(audit, actor="root", action=f"action{index}", payload={"i": index})

    result = await chain.verify(audit)
    assert result.ok
    assert result.checked == 5


async def test_an_edited_payload_breaks_verification():
    """Tampering is not prevented — root owns the machine — but it is evident."""

    audit = FakeAudit()
    for index in range(3):
        await chain.append(audit, actor="root", action="act", payload={"i": index})

    tampered = audit.rows[1]
    audit.rows[1] = FakeAuditRow(
        seq=tampered.seq,
        ts=tampered.ts,
        actor=tampered.actor,
        action=tampered.action,
        payload={"i": 99},
        prev_hash=tampered.prev_hash,
        hash=tampered.hash,
    )

    result = await chain.verify(audit)
    assert not result.ok
    assert result.first_bad_seq == 2


async def test_a_removed_row_breaks_verification():
    audit = FakeAudit()
    for index in range(4):
        await chain.append(audit, actor="root", action="act", payload={"i": index})

    del audit.rows[1]
    result = await chain.verify(audit)
    assert not result.ok


async def test_the_hash_is_reproducible_from_the_stored_row():
    """A hash over server-assigned values could not be recomputed from an export."""

    audit = FakeAudit()
    await chain.append(audit, actor="alice", action="policy.set", payload={"x": 1})
    row = audit.rows[0]

    assert row.hash == chain.digest_for(row.prev_hash, row.actor, row.action, row.payload)


async def test_canonical_form_is_order_independent():
    left = chain.canonical("a", "act", {"x": 1, "y": 2})
    right = chain.canonical("a", "act", {"y": 2, "x": 1})
    assert left == right


# -- the disk mirror --------------------------------------------------------


async def test_rows_are_mirrored_to_disk(tmp_path):
    mirror = tmp_path / "audit.jsonl"
    audit = FakeAudit()

    await chain.append(audit, actor="alice", action="one", payload={}, mirror=mirror)
    await chain.append(audit, actor="alice", action="two", payload={}, mirror=mirror)

    lines = mirror.read_text().strip().split("\n")
    assert [json.loads(line)["action"] for line in lines] == ["one", "two"]


async def test_a_failed_mirror_write_does_not_fail_the_append(tmp_path):
    """Losing the off-database copy beats losing the event."""

    unwritable = tmp_path / "nope"
    unwritable.write_text("not a directory")
    audit = FakeAudit()

    appended = await chain.append(
        audit, actor="alice", action="act", payload={}, mirror=unwritable / "audit.jsonl"
    )

    assert appended.seq == 1
    assert len(audit.rows) == 1


# -- retention --------------------------------------------------------------


async def test_retention_of_zero_keeps_everything():
    """Deleting a governance record because nobody configured a policy is wrong."""

    audit = FakeAudit()
    await chain.append(audit, actor="a", action="act", payload={})

    result = await purge_audit(audit, retention_days=0)
    assert result.removed == 0
    assert len(audit.rows) == 1


async def test_retention_purges_rows_outside_the_window():
    audit = FakeAudit()
    await chain.append(audit, actor="a", action="old", payload={})
    audit.rows[0] = FakeAuditRow(
        seq=1,
        ts=datetime.now(UTC) - timedelta(days=90),
        actor="a",
        action="old",
        payload={},
        prev_hash=chain.GENESIS,
        hash=audit.rows[0].hash,
    )
    await chain.append(audit, actor="a", action="new", payload={})

    result = await purge_audit(audit, retention_days=30)
    assert result.removed == 1
    assert [row.action for row in audit.rows] == ["new"]
