"""Ports the policy domain needs from the outside world.

Declared here as :class:`~typing.Protocol`s; implemented in `adapters/` and
injected by `composition/`. The decision layer never imports an adapter
(docs/decisions.md D7), which is what lets every rule below be tested against fakes.

`IdentityDirectory` is *not* declared here even though policy is a heavy
consumer of it: identity owns the concept of an account, and a port lives with
the domain whose language it speaks. Policy imports it from there — the one
direction that keeps "identity is policy" (docs/decisions.md D16) from becoming a cycle.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from ..identity.directory import IdentityDirectory

__all__ = [
    "GenerationRepository",
    "GrantRepository",
    "IdentityDirectory",
    "ToolManifest",
    "ToolRegistry",
]


class ToolManifest(Protocol):
    """One tool's manifest, as `contracts/schemas/tool-manifest.schema.json`."""

    @property
    def name(self) -> str: ...

    @property
    def kind(self) -> str: ...

    @property
    def needs(self) -> frozenset[str]: ...

    @property
    def mutating(self) -> bool: ...

    @property
    def critical(self) -> bool: ...

    @property
    def wait_ms(self) -> int: ...

    @property
    def lifetime_ms(self) -> int: ...

    @property
    def detaches(self) -> bool:
        """Whether a call can outlive its wait as a background job (D98)."""

    @property
    def parameters(self) -> dict[str, Any]: ...

    @property
    def description(self) -> str: ...

    @property
    def summary(self) -> str | None:
        """A short human label for the client's tool chooser, if the tool has one.

        Not the same string as `description`, which is the model's instructions
        and is long on purpose. None means the tool has not been given one.
        """
        ...

    @property
    def diagnostic(self) -> bool:
        """Whether this tool exists to test the appliance rather than to do work.

        Presentation only — the client leaves it out of the tool chooser. It is
        not a capability anybody chooses, so an ungranted row inviting an
        operator to grant it would be misleading. The gate is unaffected.
        """
        ...

    @property
    def returns_image(self) -> bool:
        """Whether the tool's stdout is a picture for the model to see (D28).

        The harness refuses such a tool to a model without vision, and lifts a
        successful result into an image rather than handing over base64.
        """
        ...


class ToolRegistry(Protocol):
    """The set of tools this appliance has: built in, or in an enabled package."""

    def get(self, name: str) -> ToolManifest | None: ...

    def names(self) -> Sequence[str]: ...

    def withheld(self, name: str) -> str | None:
        """The package a shipped tool is in, when that package is not enabled.

        None for a tool that is present, built in, or not shipped at all.
        """
        ...

    def __len__(self) -> int:
        """How many tools are installed.

        Declared because composition reports it at startup and in `/healthz`,
        where an operator reads it to confirm the appliance came up with the
        tool set it expected. An implementation without it fails at boot, which
        is exactly the kind of break a Protocol exists to make visible here
        rather than in production.
        """


class GrantRepository(Protocol):
    """Group-keyed role grants (docs/decisions.md D16)."""

    async def grants_for_groups(self, groups: frozenset[str]) -> list[tuple[str, str]]:
        """Every ``(group, role)`` pair whose group is in ``groups``, in a stable order.

        The pair rather than the role alone: the account page has to say whether
        a role is held directly or through a group, and a role granted by two
        groups is two removals in two places.
        """

    async def list_grants(self) -> list[tuple[str, str]]:
        """Every ``(group, role)`` pair."""

    async def rename_role(self, old: str, new: str) -> int:
        """Move every grant from ``old`` to ``new``; return how many moved.

        The move is part of a role rename, not a policy edit of its own: a grant
        left on the old key is inert, which silently revokes access for every
        member of the group.
        """

    async def add_grant(self, group: str, role: str) -> bool: ...

    async def remove_grant(self, group: str, role: str) -> bool: ...


class GenerationRepository(Protocol):
    """Per-account policy generation counters."""

    async def current(self, account_id: str) -> int: ...

    async def bump_if_changed(self, account_id: str, policy_hash: str) -> int:
        """Record the hash, bumping the generation when it differs."""
