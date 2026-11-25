"""What this computer is called, as the appliance will know it.

A machine is a named place dispatches run (D112). The name is the identity: a
branch records it, policy may narrow to it, and every dispatch entry carries it.
So it has to be stable across restarts and honestly the same computer each time,
which is why it is kept in the state directory rather than recomputed from the
hostname on every start — a hostname change is not a new computer, and silently
becoming a second machine would strand every conversation pointed at the first.

The default comes from the hostname because that is the name a person already
uses for the computer, slugged to the label shape the contract states. It is a
label, not a claim: the connection is bound to an account by the login that
produced it, so this can only ever name one of *that* account's places, and a
dishonest label widens nothing. What it does decide is which of the person's
own filesystems a conversation means — and that is a question only the person
can answer, which is why there is a flag.
"""

from __future__ import annotations

import json
import os
import re
import socket
from pathlib import Path

__all__ = ["FILE", "LABEL_ENV", "MachineError", "default_label", "label", "slug"]

# An operator who has to script a deployment, or a machine whose hostname is
# useless ("ip-10-0-3-17"), can set this instead of editing a unit file.
LABEL_ENV = "NEUMAN_MACHINE"

# Beside `session.json`: the two facts this installation has about itself that
# survive a restart.
FILE = "machine.json"

# The contract's shape (`common.schema.json#/$defs/machineLabel`), repeated
# because the client refuses a bad name before asking the appliance to, and a
# sentence here beats a closed socket there.
_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class MachineError(ValueError):
    """The name this installation would use is not one the appliance accepts."""


def slug(text: str) -> str:
    """A hostname as a machine label.

    Lower case, with every run of anything outside the allowed set collapsed to
    one hyphen, because a label is typed by people and compared by strings and
    the two only agree when there is one spelling. Truncated to the contract's
    ceiling, and never left starting with the separator it might have gained.
    """

    cleaned = re.sub(r"[^a-z0-9._-]+", "-", text.strip().lower())
    cleaned = cleaned.lstrip(".-_")[:64].rstrip(".-_")
    return cleaned or "client"


def default_label() -> str:
    """The name this computer would use if nobody said otherwise."""

    try:
        host = socket.gethostname()
    except OSError:  # pragma: no cover - a host with no name at all
        return "client"
    # The short form: `ernest`, not `ernest.lan.example.com`. A person reads
    # this in a header and policy grants it, and both are better served by the
    # name they call the computer.
    return slug(host.split(".", 1)[0])


def label(state: Path, override: str | None = None) -> str:
    """This installation's machine label, recording it the first time.

    `override` is the flag; `NEUMAN_MACHINE` is the environment; the stored
    file is what either of those last chose; the default is the hostname. A
    stored value is kept rather than rewritten from the hostname, because a
    hostname is a setting somebody else can change and a machine's identity is
    not.
    """

    path = state / FILE
    if override is not None:
        chosen = override.strip().lower()
    elif os.environ.get(LABEL_ENV):
        chosen = os.environ[LABEL_ENV].strip().lower()
    else:
        stored = _read(path)
        if stored is not None:
            return stored
        chosen = default_label()

    if not _PATTERN.fullmatch(chosen):
        raise MachineError(
            f"{chosen!r} is not a machine name. Lower-case letters, digits, "
            "dot, hyphen or underscore, starting with a letter or digit, at "
            "most 64 characters — for example `ernest` or `dgx`."
        )
    _write(path, chosen)
    return chosen


def _read(path: Path) -> str | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get("label")
    # A stored name that no longer satisfies the pattern is not repaired into
    # something else: a rename nobody asked for would move every conversation
    # that names it. It falls back to the hostname, and the person can say.
    return value if isinstance(value, str) and _PATTERN.fullmatch(value) else None


def _write(path: Path, chosen: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # 0600 in one call rather than written then chmodded, matching the session
    # file: nothing secret is here, but there is no reason for the window's
    # bookkeeping to be readable by anyone else on the machine.
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump({"label": chosen}, stream)
