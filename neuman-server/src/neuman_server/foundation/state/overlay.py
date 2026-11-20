"""Atomic read/write of the runtime policy overlay.

The overlay is the runtime-authoring surface: what an operator changes through
the admin API between Nix deployments. It stays a file rather than a database
row because it must survive a wiped database (docs/decisions.md D18) — losing runtime policy
to a restore is how an appliance silently reverts an operator's revocation.

Write discipline: write to a temp file in the same
directory, `fchmod` it (`mkstemp` yields `0600`, which the server may not be
able to re-read after a uid change and which hides the file from operators),
`fsync`, then `os.replace`. A half-written overlay must never be ingested; the
rename is what makes that impossible.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..logging import get_logger

__all__ = ["OverlayError", "delete_overlay", "list_overlays", "read_overlay", "write_overlay"]

_log = get_logger("neuman.server.state.overlay")

# Group-readable: the admin surface runs as the same user, but an operator
# reading state by hand should not need root.
_MODE = 0o640


class OverlayError(RuntimeError):
    """An overlay file exists but cannot be trusted."""


def _path(directory: str | Path, name: str) -> Path:
    # `name` is an account name, validated upstream against common.schema.json's
    # userName pattern. Refuse anything that could escape the directory anyway:
    # this is the last place before a filesystem write.
    if "/" in name or name in {"", ".", ".."}:
        raise OverlayError(f"invalid overlay name: {name!r}")
    return Path(directory) / f"{name}.json"


def read_overlay(directory: str | Path, name: str) -> dict[str, Any] | None:
    """Read one account's overlay fragment, or None when there is none.

    A malformed overlay raises rather than being treated as absent: "no
    overlay" and "an overlay we cannot parse" mean very different things, and
    conflating them would silently drop an operator's authored policy.
    """

    file = _path(directory, name)
    if not file.exists():
        return None
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OverlayError(f"{file}: cannot read overlay: {exc}") from exc
    if not isinstance(data, dict):
        raise OverlayError(f"{file}: overlay is not an object")
    return data


def write_overlay(directory: str | Path, name: str, fragment: dict[str, Any]) -> Path:
    """Write one account's overlay fragment atomically."""

    target = _path(directory, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(fragment, indent=2, sort_keys=True) + "\n"

    handle, temporary = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            # mkstemp gives 0600; widen before the rename so the file is
            # readable by the group that operates the appliance.
            os.fchmod(stream.fileno(), _MODE)
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise

    _log.info("overlay written", fields={"account": name})
    return target


def delete_overlay(directory: str | Path, name: str) -> bool:
    """Remove an account's overlay. Returns whether one existed."""

    file = _path(directory, name)
    try:
        file.unlink()
    except FileNotFoundError:
        return False
    _log.info("overlay removed", fields={"account": name})
    return True


def list_overlays(directory: str | Path) -> list[str]:
    """Account names that have an overlay, sorted."""

    base = Path(directory)
    if not base.is_dir():
        return []
    return sorted(path.stem for path in base.glob("*.json"))
