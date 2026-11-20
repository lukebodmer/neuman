"""The capability gate: a tool's ``needs`` must be a subset of the user's reach.

This is a **dispatch decision**, not the enforcement boundary. Passing the gate
means Neuman is willing to ask for the work; the kernel is what confines the
process that does it, and the effect plane re-checks ``needs ⊆ reach``
independently against the profile it was handed. A bug here can deny
service. It cannot widen capability.

Every rule is derived from the manifest rather than a table in this file. v1
carried a hardcoded ``TOOL_NEEDS`` dict here and a matching constant in the
client, which drifted; the manifest is now the single statement of what a
tool needs, validated against the shared contract at load.
"""

from __future__ import annotations

from dataclasses import dataclass

from .compose import EffectivePolicy
from .ports import ToolRegistry

__all__ = ["Decision", "check", "grantable"]

# Reason codes. Stable strings: they reach the audit chain and the client, and
# an operator greps for them.
NOT_GRANTED = "tool_not_granted"
UNKNOWN_TOOL = "unknown_tool"
PACKAGE_NOT_ENABLED = "package_not_enabled"
INSUFFICIENT_REACH = "insufficient_reach"
ALLOWED = "ok"


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: str
    detail: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.allowed


def check(tool: str, policy: EffectivePolicy, registry: ToolRegistry) -> Decision:
    """Decide whether ``tool`` may be dispatched under ``policy``.

    Order matters for what the denial *says*: a tool the user was never granted
    is reported as ungranted even if it would also fail the reach test, because
    that is the fact an operator needs to act on.
    """

    if tool not in policy.tools:
        return Decision(False, NOT_GRANTED, f"{tool!r} is not granted to this user")

    manifest = registry.get(tool)
    if manifest is None and (package := registry.withheld(tool)) is not None:
        # Granted, shipped, and switched off for the whole appliance. Its own
        # reason because the remedy is the Tools page, not the role (D101).
        return Decision(
            False,
            PACKAGE_NOT_ENABLED,
            f"{tool!r} is in the {package!r} tool package, which this appliance has not enabled",
        )
    if manifest is None:
        # Granted but unknown: the policy names a tool this appliance does not
        # install. Fail closed — a name the registry cannot describe is a name
        # whose needs we cannot check (docs/decisions.md D5).
        return Decision(False, UNKNOWN_TOOL, f"{tool!r} has no manifest on this appliance")

    missing = manifest.needs - policy.reach
    if missing:
        return Decision(
            False,
            INSUFFICIENT_REACH,
            f"{tool!r} needs {sorted(manifest.needs)}; this user has {sorted(policy.reach)}",
        )

    return Decision(True, ALLOWED)


def grantable(policy: EffectivePolicy, registry: ToolRegistry) -> list[str]:
    """The tools that pass the gate right now, in registry order.

    This is what gets advertised to the model. Advertising a tool the gate would
    deny wastes a turn and teaches the model to propose things it cannot have,
    so the advertisement and the gate read from exactly the same rule.
    """

    return [name for name in registry.names() if check(name, policy, registry).allowed]
