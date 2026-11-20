"""Backup orchestration: the database, the audit mirror, the policy overlay.

There is exactly one database on the appliance (docs/decisions.md D18), so a backup is three
things and no more:

* ``pg_dump`` of the core database;
* the audit JSONL mirror — the durable tamper-evident artifact, which is what
  makes "the database was edited" detectable after a restore;
* the policy overlay — the runtime-authoring surface, which *must* survive a
  wiped database or an operator's revocation silently reverts.

v1's backup covered its own store and left the gateway's Postgres dump as a
separate documented operator step; with the gateway owned in our
own tables that split is gone.

Restore rehearses both halves: a backup nobody has restored is a hypothesis.
This module orchestrates and audits; the actual `pg_dump` invocation is an
adapter concern injected as ``run_dump``, so tests never shell out.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...foundation.logging import get_logger
from ..audit import chain
from ..audit.ports import AuditRepository
from ..identity.authz import AdminPolicy, require_admin
from ..policy.ports import IdentityDirectory

__all__ = ["MANIFEST_NAME", "BackupError", "BackupManifest", "create_backup"]

# The manifest's filename inside a backup directory. Named here because
# `restore.py` reads it and the two must not disagree.
MANIFEST_NAME = "manifest.json"

_log = get_logger("neuman.server.ops.backup")

# (destination directory) -> path of the written dump.
DumpRunner = Callable[[Path], Awaitable[Path]]


class BackupError(Exception):
    """The backup cannot be written, and nothing has been changed."""


@dataclass(frozen=True, slots=True)
class BackupManifest:
    created_at: datetime
    destination: Path
    database_dump: Path | None
    audit_mirror: Path | None
    overlay_files: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at.isoformat(),
            "destination": str(self.destination),
            "database_dump": str(self.database_dump) if self.database_dump else None,
            "audit_mirror": str(self.audit_mirror) if self.audit_mirror else None,
            "overlay_files": list(self.overlay_files),
        }


async def create_backup(
    destination: str | Path,
    *,
    actor: str,
    directory: IdentityDirectory,
    audit: AuditRepository,
    run_dump: DumpRunner,
    audit_mirror: str | Path | None,
    overlay_dir: str | Path,
    admin_policy: AdminPolicy | None = None,
    now: datetime | None = None,
) -> BackupManifest:
    """Write a complete backup to ``destination``."""

    await require_admin(actor, directory=directory, policy=admin_policy)

    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    moment = now or datetime.now(UTC)

    # A destination with anything in it is refused rather than written over.
    # `pg_dump` into an existing directory is how a retry races the dump the
    # first attempt left running: the operator sees a timeout, runs the same
    # command again, and two processes write one file (N166). A half-written
    # directory is recognisable by the absence of a manifest, so refusing
    # everything present — not only a dump — keeps the rule one sentence.
    if any(target.iterdir()):
        raise BackupError(
            f"{target} already holds something; choose an empty destination or remove it"
        )

    dump = await run_dump(target)

    mirror_copy: Path | None = None
    if audit_mirror is not None:
        source = Path(audit_mirror)
        if source.exists():
            mirror_copy = target / source.name
            shutil.copy2(source, mirror_copy)

    overlay_target = target / "policies"
    overlay_source = Path(overlay_dir)
    copied: list[str] = []
    if overlay_source.is_dir():
        overlay_target.mkdir(parents=True, exist_ok=True)
        for path in sorted(overlay_source.glob("*.json")):
            shutil.copy2(path, overlay_target / path.name)
            copied.append(path.name)

    manifest = BackupManifest(
        created_at=moment,
        destination=target,
        database_dump=dump,
        audit_mirror=mirror_copy,
        overlay_files=tuple(copied),
    )

    # Written into the backup itself, not only returned. A restore that has to
    # infer the layout from filenames is a restore that breaks the first time
    # a filename changes — and the manifest is also the only record of what
    # the backup *intended* to contain, which is how a truncated copy is
    # distinguished from a backup taken before a file existed.
    (target / MANIFEST_NAME).write_text(json.dumps(manifest.to_json(), indent=2))

    await chain.append(
        audit,
        actor=actor,
        action="ops.backup",
        payload=manifest.to_json(),
        mirror=audit_mirror,
    )
    _log.info(
        "backup written",
        fields={"destination": str(target), "overlays": len(copied), "actor": actor},
    )
    return manifest
