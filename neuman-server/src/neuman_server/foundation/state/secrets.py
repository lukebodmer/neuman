"""Provider keys entered in the administration page (D77).

A key is written once, to a file only the server can read, and the baseline
holds the path. It is never returned by a route, logged, stored in a database
row, put in an audit payload or copied into a backup — so restoring an
appliance means entering its keys again, which is the price of a backup that
cannot leak them.

Same write discipline as the overlay: a temporary file in the same directory,
`fsync`, then `os.replace`, so a reader sees the old key or the new one and
never half of either. The mode stays at the `0600` `mkstemp` creates, and the
directory is `0700`.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from pathlib import Path

__all__ = ["SecretError", "key_path", "sweep_secrets", "write_secret"]

# A provider name, which is also a baseline key. Checked here as well because
# this is the last place before a filesystem write.
_NAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*$")

# Generous for any provider's token, small enough that a pasted file is refused.
_LIMIT = 4096


class SecretError(ValueError):
    """The name or the value cannot be written as a key."""


def key_path(directory: str | Path, name: str) -> Path:
    if not _NAME.fullmatch(name) or name in {".", ".."}:
        raise SecretError(f"invalid provider name: {name!r}")
    return Path(directory) / f"{name}.key"


def write_secret(directory: str | Path, name: str, value: str) -> Path:
    """Write `value` as the key for provider `name` and return its path.

    Surrounding whitespace is dropped, because a pasted key usually carries a
    newline and the header it becomes must not. Anything else that is not a
    printable character is refused rather than stripped: it is not part of a
    key, and silently sending a different string is how a 401 gets blamed on
    the provider.
    """

    key = value.strip()
    if not key:
        raise SecretError("the key is empty")
    if len(key) > _LIMIT:
        raise SecretError(f"the key is longer than {_LIMIT} characters")
    if not key.isprintable() or any(character.isspace() for character in key):
        raise SecretError("the key contains whitespace or control characters")

    target = key_path(directory, name)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(target.parent), prefix=f".{name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(key)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
    return target


def sweep_secrets(directory: str | Path, keep: set[str]) -> list[str]:
    """Remove every key file not named in `keep`, and return what went.

    Run after each baseline write, so a provider removed by any route — the
    panel's table, a script's `baseline set` — takes its key with it rather
    than leaving a credential on disk that nothing names and nobody revokes.
    `keep` is the set of paths the baseline still references.
    """

    base = Path(directory)
    if not base.is_dir():
        return []
    removed = []
    for file in sorted(base.glob("*.key")):
        if str(file) not in keep:
            with contextlib.suppress(FileNotFoundError):
                file.unlink()
            removed.append(file.stem)
    return removed
