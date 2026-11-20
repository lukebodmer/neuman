"""Policy authoring operations: guarded, audited, generation-bumped.

Every mutation of runtime policy goes through here, and each one does the same
three things in the same order:

1. **Guard** — `require_admin`, inside the operation (docs/decisions.md D9).
2. **Apply** — write the overlay atomically.
3. **Record** — append to the audit chain and bump the affected generation.

Doing this in the operation rather than the surface is what makes "a surface
cannot bypass the audit chain" true (docs/decisions.md D8). The HTTP admin API and the admin
socket both call these functions; neither can author policy any other way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...foundation.logging import get_logger
from ...foundation.state import delete_overlay, read_overlay, write_overlay
from ..audit import chain
from ..audit.ports import AuditRepository
from ..identity.authz import AdminPolicy, require_admin
from .compose import EffectivePolicy, compose
from .fragments import (
    AUTO_ACCEPT_MODES,
    FragmentError,
    PolicyFragment,
    auto_accept_rank,
)
from .generations import record
from .ports import GenerationRepository, GrantRepository, IdentityDirectory
from .source import (
    PolicySource,
    RoleConflict,
    RoleInForce,
    layers_and_roles,
    layers_for,
    role_conflicts,
)

__all__ = [
    "DraftPreview",
    "PolicyChange",
    "PolicyPreview",
    "choose_auto_accept",
    "clear_overlay",
    "grant_role",
    "preview_draft",
    "preview_policy",
    "preview_policy_detail",
    "revoke_role",
    "set_overlay",
]

_log = get_logger("neuman.server.policy.ops")


@dataclass(frozen=True, slots=True)
class PolicyChange:
    account: str
    generation: int
    changed: bool
    policy: EffectivePolicy


@dataclass(frozen=True, slots=True)
class PolicyPreview:
    """One account's composed policy, split into what it inherits and its exceptions.

    ``inherited`` is the same composition without the overlay: defaults plus
    every role that reaches the account. The account page shows an edit as a
    difference from that value. Composing it here keeps the layer order in
    `source.py`, its single owner.
    """

    policy: EffectivePolicy
    inherited: EffectivePolicy
    roles: tuple[RoleInForce, ...]
    conflicts: tuple[RoleConflict, ...] = ()


@dataclass(frozen=True, slots=True)
class DraftPreview:
    """A proposed composition before any account exists.

    Onboarding needs to show what a role selection will do without creating the
    account first. This is the same `compose` call the live path uses, over a
    list of roles the caller has not stored yet.
    """

    policy: EffectivePolicy
    conflicts: tuple[RoleConflict, ...]


async def preview_policy(
    name: str,
    *,
    source: PolicySource,
    directory: IdentityDirectory | None = None,
    grants: GrantRepository | None = None,
) -> EffectivePolicy:
    """Compose the effective policy for ``name`` without changing anything.

    The read path for "why can this person do that?" — the composed result
    carries provenance, so the answer names the layer.
    """

    return compose(await layers_for(source, name, directory=directory, grants=grants))


async def preview_policy_detail(
    name: str,
    *,
    source: PolicySource,
    directory: IdentityDirectory | None = None,
    grants: GrantRepository | None = None,
) -> PolicyPreview:
    """Compose an account's policy and the value it would have without its overlay.

    The read path for the account page: the effective policy answers "what is in
    force", the inherited one answers "what defaults and roles give", and the
    roles name the paths that produced them. Together they are what lets the
    panel show an override as one, rather than as a permission the roles gave.
    """

    layers, roles = await layers_and_roles(source, name, directory=directory, grants=grants)
    inherited = compose([(label, fragment) for label, fragment in layers if label != "overlay"])
    conflicts = role_conflicts(source, [role.name for role in roles if role.known])
    return PolicyPreview(
        policy=compose(layers),
        inherited=inherited,
        roles=tuple(roles),
        conflicts=conflicts,
    )


def preview_draft(
    *,
    source: PolicySource,
    roles: list[str],
    overlay: dict[str, Any] | None = None,
) -> DraftPreview:
    """Compose a proposed account policy without writing it.

    The onboarding dialog must not re-implement layer order in JavaScript. A
    second composition path would drift from `source.py`, and the panel would
    preview a policy different from the one the server applies.
    """

    unknown = sorted(set(roles) - source.known_roles)
    if unknown:
        raise FragmentError(f"unknown roles: {', '.join(unknown)}")

    layers: list[tuple[str, PolicyFragment]] = [
        ("defaults", PolicyFragment.parse(source.baseline.get("defaults")))
    ]
    for role in sorted(set(roles)):
        layers.append((f"role:{role}", source.role_fragment(role)))
    if overlay:
        layers.append(("overlay", PolicyFragment.parse(overlay, replaces=True)))

    return DraftPreview(
        policy=compose(layers),
        conflicts=role_conflicts(source, roles),
    )


async def set_overlay(
    name: str,
    fragment: dict[str, Any],
    *,
    actor: str,
    source: PolicySource,
    directory: IdentityDirectory,
    accounts: Any,
    generations: GenerationRepository,
    audit: AuditRepository,
    grants: GrantRepository | None = None,
    admin_policy: AdminPolicy | None = None,
    mirror: Any = None,
) -> PolicyChange:
    """Replace an account's runtime overlay.

    The fragment is validated before it is written: an overlay that cannot be
    parsed would fail closed at the next dispatch, which is safe but silent, and
    an operator deserves the error at authoring time.
    """

    await require_admin(actor, directory=directory, policy=admin_policy)

    try:
        parsed = PolicyFragment.parse(fragment, replaces=True)
    except FragmentError as exc:
        raise FragmentError(f"overlay for {name!r} is not valid policy: {exc}") from exc

    previous = read_overlay(source.overlay_dir, name)
    write_overlay(source.overlay_dir, name, parsed.to_json())

    policy = await preview_policy(name, source=source, directory=directory, grants=grants)
    change = await _record(name, policy, accounts=accounts, generations=generations)

    await chain.append(
        audit,
        actor=actor,
        action="policy.overlay_set",
        payload={
            "account": name,
            "previous": previous,
            "overlay": parsed.to_json(),
            "generation": change.generation,
        },
        mirror=mirror,
    )
    _log.info(
        "overlay set",
        fields={"account": name, "actor": actor, "generation": change.generation},
    )
    return change


async def clear_overlay(
    name: str,
    *,
    actor: str,
    source: PolicySource,
    directory: IdentityDirectory,
    accounts: Any,
    generations: GenerationRepository,
    audit: AuditRepository,
    grants: GrantRepository | None = None,
    admin_policy: AdminPolicy | None = None,
    mirror: Any = None,
) -> PolicyChange:
    """Drop an account's runtime overlay, reverting to the declared baseline."""

    await require_admin(actor, directory=directory, policy=admin_policy)

    previous = read_overlay(source.overlay_dir, name)
    delete_overlay(source.overlay_dir, name)

    policy = await preview_policy(name, source=source, directory=directory, grants=grants)
    change = await _record(name, policy, accounts=accounts, generations=generations)

    await chain.append(
        audit,
        actor=actor,
        action="policy.overlay_cleared",
        payload={"account": name, "previous": previous, "generation": change.generation},
        mirror=mirror,
    )
    return change


