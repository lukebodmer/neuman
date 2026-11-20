"""Layered policy load: baseline + grants + overlay, in one place.

The layer order is ``defaults → role(s) → overlay``, and this module is its
**single owner**. Nowhere else in the server may decide what order layers
compose in or where a layer comes from: a second opinion about layering is a
second policy engine.

Roles reach a user two ways, and both are here:

* **Group-keyed grants** (docs/decisions.md D16) — the roles granted to any group the user
  belongs to, resolved live through the :class:`IdentityDirectory` port.
* **Declared roles** — roles the baseline lists directly on the user entry.

Both are sorted and de-duplicated before composition so that the result does not
depend on directory iteration order: two appliances with the same configuration
must compose the same policy.

Everything is re-read per call. Policy is never cached per session, because
revocation must be immediate (docs/security-model.md).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ...foundation.state import read_overlay
from .compose import EffectivePolicy, compose
from .fragments import PolicyFragment
from .ports import GrantRepository, IdentityDirectory

__all__ = [
    "PolicySource",
    "RoleConflict",
    "RoleInForce",
    "account_role_conflicts",
    "baseline_role_conflicts",
    "conflict_detail",
    "layers_and_roles",
    "layers_for",
    "role_conflicts",
    "roles_in_force",
]


@dataclass(frozen=True, slots=True)
class PolicySource:
    """Where the layers come from.

    Held as data so tests can point it at a dict and a temp directory instead of
    a real baseline file.
    """

    baseline: dict[str, Any]
    overlay_dir: str

    def user_entry(self, name: str) -> dict[str, Any]:
        for entry in self.baseline.get("users") or []:
            if entry.get("name") == name:
                return entry
        return {}

    def role_fragment(self, role: str) -> PolicyFragment:
        roles = self.baseline.get("roles") or {}
        return PolicyFragment.parse(roles.get(role))

    @property
    def known_roles(self) -> frozenset[str]:
        return frozenset(self.baseline.get("roles") or {})


@dataclass(frozen=True, slots=True)
class RoleInForce:
    """One role that reaches an account, and the path it took.

    ``source`` is ``"declared"`` or ``"group:<name>"``. A role named twice
    appears twice, once per path, because the two are removed in different
    places: a declared role on the account, a granted role on the group. A role
    the baseline does not define is reported with ``known=False`` rather than
    dropped — the account page should show a declaration that has gone stale
    instead of silently composing without it.
    """

    name: str
    source: str
    known: bool


@dataclass(frozen=True, slots=True)
class RoleConflict:
    """Two roles in force set the same scalar to different values.

    Lists union, so a difference there is composition rather than a conflict.
    Scalars are last-wins, and the layer order sorts role names alphabetically;
    without this report the winner would depend on spelling and an operator
    would have no way to see why one role beat another.
    """

    field: str
    values: tuple[tuple[str, Any], ...]


def role_conflicts(source: PolicySource, roles: Iterable[str]) -> tuple[RoleConflict, ...]:
    """Report scalars two known roles set differently.

    Unknown roles are skipped: they are inert in composition as well, and a
    stale grant should not turn every account page into an error (D16).
    """

    by_field: dict[str, dict[str, Any]] = {}
    for role in sorted(set(roles)):
        if role not in source.known_roles:
            continue
        for field, value in source.role_fragment(role).scalars.items():
            by_field.setdefault(field, {})[role] = value

    conflicts: list[RoleConflict] = []
    for field in sorted(by_field):
        values = by_field[field]
        if len(set(values.values())) > 1:
            conflicts.append(RoleConflict(field, tuple(sorted(values.items()))))
    return tuple(conflicts)


def conflict_detail(conflicts: Iterable[RoleConflict]) -> str:
    """A compact sentence for an API refusal or a log line."""

    return "; ".join(
        f"{conflict.field}: " + ", ".join(f"{role}={value}" for role, value in conflict.values)
        for conflict in conflicts
    )


async def account_role_conflicts(
    source: PolicySource,
    name: str,
    *,
    directory: IdentityDirectory,
    grants: GrantRepository,
    extra_roles: Iterable[str] = (),
) -> tuple[RoleConflict, ...]:
    """Conflicts among every role that reaches one account.

    ``extra_roles`` is for a grant that has not been written yet: the caller can
    ask what adding it would do without mutating the repository first.
    """

    roles = await roles_in_force(source, name, directory=directory, grants=grants)
    names = {role.name for role in roles if role.known}
    names.update(extra_roles)
    return role_conflicts(source, names)


async def baseline_role_conflicts(
    source: PolicySource,
    *,
    accounts: Any,
    directory: IdentityDirectory,
    grants: GrantRepository,
) -> dict[str, tuple[RoleConflict, ...]]:
    """Every enabled account whose roles disagree on a scalar.

    Called on a baseline write, after the proposed document has been validated
    but before it is stored. Checking every account rather than only the ones
    named in the document is what catches a role edit that makes an existing
    group grant conflict.
    """

    conflicts: dict[str, tuple[RoleConflict, ...]] = {}
    for account in await accounts.list_all():
        if account.disabled:
            continue
        found = await account_role_conflicts(
            source,
            account.name,
            directory=directory,
            grants=grants,
        )
        if found:
            conflicts[account.name] = found
    return conflicts


async def roles_in_force(
    source: PolicySource,
    name: str,
    *,
    directory: IdentityDirectory | None = None,
    grants: GrantRepository | None = None,
) -> list[RoleInForce]:
    """Return every role that reaches ``name``, with where it came from.

    The same resolution `layers_and_roles` composes, exposed for the account
    page: an operator changing a role needs to know whether the account holds
    it directly or through a group, because only the first is edited here.
    """

    known = source.known_roles
    roles = [
        RoleInForce(role, "declared", role in known)
        for role in source.user_entry(name).get("roles") or []
    ]

    if directory is not None and grants is not None:
        groups = await directory.groups_of(name)
        if groups:
            for group, role in await grants.grants_for_groups(groups):
                roles.append(RoleInForce(role, f"group:{group}", role in known))

    return sorted(set(roles), key=lambda role: (role.name, role.source))


async def layers_and_roles(
    source: PolicySource,
    name: str,
    *,
    directory: IdentityDirectory | None = None,
    grants: GrantRepository | None = None,
) -> tuple[list[tuple[str, PolicyFragment]], list[RoleInForce]]:
    """Return the labelled layers for ``name`` and the roles that produced them.

    ``directory`` and ``grants`` are optional so that policy can be composed
    from the baseline alone — the export and preview paths do exactly that, and
    an appliance with no grants configured is a valid one. The roles are
    returned alongside the layers so the account page can say whether a role is
    declared on the account or inherited from a group without resolving the
    directory a second time.
    """

    roles = await roles_in_force(source, name, directory=directory, grants=grants)

    layers: list[tuple[str, PolicyFragment]] = [
        ("defaults", PolicyFragment.parse(source.baseline.get("defaults")))
    ]

    # Deterministic order, and a role named twice composes once. A grant naming
    # a role the baseline does not define is inert, not an error: roles come and
    # go in git, and a stale grant must not break every login (docs/decisions.md
    # D16 — "policy for a name that does not resolve is inert").
    for role in sorted({entry.name for entry in roles if entry.known}):
        layers.append((f"role:{role}", source.role_fragment(role)))

    overlay = read_overlay(source.overlay_dir, name)
    if overlay is not None:
        # Only this layer may subtract (see fragments.py).
        layers.append(("overlay", PolicyFragment.parse(overlay, replaces=True)))

    return layers, roles


async def layers_for(
    source: PolicySource,
    name: str,
    *,
    directory: IdentityDirectory | None = None,
    grants: GrantRepository | None = None,
) -> list[tuple[str, PolicyFragment]]:
    """Return the labelled layers for ``name``, in composition order."""

    layers, _ = await layers_and_roles(source, name, directory=directory, grants=grants)
    return layers


async def effective_policy(
    source: PolicySource,
    name: str,
    *,
    directory: IdentityDirectory | None = None,
    grants: GrantRepository | None = None,
) -> EffectivePolicy:
    """Compose the live effective policy for ``name``."""

    return compose(await layers_for(source, name, directory=directory, grants=grants))
