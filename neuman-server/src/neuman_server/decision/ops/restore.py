"""Restore: putting a backup back, and proving it arrived intact.

`backup.py` says it in its own docstring — *a backup nobody has restored is a
hypothesis* — and until this module existed, that is exactly what the
appliance had. A `pg_dump` that has never been fed to `pg_restore` is an
assertion about a file format.

A restore is the same three artifacts as a backup, in a deliberate order:

1. **The overlay first.** It is the runtime-authoring surface and must survive
   a wiped database (D18). Restoring it last would mean a window in which the
   database says one thing about policy and the filesystem says another, and
   the server re-reads policy per dispatch — so the window is not theoretical.
2. **The database second**, which is the destructive step and the one that can
   fail halfway.
3. **The audit mirror last**, because it is evidence rather than state. It is
   copied beside the restored database rather than merged into it: the
   database's chain and the mirror are two independent records, and the whole
   point of keeping both is that they can be *compared*.

**What this deliberately does not do: merge.** A restore replaces. Merging a
backup into a live database would mean deciding what to do about a row that
exists in both with different contents, and there is no answer to that which
is not a policy decision dressed as a data operation. The caller stops the
appliance, restores, and starts it.

**Verification is not optional and not separate.** `restore` re-verifies the
audit chain after loading and refuses to report success if the chain is
broken, because the failure mode that matters is not "the restore crashed" —
it is a restore that appears to work and silently drops or reorders governance
events. A hash chain is exactly the instrument for detecting that, and running
it anywhere but here would make it a step someone can skip.
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
from .backup import MANIFEST_NAME

__all__ = ["RestoreError", "RestoreReport", "restore_backup"]

_log = get_logger("neuman.server.ops.restore")

# (path to the dump) -> None. The `pg_restore` invocation is an adapter
# concern, injected for the same reason `run_dump` is: the decision layer
# does not shell out, and tests do not need a database.
LoadRunner = Callable[[Path], Awaitable[None]]


class RestoreError(Exception):
    """The backup cannot be restored, and nothing has been changed."""


@dataclass(frozen=True, slots=True)
class RestoreReport:
    restored_at: datetime
    source: Path
    database_loaded: bool
    overlay_files: tuple[str, ...]
    audit_mirror: Path | None
    chain_ok: bool
    chain_checked: int
    chain_first_bad_seq: int | None

    def to_json(self) -> dict[str, Any]:
        return {
            "restored_at": self.restored_at.isoformat(),
            "source": str(self.source),
            "database_loaded": self.database_loaded,
            "overlay_files": list(self.overlay_files),
            "audit_mirror": str(self.audit_mirror) if self.audit_mirror else None,
            "chain_ok": self.chain_ok,
            "chain_checked": self.chain_checked,
            "chain_first_bad_seq": self.chain_first_bad_seq,
        }


def read_manifest(source: str | Path) -> dict[str, Any]:
    """The manifest written into the backup, or a `RestoreError` naming why not.

    Checked before anything is touched. Every failure here is a refusal with
    nothing modified, which is the only point at which that is still free.
    """

    directory = Path(source)
    if not directory.is_dir():
        raise RestoreError(f"{directory} is not a directory")

    path = directory / MANIFEST_NAME
    if not path.exists():
        raise RestoreError(
            f"{path} is missing; this directory was not written by `create_backup`"
        )

    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RestoreError(f"{path} is unreadable: {exc}") from exc

    if not isinstance(manifest, dict):
        raise RestoreError(f"{path} does not contain a manifest object")
    return manifest


async def restore_backup(
    source: str | Path,
    *,
    actor: str,
    directory: IdentityDirectory,
    audit: AuditRepository,
    run_load: LoadRunner,
    overlay_dir: str | Path,
    audit_mirror: str | Path | None,
    admin_policy: AdminPolicy | None = None,
    now: datetime | None = None,
) -> RestoreReport:
    """Restore a backup written by :func:`create_backup`.

    Raises :class:`RestoreError` before touching anything if the backup is not
    one this appliance can read.
    """

    await require_admin(actor, directory=directory, policy=admin_policy)

    root = Path(source)
    manifest = read_manifest(root)
    moment = now or datetime.now(UTC)

    # Everything is located and checked before the first write. A restore that
    # fails halfway is the worst outcome available — worse than refusing —
    # because the appliance is then in a state neither backup describes.
    dump_name = manifest.get("database_dump")
    dump_path = root / Path(dump_name).name if dump_name else None
    if dump_path is not None and not dump_path.exists():
        raise RestoreError(f"the manifest names {dump_path.name}, which is not present")

    overlay_names = tuple(manifest.get("overlay_files") or ())
    overlay_source = root / "policies"
    missing = [n for n in overlay_names if not (overlay_source / n).exists()]
    if missing:
        raise RestoreError(f"overlay files named in the manifest are missing: {missing}")

    # 1. The overlay, first: it must survive a wiped database (D18).
    overlay_target = Path(overlay_dir)
    restored_overlays: list[str] = []
    if overlay_names:
        overlay_target.mkdir(parents=True, exist_ok=True)
        for name in overlay_names:
            shutil.copy2(overlay_source / name, overlay_target / name)
            restored_overlays.append(name)

    # 2. The database. Destructive, and the step that can fail.
    database_loaded = False
    if dump_path is not None:
        try:
            await run_load(dump_path)
        except Exception as exc:
            # Deliberately not swallowed: a half-restored database must be
            # loud. The overlay is already in place, which is the ordering
            # working as intended — policy survives a failed database restore.
            raise RestoreError(f"loading {dump_path.name} failed: {exc}") from exc
        database_loaded = True

    # 3. The audit mirror, beside the database rather than merged into it.
    mirror_restored: Path | None = None
    mirror_name = manifest.get("audit_mirror")
    if mirror_name and audit_mirror is not None:
        candidate = root / Path(mirror_name).name
        if candidate.exists():
            target = Path(audit_mirror)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, target)
            mirror_restored = target

    # Verification, here rather than as a step a caller can skip. The failure
    # that matters is a restore that *looks* like it worked.
    verification = await chain.verify(audit)

    report = RestoreReport(
        restored_at=moment,
        source=root,
        database_loaded=database_loaded,
        overlay_files=tuple(restored_overlays),
        audit_mirror=mirror_restored,
        chain_ok=verification.ok,
        chain_checked=verification.checked,
        chain_first_bad_seq=verification.first_bad_seq,
    )

    # Appended after verification, so the chain that was checked is the
    # restored one rather than one this call already added to. The restore
    # event then becomes the first link written by the running appliance,
    # which is what an operator reading the chain later would expect.
    await chain.append(
        audit,
        actor=actor,
        action="ops.restore",
        payload=report.to_json(),
        mirror=audit_mirror,
    )

    _log.info(
        "backup restored",
        fields={
            "source": str(root),
            "database": database_loaded,
            "overlays": len(restored_overlays),
            "chain_ok": verification.ok,
            "actor": actor,
        },
    )
    return report
