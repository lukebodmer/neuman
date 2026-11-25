"""The host facts the client needs, and the two hosts it supports.

The client runs as the human on their own machine, so it needs the local home
directory, a private place to keep state, runtime sockets for the browser
bridge and the tool package hosts, the programs those drive, and the display flags
they need. Those are the only differences between Linux and macOS; the
executor, the tool host and the daemon are the same code on both.

Only the two hosts the product supports are named. An unknown platform fails
here with a sentence, rather than half-working through POSIX assumptions.
Windows is deliberately absent until there is a client to design it for.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import _darwin, _linux

__all__ = [
    "BROWSER_PROFILE_ENV",
    "BROWSE_SOCKET_ENV",
    "HOST_PROGRAM_ENV",
    "HOST_SOCKET_ENV",
    "adopt_display",
    "browse_socket",
    "browser_copy_source",
    "browser_profile_dir",
    "browser_profile_refusal",
    "display_args",
    "find_chromium",
    "home_dir",
    "host_socket",
    "runtime_hint",
    "service_status_hint",
    "state_dir",
]

# The environment the client uses to hand the browser bridge's location to the
# `browse` tool. One spelling, read and written in one place.
BROWSE_SOCKET_ENV = "NEUMAN_BROWSE_SOCKET"

# Where the browser's profile goes, when the package's unit cannot be edited.
# The unit that starts `neuman-browser` is the package's, so an operator's
# change has to arrive through the environment, exactly as the browser's name
# does (N184).
BROWSER_PROFILE_ENV = "NEUMAN_BROWSER_PROFILE"

# What the client names to a tool package's host and to that package's tools:
# the socket the host answers on, and the program it found for the host to run
# (D101). Generic, so a package adds no variable here.
HOST_SOCKET_ENV = "NEUMAN_HOST_SOCKET"
HOST_PROGRAM_ENV = "NEUMAN_HOST_PROGRAM"

if sys.platform == "darwin":
    _host = _darwin
elif sys.platform.startswith("linux"):
    _host = _linux
else:  # pragma: no cover - the supported hosts are the two named above
    raise RuntimeError(
        f"neuman-client has no host support for {sys.platform!r}; it runs on Linux and macOS"
    )


def home_dir() -> Path:
    """This account's home on *this* machine — what `~` means here.

    Reported to the core on every connection: the homedir the core can look up
    belongs to its own host, and the two differ as soon as the client is not on
    the same machine (N145).
    """

    return _host.home_dir()


def state_dir() -> Path:
    """Where the client keeps its session, UI token and spill files.

    `NEUMAN_CLIENT_STATE` wins, because a service and a hand-run client must be
    able to point at different state while debugging. Otherwise the host's
    native location is used.
    """

    override = os.environ.get("NEUMAN_CLIENT_STATE")
    if override:
        return Path(override).expanduser()
    return _host.default_state_dir()


def browse_socket() -> Path | None:
    """The browser bridge's socket, or None when there is no private runtime.

    `NEUMAN_BROWSE_SOCKET` wins so a caller can put the socket somewhere else.
    The tool host sets the same variable on every tool it runs, so both ends
    read one value.
    """

    override = os.environ.get(BROWSE_SOCKET_ENV)
    if override:
        return Path(override).expanduser()
    runtime = _host.runtime_dir()
    return runtime / "neuman-browse.sock" if runtime else None


def host_socket(package: str) -> Path | None:
    """Where tool package `package`'s host answers, or None with no private runtime.

    In the runtime directory for the reason the browse socket is: `0700`, and
    cleared at logout, so the socket's own mode is the access control.
    """

    runtime = _host.runtime_dir()
    return runtime / f"neuman-{package}.sock" if runtime else None


def browser_profile_dir(chromium: str | None = None) -> Path:
    """The Chromium profile Neuman owns (`--profile own`).

    The browser decides which paths it can open, so the browser is an argument
    rather than an assumption (N184). `NEUMAN_BROWSER_PROFILE` overrides both:
    the package owns the unit that starts the browser, so a path change an
    operator needs has to reach it through the environment.
    """

    override = os.environ.get(BROWSER_PROFILE_ENV)
    if override:
        return Path(override).expanduser()
    return _host.browser_profile_dir(chromium)


def browser_profile_refusal(chromium: str | None, profile: Path) -> str | None:
    """Why this browser cannot open this profile, or None when it can.

    One implementation, two callers: `neuman-browser` refuses to start on it
    and `doctor` reports it, so a host cannot pass the acceptance check with a
    profile its browser could never open (D108, N184).
    """

    return _host.browser_profile_refusal(chromium, profile)


def browser_copy_source() -> Path:
    """The user's real browser profile, seeded once by `--profile copy`."""

    return _host.browser_copy_source()


def find_chromium() -> str | None:
    """The Chromium-family browser this host can start, if one is installed.

    `NEUMAN_BROWSER_CHROMIUM` wins so a package can name the executable it
    shipped or found at install time.
    """

    override = os.environ.get("NEUMAN_BROWSER_CHROMIUM")
    if override:
        return override
    return _host.find_chromium()


def adopt_display() -> str | None:
    """Make sure this process's environment names the session's display.

    Returns what was found, or None when there is no display to open a window
    on. A program started from a user service inherits what this sets.
    """

    return _host.adopt_display()


def display_args() -> list[str]:
    """The display flags Chromium needs on this host.

    Linux has to choose between Wayland and X11; macOS has no Ozone platform to
    choose and must not be handed a Linux flag.
    """

    return _host.display_args()


def service_status_hint() -> str:
    """How to ask this host whether the client service is already running."""

    return _host.service_status_hint()


def runtime_hint() -> str:
    """What to check when the browser bridge cannot create its socket."""

    return _host.runtime_hint()
