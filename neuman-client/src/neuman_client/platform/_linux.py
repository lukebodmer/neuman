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


def browser_is_confined(browser: str) -> bool:
    """Whether `browser` runs under snap confinement — Ubuntu's Chromium.

    Two spellings reach the same snap, and neither can be found by resolving
    it: `/usr/bin/chromium-browser` is a package wrapper that runs
    `snap run chromium`, and `/snap/bin/chromium` is a symlink to
    `/usr/bin/snap`, so `realpath()` loses the fact rather than finding it.

    The name `chromium-browser` is taken to mean the snap on every host,
    including one where it is a real browser. A false positive only puts
    Neuman's profile in the snap's own `common` directory, which any browser
    can use; a false negative is the failure this exists to prevent (N184).
    """

    return (
        browser.startswith("/snap/")
        or os.path.realpath(browser) == "/usr/bin/snap"
        or Path(browser).name == "chromium-browser"
    )


def browser_profile_dir(browser: str | None = None) -> Path:
    """Neuman's profile, somewhere the browser that opens it can reach.

    A snap-confined Chromium reaches only non-hidden paths under `$HOME`; its
    `home` interface grants no more. `~/.local/share/neuman/browser` is then a
    directory it can never open, and what it says instead is exit 21 and a
    message about `SingletonLock` — neither of which names the confinement
    (N184). The snap's own `common` directory is writable by it and is an
    ordinary directory an unconfined browser can use too, so the profile's
    location follows the browser rather than being one path for both.

    Both spellings of the snap are the snap named `chromium`, so the name here
    is the snap's, not the wrapper's.
    """

    if browser and browser_is_confined(browser):
        return home_dir() / "snap" / "chromium" / "common" / "neuman-browser"
    return home_dir() / ".local" / "share" / "neuman" / "browser"


def browser_profile_refusal(browser: str | None, profile: Path) -> str | None:
    """Why `browser` cannot open `profile`, or None when it can.

    An unconfined browser can open anything the user can. A confined one opens
    non-hidden paths under `$HOME` and nothing else, so a profile outside that
    is not a slower path but an unopenable one. This sentence is the only place
    the reason can appear: Chromium's own names the lock file it could not
    create, not the interface that stopped it.
    """

    if not browser or not browser_is_confined(browser):
        return None

    home = Path(os.path.realpath(home_dir()))
    resolved = Path(os.path.realpath(profile.expanduser()))
    try:
        relative = resolved.relative_to(home)
    except ValueError:
        return (
            f"{profile} is outside {home}, and {browser} is snap-confined: "
            f"the snap's home interface reaches only $HOME"
        )
    hidden = next((part for part in relative.parts if part.startswith(".")), None)
    if hidden is None:
        return None
    return (
        f"{profile} is under the hidden {hidden!r} in {home}, and {browser} is "
        f"snap-confined: the snap's home interface reaches only non-hidden paths (N184)"
    )


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
