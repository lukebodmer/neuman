"""Backup and service health for operators."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...adapters.persistence import AuditStore
from ...decision.identity.authz import Denied
from ...decision.ops import (
    BackupError,
    RestoreError,
    create_backup,
    restore_backup,
)
from ...foundation import get_logger
from ...foundation.db import latest_version, schema_version
from .deps import admin_actor, connection, services_of

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request
    from starlette.responses import Response

__all__ = ["admin_system_routes"]

_log = get_logger("neuman.server.admin.system")


def admin_system_routes() -> list[Any]:
    from starlette.routing import Route

    return [
        Route("/admin/status", _status, methods=["GET"]),
        Route("/admin/backup", _backup, methods=["POST"]),
        Route("/admin/restore", _restore, methods=["POST"]),
    ]


async def _status(request: Request) -> Response:
    """What this appliance *is*, before what it holds.

    Identity rather than inventory. The build is the one stamped into every
    turn's audit entry, so the card and the chain answer "which code served
    this" with the same value. The schema is a pair rather than one number,
    because a migration that did not apply is visible as a disagreement instead
    of needing the expected version held in the reader's head.

    Deliberately not here: how many tools the registry holds. That number cannot
    change without a restart, counts diagnostics no user can see, and says
    nothing about what any account may call — gating is decided per dispatch.
    """

    from starlette.responses import JSONResponse

    services = services_of(request)
    settings = services.settings

    async with connection(request) as conn:
        version = await schema_version(conn)

    return JSONResponse(
        {
            "build": settings.build_id,
            "started_at": services.started_at.isoformat() if services.started_at else None,
            "uptime_s": (
                time.monotonic() - services.started_mono if services.started_mono else None
            ),
            "bind": settings.bind,
            "port": settings.port,
            "tls": settings.tls_enabled,
            "schema": {"applied": version, "expected": latest_version()},
            # Queues held, not sessions live: an inbox is created when a socket
            # connects and dropped only when the session is closed or removed, so
            # this counts memory retained, not what is connected now
            # (docs/gotchas.md N104 read the old label as a leak signature for
            # exactly this reason).
            "session_queues": len(services.inboxes),
        }
    )


async def _backup(request: Request) -> Response:
    from starlette.responses import JSONResponse

    services = services_of(request)
    settings = services.settings

    try:
        body = await request.json()
    except ValueError:
        body = {}
    destination = body.get("destination")
    if not isinstance(destination, str) or not destination:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    async def run_dump(target: Path) -> Path:
        """`pg_dump` into the backup directory.

        Shelling out lives here rather than in the decision layer, which is
        handed this as a callable.
        """

        output = target / "neuman.dump"
        process = await asyncio.create_subprocess_exec(
            "pg_dump",
            "--format=custom",
            f"--file={output}",
            settings.database_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"pg_dump failed: {stderr.decode('utf-8', errors='replace')}")
        return output

    async with connection(request) as conn:
        try:
            manifest = await create_backup(
                destination,
                actor=admin_actor(request),
                directory=services.directory,
                audit=AuditStore(conn),
                run_dump=run_dump,
                audit_mirror=settings.audit_mirror_path,
                overlay_dir=settings.overlay_dir,
                admin_policy=services.admin_policy,
            )
        except Denied as exc:
            return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)
        except BackupError as exc:
            # 409, not 500: the destination is not something this route will
            # write over, and the operator can fix it by naming another one.
            return JSONResponse(
                {"error": "destination_in_use", "detail": str(exc)}, status_code=409
            )
        except (OSError, RuntimeError) as exc:
            return JSONResponse({"error": "backup_failed", "detail": str(exc)}, status_code=500)

    return JSONResponse(manifest.to_json())


async def _restore(request: Request) -> Response:
    """Load a backup written by `create_backup`.

    Destructive and deliberately not reachable from the session surface: it is
    on `/admin`, which only the socket serves in a correct deployment.
    """

    from starlette.responses import JSONResponse

    services = services_of(request)
    settings = services.settings

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    source = body.get("source") if isinstance(body, dict) else None
    if not isinstance(source, str) or not source:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    async def run_load(dump: Path) -> None:
        """`pg_restore` over the live database.

        `--clean --if-exists` because a restore replaces rather than merges
        (see `decision/ops/restore.py`), and without `--if-exists` the drops
        fail on a fresh database and take the whole restore with them.

        `--exit-on-error` because pg_restore's default is to continue past
        failures and exit 0, which would report a successful restore of a
        database that is missing tables.
        """

        process = await asyncio.create_subprocess_exec(
            "pg_restore",
            "--clean",
            "--if-exists",
            "--exit-on-error",
            "--no-owner",
            f"--dbname={settings.database_url}",
            str(dump),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"pg_restore failed: {stderr.decode('utf-8', errors='replace')}")

    async with connection(request) as conn:
        try:
            report = await restore_backup(
                source,
                actor=admin_actor(request),
                directory=services.directory,
                audit=AuditStore(conn),
                run_load=run_load,
                overlay_dir=settings.overlay_dir,
                audit_mirror=settings.audit_mirror_path,
                admin_policy=services.admin_policy,
            )
        except Denied as exc:
            return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)
        except RestoreError as exc:
            # 400, not 500: the backup is not one we can read, which is a
            # problem with what was asked for rather than with the appliance.
            return JSONResponse(
                {"error": "restore_failed", "detail": str(exc)}, status_code=400
            )
        except (OSError, RuntimeError) as exc:
            return JSONResponse(
                {"error": "restore_failed", "detail": str(exc)}, status_code=500
            )

    payload = report.to_json()
    # A restore that loaded cleanly but produced a broken chain is not a
    # success, and must not answer 200 with `"chain_ok": false` buried in the
    # body where a script will not look.
    status = 200 if report.chain_ok else 500
    return JSONResponse(payload, status_code=status)
