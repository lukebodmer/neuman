"""The group directory port.

Policy reaches a person through group membership. The account row names the
person; the groups attached to it decide what policy fragments compose. There is
no host directory behind this any more — the membership is Neuman's own, which
is what lets an operator add someone and grant a role without touching Nix
(D68).

The port is async because its only implementation queries Postgres. It is
deliberately tiny: one question, asked on every policy read, so a change lands
on the next step rather than at the next login.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["IdentityDirectory"]


@runtime_checkable
class IdentityDirectory(Protocol):
    """Live group membership for an account."""

    async def groups_of(self, name: str) -> frozenset[str]:
        """Every group ``name`` belongs to, or empty for an unknown/disabled one."""