async def grant_role(
    group: str,
    role: str,
    *,
    actor: str,
    directory: IdentityDirectory,
    grants: GrantRepository,
    audit: AuditRepository,
    admin_policy: AdminPolicy | None = None,
    mirror: Any = None,
) -> bool:
    """Grant a role to a group (docs/decisions.md D16)."""

    await require_admin(actor, directory=directory, policy=admin_policy)
    changed = await grants.add_grant(group, role)
    if changed:
        await chain.append(
            audit,
            actor=actor,
            action="policy.role_granted",
            payload={"group": group, "role": role},
            mirror=mirror,
        )
        _log.info("role granted", fields={"group": group, "role": role, "actor": actor})
    return changed


async def revoke_role(
    group: str,
    role: str,
    *,
    actor: str,
    directory: IdentityDirectory,
    grants: GrantRepository,
    audit: AuditRepository,
    admin_policy: AdminPolicy | None = None,
    mirror: Any = None,
) -> bool:
    """Revoke a role from a group.

    Takes effect at the next dispatch for every member, because policy is
    re-read per dispatch and never cached per session (docs/security-model.md).
    """

    await require_admin(actor, directory=directory, policy=admin_policy)
    changed = await grants.remove_grant(group, role)
    if changed:
        await chain.append(
            audit,
            actor=actor,
            action="policy.role_revoked",
            payload={"group": group, "role": role},
            mirror=mirror,
        )
        _log.warning("role revoked", fields={"group": group, "role": role, "actor": actor})
    return changed


