"""Files a person dropped on the chat window, kept where the agent can read them.

An attachment is a path the agent opens with `fs_read` (D102). A browser never
hands a page a path — a dropped file arrives as bytes and a bare name — so the
UI server, which runs as the user on the same machine as the browser, writes
the bytes here and hands back the path. The directory is client state, like
`spill/` (D42): plumbing the read tools may open, not reach any policy grants.

Stored by content, `<sha256>/<name>`, so dropping the same file twice is one
copy, and two different files that share a name do not overwrite each other.
A file lives as long as some conversation still attaches it; `sweep` is what
enforces that, against the list the core keeps.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Iterable
from pathlib import Path

__all__ = ["MAX_DROP_BYTES", "DropStore"]

# Documents and pictures fit; a dataset or a disk image is something already on
# disk, and the chooser attaches it by path with no copy at all.
MAX_DROP_BYTES = 25 * 1024 * 1024

# How long a drop no conversation names is kept. A drop staged in a new chat is
# attached only when its first message is sent — until then the core has never
# heard of it — so "unreferenced" cannot mean "delete now".
GRACE_S = 24 * 60 * 60

_MAX_NAME = 200


def _safe_name(name: str) -> str:
    """The basename a person would recognise, with nothing that makes it a path."""

    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(ch for ch in base if ch >= " " and ch != "\x7f").strip()
    if base in ("", ".", ".."):
        return "file"
    # Trimmed from the front of the stem, so the extension — which is what
    # tells the agent how to read it — survives.
    if len(base.encode("utf-8")) > _MAX_NAME:
        _stem, dot, ext = base.rpartition(".")
        keep = ext if dot and len(ext) <= 16 else ""
        head = base.encode("utf-8")[: _MAX_NAME // 2].decode("utf-8", "ignore").rstrip()
        base = head + (f".{keep}" if keep else "")
    return base


class DropStore:
    """The `drops/` directory: save bytes, sweep what nothing attaches."""

    def __init__(self, root: str | Path) -> None:
        # Resolved once, because the path handed out is compared as a string by
        # the core (`within_drops`) and through `realpath` by the tool host; a
        # state directory reached through a symlink would make the two disagree.
        self._root = Path(os.path.realpath(root))

    @property
    def root(self) -> Path:
        return self._root

    def save(self, name: str, data: bytes) -> Path:
        """Store one dropped file and return its absolute path."""

        if len(data) > MAX_DROP_BYTES:
            raise ValueError("too large")
        digest = hashlib.sha256(data).hexdigest()
        folder = self._root / digest
        path = folder / _safe_name(name)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_file():
            # The same file dropped again: a fresh grace period, not a copy.
            os.utime(path)
            return path
        partial = folder / f".{path.name}.partial"
        handle = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(partial, path)
        return path

    def sweep(self, attached: Iterable[str], *, now: float | None = None) -> list[Path]:
        """Delete every drop no conversation attaches and the grace has passed for.

        `attached` must be the account's whole list; a partial one would delete
        files still in use, so the caller sweeps only on a complete answer.
        """

        keep = {os.path.realpath(entry) for entry in attached}
        cutoff = (time.time() if now is None else now) - GRACE_S
        removed: list[Path] = []
        if not self._root.is_dir():
            return removed
        for folder in self._root.iterdir():
            if not folder.is_dir() or folder.is_symlink():
                continue
            for path in folder.iterdir():
                if str(path) in keep:
                    continue
                try:
                    if path.lstat().st_mtime > cutoff:
                        continue
                    path.unlink()
                except OSError:
                    continue
                removed.append(path)
            try:
                folder.rmdir()
            except OSError:
                pass
        return removed
