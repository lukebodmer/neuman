"""Forward-only SQL migrations.

* Migrations are ``migrations/NNNN_name.sql``, applied in name order.
* Each migration runs in **one transaction**. Postgres DDL is transactional, so
  a failure leaves no partial schema and no helper machinery is needed — v1 had
  to hand-roll a statement splitter because ``sqlite3.executescript`` commits
  any open transaction first. That splitter is deleted; psycopg
  sends the script as-is inside an explicit transaction.
* Every applied migration's SHA-256 is recorded. Editing an applied file is a
  hard error, never papered over: the schema on disk no longer matches the
  schema in git, and two histories of one version number cannot be reconciled.
  ``0001_initial.sql`` froze at the first release (D103); the error names the
  way out, which is a restore from backup or starting over.
* Forward-only. A downgrade is a restore from backup.

Nothing outside this module runs DDL.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from ..logging import get_logger

__all__ = [
    "MigrationError",
    "applied",
    "latest_version",
    "migrate",
    "migrations",
    "schema_version",
]

_log = get_logger("neuman.server.db.migrate")

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    name        TEXT        NOT NULL,
    checksum    TEXT        NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    """A migration could not be applied, or an applied one was modified."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def migrations_dir() -> Path:
    """Location of the bundled migration files."""

    return Path(str(resources.files("neuman_server.foundation.db").joinpath("migrations")))


def migrations() -> list[Migration]:
    """Every bundled migration, in application order."""

    found: list[Migration] = []
    for path in sorted(migrations_dir().glob("*.sql")):
        stem = path.stem
        number, _, name = stem.partition("_")
        try:
            version = int(number)
        except ValueError as exc:
            raise MigrationError(f"migration file is not NNNN_name.sql: {path.name}") from exc
        found.append(Migration(version, name or stem, path.read_text(encoding="utf-8")))

    versions = [migration.version for migration in found]
    if len(set(versions)) != len(versions):
        raise MigrationError(f"duplicate migration versions: {versions}")
    return found


def latest_version() -> int:
    """The version a fully migrated database reaches.

    Tests derive their expectation from this rather than hard-coding a number,
    so adding a migration does not require editing assertions.
    """

    found = migrations()
    return found[-1].version if found else 0


async def schema_version(conn: Any) -> int:
    """The highest applied version, or 0 on a fresh database."""

    await conn.execute(_BOOTSTRAP)
    row = await (
        await conn.execute("SELECT max(version) AS version FROM schema_migrations")
    ).fetchone()
    return int(row["version"] or 0) if row else 0


async def applied(conn: Any) -> dict[int, str]:
    """``{version: checksum}`` for every applied migration."""

    await conn.execute(_BOOTSTRAP)
    cursor = await conn.execute("SELECT version, checksum FROM schema_migrations")
    return {int(row["version"]): row["checksum"] for row in await cursor.fetchall()}


async def migrate(conn: Any) -> int:
    """Apply every pending migration. Returns the resulting schema version.

    Raises :class:`MigrationError` if an already-applied migration's file has
    changed since it was applied.
    """

    already = await applied(conn)
    pending: list[Migration] = []

    for migration in migrations():
        recorded = already.get(migration.version)
        if recorded is None:
            pending.append(migration)
        elif recorded != migration.checksum:
            raise MigrationError(
                f"migration {migration.version:04d}_{migration.name} has changed since it "
                "was applied, so this database's schema is not the one in git; an applied "
                "migration is immutable (D103). Restore from backup, or start over: see "
                "docs/setup.md"
            )

    for migration in pending:
        # One transaction per migration: Postgres DDL is transactional, so a
        # failure here leaves the schema exactly as it was.
        async with conn.transaction():
            await conn.execute(migration.sql)
            await conn.execute(
                "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                (migration.version, migration.name, migration.checksum),
            )
        _log.info(
            "migration applied",
            fields={"version": migration.version, "name": migration.name},
        )

    return await schema_version(conn)
