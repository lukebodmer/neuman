"""Policy repositories: group-keyed grants and generation counters.

Implements `decision.policy.ports.GrantRepository` and `GenerationRepository`.
Policy *fragments* are not here — they live in the Nix baseline and the file
overlay by design (docs/decisions.md D18), so the database holds only what must be
transactional: who granted what, and whether a composed policy has changed.
"""

from __future__ import annotations

from typing import Any

__all__ = ["GenerationStore", "GrantStore"]


class GrantStore:
    """Group → role grants (docs/decisions.md D16)."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def grants_for_groups(self, groups: frozenset[str]) -> list[tuple[str, str]]:
        if not groups:
            return []
        cursor = await self._conn.execute(
            """
            SELECT group_name, role
              FROM policy_grants
             WHERE group_name = ANY(%s)
             ORDER BY group_name, role
            """,
            (list(groups),),
        )
        return [(row["group_name"], row["role"]) for row in await cursor.fetchall()]

    async def list_grants(self) -> list[tuple[str, str]]:
        cursor = await self._conn.execute(
            "SELECT group_name, role FROM policy_grants ORDER BY group_name, role"
        )
        return [(row["group_name"], row["role"]) for row in await cursor.fetchall()]

    async def rename_role(self, old: str, new: str) -> int:
        """Move grants in one statement so a conflict cannot half-apply.

        `DELETE ... RETURNING` is the move; the insert ignores a group that
        already held the new role, which is the only sensible answer when both
        names were granted to it.
        """

        cursor = await self._conn.execute(
            """
            WITH moved AS (
                DELETE FROM policy_grants WHERE role = %s
                RETURNING group_name
            )
            INSERT INTO policy_grants (group_name, role)
            SELECT group_name, %s FROM moved
            ON CONFLICT (group_name, role) DO NOTHING
            """,
            (old, new),
        )
        return cursor.rowcount

    async def add_grant(self, group: str, role: str) -> bool:
        cursor = await self._conn.execute(
            """
            INSERT INTO policy_grants (group_name, role)
            VALUES (%s, %s)
            ON CONFLICT (group_name, role) DO NOTHING
            """,
            (group, role),
        )
        return cursor.rowcount > 0

    async def remove_grant(self, group: str, role: str) -> bool:
        cursor = await self._conn.execute(
            "DELETE FROM policy_grants WHERE group_name = %s AND role = %s",
            (group, role),
        )
        return cursor.rowcount > 0


class GenerationStore:
    """Per-account policy generation counters."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def current(self, account_id: str) -> int:
        cursor = await self._conn.execute(
            "SELECT generation FROM policy_generations WHERE account_id = %s",
            (account_id,),
        )
        row = await cursor.fetchone()
        return int(row["generation"]) if row else 0

    async def bump_if_changed(self, account_id: str, policy_hash: str) -> int:
        """Record the hash, bumping the generation only when it differs.

        One statement so a concurrent bump cannot interleave between the read
        and the write: the `WHERE` on the update clause is what decides, not the
        application.
        """

        cursor = await self._conn.execute(
            """
            INSERT INTO policy_generations (account_id, generation, policy_hash, updated_at)
            VALUES (%s, 1, %s, now())
            ON CONFLICT (account_id) DO UPDATE
               SET generation = policy_generations.generation + 1,
                   policy_hash = EXCLUDED.policy_hash,
                   updated_at = now()
             WHERE policy_generations.policy_hash IS DISTINCT FROM EXCLUDED.policy_hash
            RETURNING generation
            """,
            (account_id, policy_hash),
        )
        row = await cursor.fetchone()
        if row is not None:
            return int(row["generation"])
        # The guard suppressed the update: the hash was unchanged.
        return await self.current(account_id)
