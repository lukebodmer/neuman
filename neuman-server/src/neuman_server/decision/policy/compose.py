"""Fragment composition → the effective policy.

``P(u) = defaults → roles (in order) → overlay``. List fields accumulate until
the overlay, whose lists replace them (D87); scalars override. The result carries
**per-field provenance** so an operator can see which layer set each value —
"why can this person write here?" is the question the admin console exists to
answer, and it is unanswerable from the composed result alone.

The composed policy is *reach*, not enforcement: it says what the user may be
granted. The capability gate and the approval verdict are what bound a dispatch
now (docs/security-model.md): the client runs as the human, and there is no kernel
confinement below them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .fragments import LIST_FIELDS, SCALAR_DEFAULTS, PolicyFragment

__all__ = ["EffectivePolicy", "compose", "policy_hash"]


@dataclass(frozen=True, slots=True)
class EffectivePolicy:
    """The composed result for one account."""

    tools: tuple[str, ...] = ()
    files_read: tuple[str, ...] = ()
    files_write: tuple[str, ...] = ()
    models: tuple[str, ...] = ()
    network: str = SCALAR_DEFAULTS["network"]
    auto_accept: str = SCALAR_DEFAULTS["auto_accept"]
    auto_accept_max: str = SCALAR_DEFAULTS["auto_accept_max"]
    max_iterations: int = SCALAR_DEFAULTS["max_iterations"]
    tokens_daily: int | None = SCALAR_DEFAULTS["tokens_daily"]
    provenance: dict[str, str] = field(default_factory=dict)

    @property
    def effective_auto_accept(self) -> str:
        """The approval mode actually in force: `auto_accept`, clamped to the ceiling.

        The clamp lives here rather than in `compose` because the two fields can
        arrive from different layers — an operator states the ceiling in a role,
        the user chooses within it from an overlay, and scalars are last-wins, so
        the overlay would otherwise simply beat the role. Composing cannot know
        that one of these bounds the other; this is where that relationship is
        expressed.

        Read by the consent check, so a choice wider than the grant is narrowed
        at the point of enforcement rather than trusted at the point of writing.
        Both are checked anyway — the route refuses an out-of-range choice — but a
        validation that is the *only* guard is one bug away from being no guard.
        """

        from .fragments import AUTO_ACCEPT_MODES, auto_accept_rank

        chosen = auto_accept_rank(self.auto_accept)
        ceiling = auto_accept_rank(self.auto_accept_max)
        return AUTO_ACCEPT_MODES[min(chosen, ceiling)]

    def clamped_auto_accept(self, choice: str | None) -> str:
        """One branch's approval choice, clamped to the operator's ceiling.

        `None` means the branch has stated no choice and follows the account's
        own mode. A stated choice never exceeds `auto_accept_max`, whatever the
        account's mode says: a branch is a per-conversation preference, not a
        second policy.
        """

        from .fragments import AUTO_ACCEPT_MODES, auto_accept_rank

        chosen = auto_accept_rank(choice) if choice else auto_accept_rank(self.auto_accept)
        ceiling = auto_accept_rank(self.auto_accept_max)
        return AUTO_ACCEPT_MODES[min(chosen, ceiling)]

    @property
    def reach(self) -> frozenset[str]:
        """The capability kinds this policy actually confers.

        A tool's ``needs`` must be a subset of this (D43, the capability gate). Reach is derived from whether the user has *any* path or host,
        not from a separate grant list: a user with no writable path has no
        write reach regardless of what a fragment claims.
        """

        kinds: set[str] = set()
        if self.files_read:
            kinds.add("read")
        if self.files_write:
            kinds.add("write")
        if self.network == "any":
            kinds.add("network")
        return frozenset(kinds)

    def to_json(self) -> dict[str, Any]:
        return {
            "tools": list(self.tools),
            "files_read": list(self.files_read),
            "files_write": list(self.files_write),
            "models": list(self.models),
            "network": self.network,
            "auto_accept": self.auto_accept,
            "auto_accept_max": self.auto_accept_max,
            "max_iterations": self.max_iterations,
            "tokens_daily": self.tokens_daily,
        }


def compose(layers: Iterable[tuple[str, PolicyFragment]]) -> EffectivePolicy:
    """Compose labelled fragments in order; later layers win for scalars."""

    lists: dict[str, tuple[str, ...]] = dict.fromkeys(LIST_FIELDS, ())
    scalars: dict[str, Any] = dict(SCALAR_DEFAULTS)
    provenance: dict[str, str] = {}

    for label, fragment in layers:
        for name, items in fragment.lists.items():
            below = () if fragment.replaces else lists[name]
            lists[name] = (*below, *(item for item in items if item not in below))
            provenance[name] = label
        for name, value in fragment.scalars.items():
            scalars[name] = value
            provenance[name] = label

    return EffectivePolicy(
        tools=lists["tools"],
        files_read=lists["files_read"],
        files_write=lists["files_write"],
        models=lists["models"],
        network=scalars["network"],
        auto_accept=scalars["auto_accept"],
        auto_accept_max=scalars["auto_accept_max"],
        max_iterations=scalars["max_iterations"],
        tokens_daily=scalars["tokens_daily"],
        provenance=provenance,
    )


def policy_hash(policy: EffectivePolicy) -> str:
    """A stable content hash of the effective policy.

    Provenance is excluded: relabelling which layer granted something is not a
    policy change, and bumping the generation for it would revoke live sessions
    for nothing.
    """

    canonical = json.dumps(policy.to_json(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
