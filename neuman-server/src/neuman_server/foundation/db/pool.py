"""The connection pool every loop borrows from.

One pool per process, owned here and nowhere else (docs/decisions.md D18). Loops borrow a
connection for the duration of a unit of work and give it back; nothing opens a
connection per thread or per request — v1's SQLite thread-affinity problems
 were a symptom of exactly that.

The server connects over the local Unix socket with peer authentication as the
`neuman` role, so there is no password anywhere in the configuration.

`psycopg` is imported lazily so that the pure-decision parts of the package
remain importable (and testable) without the driver installed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Protocol

from ..logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from psycopg import AsyncConnection

__all__ = ["Database", "Pool", "connect"]

_log = get_logger("neuman.server.db")


class Pool(Protocol):
    """What the rest of the server needs from a pool.

    Narrow on purpose: tests substitute a fake that hands out a connection to a
    transaction that is rolled back afterwards.
    """

    def connection(self) -> Any:
        """An async context manager yielding a connection."""


class Database:
    """Owns the process's connection pool.

    Usage::

        db = Database(settings.database_url, min_size=2, max_size=16)
        await db.open()
        async with db.connection() as conn:
            ...
        await db.close()
    """

    __slots__ = ("_max_size", "_min_size", "_pool", "_url")

    def __init__(self, url: str, *, min_size: int = 2, max_size: int = 16) -> None:
        self._url = url
        self._min_size = min_size
        self._max_size = max_size
        self._pool: Any | None = None

    async def open(self) -> None:
        """Open the pool and wait for it to be usable.

        Waiting here means a failure to reach Postgres is a startup error with a
        clear message, not a confusing error on the first request.
        """

        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(
            self._url,
            min_size=self._min_size,
            max_size=self._max_size,
            kwargs={"row_factory": dict_row, "autocommit": True},
            open=False,
        )
        await pool.open(wait=True)
        self._pool = pool
        _log.info(
            "database pool open",
            fields={"min_size": self._min_size, "max_size": self._max_size},
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection]:
        """Borrow a connection for the duration of the block."""

        if self._pool is None:
            raise RuntimeError("database pool is not open")
        async with self._pool.connection() as conn:
            yield conn


@asynccontextmanager
async def connect(url: str) -> AsyncIterator[AsyncConnection]:
    """A single connection, outside any pool.

    For migrations and one-shot operator commands, which run before or beside
    the server rather than inside it.
    """

    import psycopg
    from psycopg.rows import dict_row

    conn = await psycopg.AsyncConnection.connect(url, row_factory=dict_row)
    try:
        yield conn
    finally:
        await conn.close()
