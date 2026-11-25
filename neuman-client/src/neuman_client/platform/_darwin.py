"""macOS host facts.

macOS is not XDG: state belongs under `~/Library/Application Support`, and the
per-session runtime directory is `$TMPDIR` rather than `XDG_RUNTIME_DIR`.
Chromium is normally an app bundle, not a name on `PATH`, and Chromium must not
be given Linux's Ozone flags.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_APP_PATHS = (
    "Google Chrome.app/Contents/MacOS/Google Chrome",
    "Chromium.app/Contents/MacOS/Chromium",
)


def home_dir() -> Path:
    return Path.home()


def default_state_dir() -> Path:
    return home_dir() / "Library" / "Application Support" / "Neuman" / "client"


def runtime_dir() -> Path | None:
    value = os.environ.get("TMPDIR")
    return Path(value) if value else None


def browser_profile_dir(browser: str | None = None) -> Path:
    # macOS confines an app rather than a path: TCC withholds a folder until
    # the user consents, and then prompts, so a profile is never unopenable in
    # the way snap confinement makes it. `doctor` reports the consent it needs.
    return home_dir() / "Library" / "Application Support" / "Neuman" / "browser"


def browser_profile_refusal(browser: str | None, profile: Path) -> str | None:
    # No path is unopenable here for a packaging reason; see browser_profile_dir.
    return None


def browser_copy_source() -> Path:
    return home_dir() / "Library" / "Application Support" / "Google" / "Chrome"


def find_chromium() -> str | None:
    for root in (Path("/Applications"), home_dir() / "Applications"):
        for relative in _APP_PATHS:
            candidate = root / relative
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    for name in ("chromium", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def adopt_display() -> str | None:
    # macOS has one window server, and nothing to name in the environment.
    return "native"


def display_args() -> list[str]:
    # No Wayland and no X11 on macOS; Chromium starts its normal native window.
    return []


def service_status_hint() -> str:
    return f"launchctl print gui/{os.getuid()}/dev.neuman.client"


def runtime_hint() -> str:
    return "check TMPDIR is set and owned by this user"
