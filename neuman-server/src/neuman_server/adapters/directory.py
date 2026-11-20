"""`IdentityDirectory` over Postgres.

The policy engine asks one question of an account: which groups is it in? The
answer is Neuman's own `groups`/`group_members` tables, so a grant takes effect
on the next policy read and a disabled account loses every role immediately.

A connection is opened per call rather than cached. Policy is composed per step,
so a stale cache would be a revocation that lands late — exactly the property
the security model says it does not have. The pool is small and the query is a
three-table join on indexed columns; correctness wins over saving a round trip.
"""

from __future__ import annotations

from typing import Any

from .persistence.identity import AccountStore

__all__ = ["DatabaseDirectory"]


class DatabaseDirectory:
    """Implements `decision.identity.directory.IdentityDirectory`."""

    def __init__(self, database: Any) -> None:
        self._database = database

    async def groups_of(self, name: str) -> frozenset[str]:
        async with self._database.connection() as conn:
            return await AccountStore(conn).groups_of(name)
