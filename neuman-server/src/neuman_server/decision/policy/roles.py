"""Role-name validation and the pure baseline rewrite for a rename.

A role is a key in the baseline document and a value in every ``users[].roles``
list that names it. The group grants live in their own table and are moved by
the repository in the same transaction, because a grant left pointing at the
old key is inert — a silent revocation for everyone in the group.

The rewrite is pure and separate from the write so it can be tested without a
database, and so the route can compare-and-set the document it read.
"""

from __future__ import annotations

import copy
import re

__all__ = ["ROLE_NAME", "RoleError", "assign_roles_in_baseline", "rename_in_baseline"]

# The same shape the panel's role form accepts. The baseline schema puts no
# pattern on a role key, so this is the authoring surface's rule rather than a
# contract; it is repeated here because the server must not trust the form.
ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_-]*$")


def assign_roles_in_baseline(baseline: dict, name: str, roles: list[str]) -> dict:
    """Return a copy of ``baseline`` with ``name`` holding exactly ``roles``.

    The user entry is only a role assignment; per-account policy is the runtime
    overlay. Removing the last role removes the entry.
    """

    next_baseline = copy.deepcopy(baseline)
    users = next_baseline.get("users")
    if not isinstance(users, list):
        users = []

    target = next(
        (entry for entry in users if isinstance(entry, dict) and entry.get("name") == name),
        None,
    )
    if target is None:
        target = {"name": name}
        users.append(target)

    if roles:
        target["roles"] = sorted(set(roles))
    else:
        target.pop("roles", None)

    if not target.get("roles"):
        users.remove(target)

    next_baseline["users"] = users
    return next_baseline


class RoleError(ValueError):
    """A rename the baseline cannot perform."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def rename_in_baseline(baseline: dict, old: str, new: str) -> dict:
    """Return a copy of ``baseline`` with ``old`` renamed to ``new``.

    Every declared reference moves with the key. The copy is deep because the
    document is stored as JSONB and the caller is about to compare-and-set it;
    mutating the live dict in place would make a failed write look successful
    to anything already holding the old object.
    """

    if old == new:
        raise RoleError("same_name", f"{old!r} is already the name")
    if not ROLE_NAME.fullmatch(new):
        raise RoleError(
            "invalid_name",
            "a role name is lower-case letters, digits, underscore or hyphen",
        )

    next_baseline = copy.deepcopy(baseline)
    roles = next_baseline.get("roles")
    if not isinstance(roles, dict) or old not in roles:
        raise RoleError("unknown_role", f"{old!r} is not in the baseline")
    if new in roles:
        raise RoleError("role_exists", f"{new!r} is already a role")

    roles[new] = roles.pop(old)

    for user in next_baseline.get("users") or []:
        if not isinstance(user, dict):
            continue
        declared = user.get("roles")
        if isinstance(declared, list):
            user["roles"] = [new if role == old else role for role in declared]

    return next_baseline
