"""Linux host facts.

The XDG layout is the native one here: state under `~/.local/share`, the session
runtime directory under `XDG_RUNTIME_DIR`, and Chromium's profile under
`~/.config`.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_CHROMIUM_NAMES = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
)


def home_dir() -> Path:
    return Path.home()


def default_state_dir() -> Path:
    return home_dir() / ".local" / "share" / "neuman" / "client"


def runtime_dir() -> Path | None:
    value = os.environ.get("XDG_RUNTIME_DIR")
    return Path(value) if value else None


def browser_profile_dir() -> Path:
    return home_dir() / ".local" / "share" / "neuman" / "browser"


def browser_copy_source() -> Path:
    """The user's own profile, where the browser they run keeps one.

    `--profile copy` seeds Neuman's browser from this. Three places a Linux
    browser keeps a profile: the packaged Chromium, the Chromium snap — whose
    home is the snap's own `common` directory, not `~/.config` — and Google
    Chrome. The first that exists wins; `--copy-source` overrides, and the
    browser refuses to start in copy mode when the chosen path is absent,
    rather than silently running without the logins it was asked for.
    """

    candidates = (
        home_dir() / ".config" / "chromium",
        home_dir() / "snap" / "chromium" / "common" / "chromium",
        home_dir() / ".config" / "google-chrome",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def find_chromium() -> str | None:
    for name in _CHROMIUM_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def adopt_display() -> str | None:
    """Name the session's display in this process's environment: "wayland", "x11" or None.

    A systemd user service may not have been handed `WAYLAND_DISPLAY` at all, so
    it is found in the runtime directory the same way a session manager would,
    and set, so that whatever this process starts inherits it.
    """

    runtime = runtime_dir()
    display = os.environ.get("WAYLAND_DISPLAY")
    if not display and runtime and runtime.is_dir():
        try:
            display = next(
                name
                for name in sorted(os.listdir(runtime))
                if name.startswith("wayland-") and not name.endswith(".lock")
            )
        except (OSError, StopIteration):
            display = None
    if display:
        os.environ.setdefault("WAYLAND_DISPLAY", display)
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return None


def display_args() -> list[str]:
    """Choose Wayland or X11 for Chromium.

    Chromium does not infer its backend from `WAYLAND_DISPLAY`; it defaults to
    X11 and exits with `Missing X server or $DISPLAY` under a perfectly good
    Wayland socket (N74), so the backend is named explicitly.
    """

    display = adopt_display()
    return [f"--ozone-platform={display}"] if display else []


def service_status_hint() -> str:
    return "systemctl --user status neuman-client"


def runtime_hint() -> str:
    return "check XDG_RUNTIME_DIR is set and owned by this user"