async def choose_auto_accept(
    name: str,
    mode: str,
    *,
    source: PolicySource,
    directory: IdentityDirectory,
    accounts: Any,
    generations: GenerationRepository,
    audit: AuditRepository,
    grants: GrantRepository | None = None,
    mirror: Any = None,
) -> PolicyChange:
    """Set one account's own approval mode, within the ceiling it was granted.

    The one policy field a *user* may author for themselves, and it is here
    rather than in the HTTP surface for the reason the module docstring gives:
    every mutation of runtime policy is guarded, audited and generation-bumped
    in one place, so a surface cannot bypass the chain (D8).

    No `require_admin`. That is the whole point of this operation, and what
    makes it safe is `auto_accept_max`: an operator states how far this account
    may go, and a choice beyond it is refused here. The enforcement path clamps
    independently (`EffectivePolicy.effective_auto_accept`), so a bug in this
    check narrows rather than widens.

    Writing is a *merge* into the existing overlay, never a replacement. An
    overlay can hold operator-authored grants — a removed tool, a narrowed path
    — and a user changing their approval mode must not drop them. Replacing it
    would make a UI setting silently revoke an operator's revocation.
    """

    if mode not in AUTO_ACCEPT_MODES:
        raise FragmentError(f"auto_accept must be one of {AUTO_ACCEPT_MODES}, got {mode!r}")

    current = await preview_policy(name, source=source, directory=directory, grants=grants)
    if auto_accept_rank(mode) > auto_accept_rank(current.auto_accept_max):
        raise FragmentError(
            f"{mode!r} exceeds the approval ceiling for {name!r} "
            f"({current.auto_accept_max!r}); an operator sets `auto_accept_max`"
        )

    existing = read_overlay(source.overlay_dir, name) or {}
    merged = dict(existing)
    merged["auto_accept"] = mode

    # Validated after merging, so a pre-existing overlay that has since become
    # invalid is reported here rather than written back out unchecked.
    parsed = PolicyFragment.parse(merged, replaces=True)
    write_overlay(source.overlay_dir, name, parsed.to_json())

    policy = await preview_policy(name, source=source, directory=directory, grants=grants)
    change = await _record(name, policy, accounts=accounts, generations=generations)

    # Audited like any other policy change. A user lowering their own guardrail
    # is exactly the kind of decision the chain exists to record — and `actor`
    # is the user, because they are who did it.
    await chain.append(
        audit,
        actor=name,
        action="policy.auto_accept",
        payload={
            "account": name,
            "mode": mode,
            "ceiling": current.auto_accept_max,
            "generation": change.generation,
        },
        mirror=mirror,
    )
    _log.info(
        "approval mode chosen",
        fields={"account": name, "mode": mode, "ceiling": current.auto_accept_max},
    )
    return change


async def _record(
    name: str,
    policy: EffectivePolicy,
    *,
    accounts: Any,
    generations: GenerationRepository,
) -> PolicyChange:
    """Bump the generation for an account whose policy may have changed."""

    account = await accounts.by_name(name)
    if account is None:
        # Policy authored for a name that does not resolve is inert (D16); there
        # is no account row to attach a generation to yet.
        return PolicyChange(account=name, generation=0, changed=False, policy=policy)

    state = await record(generations, account.id, policy)
    return PolicyChange(
        account=name, generation=state.generation, changed=state.changed, policy=policy
    )
