"""Revocation counters.

Policy is re-read per dispatch, not cached per session (docs/security-model.md), so a
generation counter is not what makes revocation work — re-reading is. What the
counter provides is *detection*: a live session holding generation N can be told
its policy changed, so the client can refresh what it advertises and a long-
running turn can be re-gated rather than finishing under stale assumptions.

The hash is of the composed policy (excluding provenance), so relabelling which
layer granted something does not bump the generation and does not disturb live
sessions.
"""

from __future__ import annotations

from dataclasses import dataclass

from .compose import EffectivePolicy, policy_hash
from .ports import GenerationRepository

__all__ = ["GenerationState", "current", "record"]


@dataclass(frozen=True, slots=True)
class GenerationState:
    generation: int
    changed: bool


async def current(repository: GenerationRepository, account_id: str) -> int:
    """The account's current policy generation (0 when never recorded)."""

    return await repository.current(account_id)


async def record(
    repository: GenerationRepository,
    account_id: str,
    policy: EffectivePolicy,
) -> GenerationState:
    """Record the composed policy, bumping the generation when it changed."""

    before = await repository.current(account_id)
    after = await repository.bump_if_changed(account_id, policy_hash(policy))
    return GenerationState(generation=after, changed=after != before)
