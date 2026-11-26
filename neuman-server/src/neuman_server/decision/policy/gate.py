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

__all__ = ["Decision", "check", "check_machine", "grantable"]

# Reason codes. Stable strings: they reach the audit chain and the client, and
# an operator greps for them.
NOT_GRANTED = "tool_not_granted"
UNKNOWN_TOOL = "unknown_tool"
PACKAGE_NOT_ENABLED = "package_not_enabled"
INSUFFICIENT_REACH = "insufficient_reach"
MACHINE_NOT_GRANTED = "machine_not_granted"
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


def check_machine(machine: str, policy: EffectivePolicy) -> Decision:
    """Decide whether this policy may dispatch to ``machine`` (D112).

    A dispatch-time ceiling, like `check` — not the boundary. What bounds a
    dispatch is the operating system on the machine it lands on, running as the
    person who logged in there.

    **An empty grant list is not a refusal, and the asymmetry with `tools` is
    deliberate.** A tool is something the appliance holds and hands out; a
    machine is a place this account's own client logged in from, so reaching it
    already depends on a login rather than on a grant, and a policy list can only
    ever narrow that to the places a role's work belongs. Refusing an empty list
    would make every existing role unusable the day this field arrived, and would
    say "you may not use the computer you are typing on" — which heals nothing
    and teaches the operator to fill the field with every machine they own.

    The cost of that choice, stated because a restriction that names no cost is a
    default dressed as a decision: a role that forgets to list machines does not
    confine the role to fewer places, so an operator who wants a laptop-only role
    must say so. The gain is that the list is usable for its actual purpose —
    narrowing people whose work spans a shared box and a personal one.
    """

    if not policy.machines:
        return Decision(True, ALLOWED)
    if machine not in policy.machines:
        return Decision(
            False,
            MACHINE_NOT_GRANTED,
            f"{machine!r} is not one of the machines this user may work on "
            f"({', '.join(policy.machines)})",
        )
    return Decision(True, ALLOWED)


def grantable(policy: EffectivePolicy, registry: ToolRegistry) -> list[str]:
    """The tools that pass the gate right now, in registry order.

    This is what gets advertised to the model. Advertising a tool the gate would
    deny wastes a turn and teaches the model to propose things it cannot have,
    so the advertisement and the gate read from exactly the same rule.
    """

    return [name for name in registry.names() if check(name, policy, registry).allowed]
