"""Which code this process is running, and which code is on disk.

The client is a package installed by `apt` and a process started by a systemd
user unit. `apt` replaces the files; nothing restarts a per-user unit, and the
process keeps the modules it imported. So "installed" and "running" are two
different facts, and after an upgrade they disagree until this process exits
(D111).

`NEUMAN_CLIENT_BUILD_ID` is what the running process was started from — the deb
wrapper exports it, the Nix wrapper can too — and `/usr/share/neuman-client/
build-id` is what is on disk now. A process notices the difference and restarts
itself at the next idle moment; there is no root component and no reach into
another person's session, which is exactly what D108 requires.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["BUILD_ID_FILE", "installed_build_id", "running_build_id"]

# Written by the package beside the modules. The path is the deb's; a Nix
# install has no such file and sets the environment instead, so a Nix client
# simply never sees a change under it — home-manager restarts it with the
# session, which is the same outcome by a different route.
BUILD_ID_FILE = Path("/usr/share/neuman-client/build-id")


def running_build_id() -> str:
    """The build this process started from, or "" when nothing says."""

    from_env = os.environ.get("NEUMAN_CLIENT_BUILD_ID", "").strip()
    if from_env:
        return from_env
    return installed_build_id()


def installed_build_id() -> str:
    """The build on disk now, read every time because `apt` may have replaced it."""

    try:
        return BUILD_ID_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
