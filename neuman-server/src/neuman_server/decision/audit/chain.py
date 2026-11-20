"""The hash-chained governance record.

Each row commits to its predecessor::

    hash = SHA256(prev_hash ‖ canonical(row))

with the first row chaining from 64 zeros. Tampering is not *prevented* — root
owns the machine and can edit the database — but it is **evident**: altering or
removing a row breaks every hash after it, and :func:`verify` names the first
bad sequence number.

The database row is the transaction; the JSONL mirror on local disk is the
durable tamper-evident artifact (docs/decisions.md D18), because a mirror on a different
medium is what makes "the database was edited" detectable at all. A mirror write
that fails is logged and does **not** fail the append: losing the audit record
of an action entirely is worse than losing its off-database copy.

Audit events are emitted by core operations, never by a transport surface
(docs/decisions.md D8) — a surface cannot bypass the chain, because it has no
way to reach the audit or policy stores except through an operation that
appends.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ...foundation.logging import get_logger
from .ports import AuditRepository, AuditRow

__all__ = [
    "GENESIS",
    "Appended",
    "Verification",
    "append",
    "canonical",
    "digest_for",
    "verify",
]

_log = get_logger("neuman.server.audit")

GENESIS = "0" * 64


@dataclass(frozen=True, slots=True)
class Appended:
    seq: int
    hash: str
    ts: datetime


def canonical(actor: str | None, action: str, payload: dict[str, Any]) -> str:
    """The stable serialization the hash commits to.

    Deliberately excludes the timestamp and sequence number: both are assigned
    by the database, and a hash over server-assigned values cannot be recomputed
    from an exported row. What is committed to is *who did what*.
    """

    return json.dumps(
        {"actor": actor, "action": action, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
    )


def digest_for(prev_hash: str, actor: str | None, action: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        (prev_hash + canonical(actor, action, payload)).encode("utf-8")
    ).hexdigest()


def _document(actor: str | None, action: str, payload: dict[str, Any]) -> str:
    """The text the derived search view indexes."""

    parts = [action, actor or ""]
    for key, value in sorted(payload.items()):
        parts.append(f"{key}:{value}" if not isinstance(value, (dict, list)) else key)
    return " ".join(part for part in parts if part)


async def append(
    repository: AuditRepository,
    *,
    actor: str | None,
    action: str,
    payload: dict[str, Any] | None = None,
    mirror: str | Path | None = None,
) -> Appended:
    """Append one governance event and return its position in the chain."""

    body = payload or {}
    head = await repository.head()
    prev_hash = head.hash if head is not None else GENESIS
    digest = digest_for(prev_hash, actor, action, body)

    row = await repository.append(
        actor=actor,
        action=action,
        payload=body,
        prev_hash=prev_hash,
        digest=digest,
        document=_document(actor, action, body),
    )

    if mirror is not None:
        _mirror(mirror, row)

    return Appended(seq=row.seq, hash=row.hash, ts=row.ts)


def _mirror(path: str | Path, row: AuditRow) -> None:
    """Append one row to the on-disk JSONL mirror. Fails soft."""

    entry = json.dumps(
        {
            "seq": row.seq,
            "ts": row.ts.isoformat(),
            "actor": row.actor,
            "action": row.action,
            "payload": row.payload,
            "prev_hash": row.prev_hash,
            "hash": row.hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        with file.open("a", encoding="utf-8") as handle:
            handle.write(entry + "\n")
            handle.flush()
    except OSError as exc:
        # The chain row is already committed; losing the mirror is a degraded
        # integrity anchor, not a lost event.
        _log.warning("audit mirror write failed", fields={"error": str(exc), "seq": row.seq})


@dataclass(frozen=True, slots=True)
class Verification:
    ok: bool
    checked: int
    first_bad_seq: int | None = None
    detail: str = ""


async def verify(repository: AuditRepository, *, batch: int = 1000) -> Verification:
    """Walk the chain and confirm every link. Names the first bad row."""

    prev_hash = GENESIS
    checked = 0
    after = 0

    while True:
        rows = await repository.read(after=after, limit=batch)
        if not rows:
            break
        for row in rows:
            if row.prev_hash != prev_hash:
                return Verification(
                    False,
                    checked,
                    row.seq,
                    f"row {row.seq} chains from {row.prev_hash[:12]}…, expected {prev_hash[:12]}…",
                )
            expected = digest_for(prev_hash, row.actor, row.action, row.payload)
            if row.hash != expected:
                return Verification(
                    False, checked, row.seq, f"row {row.seq} hash does not match its contents"
                )
            prev_hash = row.hash
            checked += 1
            after = row.seq

    return Verification(True, checked)
