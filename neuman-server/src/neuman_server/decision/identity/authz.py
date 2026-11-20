"""Admin authorization, checked inside operations.

Authorization is identity's job and is checked **inside** the core operation,
never by the surface that called it (docs/decisions.md (D9)). The HTTP API
and the admin socket are both thin adapters over the same operations; if each
checked authorization itself, the two would eventually disagree, and the
disagreement would be a privilege escalation rather than a bug.

Admin status comes from group membership in the directory, so it follows the
same lifecycle as every other grant (docs/decisions.md D16). The admin socket additionally
authenticates by peer credentials, which is a *second* factor — being on the
machine as the right uid — not a replacement for this check.

**One group, and one word.** This was "operator" and admitted `wheel` as well,
which cost nothing in correctness and a great deal in explanation: the Nix
option that granted socket *reachability* was also called `operators`, so the
word named two different things and the module's own documentation had to say
that listing someone as an operator did not make them one. The word is now
`admin` everywhere — the CLI, the group, the authority, the error — and the
authority is exactly membership in `neuman-admin`.

Dropping `wheel` is a deliberate narrowing. A sysadmin can still reach
everything by editing the policy file and restarting the server, so this is not
a boundary against them; what it stops is the appliance *handing* the governance
record to everyone who can sudo, which on a shared machine may be several people
with no business reading it. Admin becomes something granted on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass

from .directory import IdentityDirectory

__all__ = ["AdminPolicy", "Authorization", "Denied", "require_admin"]


class Denied(PermissionError):
    """The actor may not perform this operation."""


@dataclass(frozen=True, slots=True)
class AdminPolicy:
    """Which groups confer admin authority."""

    admin_groups: frozenset[str] = frozenset({"neuman-admin"})


@dataclass(frozen=True, slots=True)
class Authorization:
    actor: str
    is_admin: bool
    via: str = ""


async def authorize(
    actor: str,
    *,
    directory: IdentityDirectory,
    policy: AdminPolicy | None = None,
) -> Authorization:
    """Determine whether ``actor`` holds admin authority right now."""

    rules = policy or AdminPolicy()
    groups = await directory.groups_of(actor)
    matched = sorted(groups & rules.admin_groups)
    return Authorization(actor=actor, is_admin=bool(matched), via=matched[0] if matched else "")


async def require_admin(
    actor: str,
    *,
    directory: IdentityDirectory,
    policy: AdminPolicy | None = None,
) -> Authorization:
    """Assert admin authority or raise :class:`Denied`."""

    result = await authorize(actor, directory=directory, policy=policy)
    if not result.is_admin:
        raise Denied(f"{actor!r} is not an admin on this appliance")
    return result
