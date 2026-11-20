"""Start a tool package's host when one of its tools needs it (D101).

A host is a program in a window the person works in — Blender, for the Blender
package — that the package's tools reach over a socket. Nobody should have to
start it by hand before asking for the work: the first tool call starts it, and
the tool waits for its socket.

**Not waited for here.** A dispatch must report `started` within seconds (D15)
and a host can take longer to open, so this starts the process and returns; the
tool retries its connection while the host comes up.

**Left running.** The host is started in its own session, so it outlives the
dispatch that started it and the client too: it is the person's window, may
hold unsaved work, and closing it is theirs to do. A host already answering —
one left from a previous client, say — is used rather than started twice.

Its output goes to `<state>/hosts/<package>.log`, the one place to look when a
host opens and then stops.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
from pathlib import Path

from .platform import adopt_display
from .tools import HostSpec

__all__ = ["HostManager"]


def _answers(path: Path) -> bool:
    with contextlib.closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as probe:
        probe.settimeout(1.0)
        try:
            probe.connect(os.fspath(path))
        except OSError:
            return False
    return True


class HostManager:
    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir
        self._running: dict[str, subprocess.Popen[bytes]] = {}

    def ensure(self, spec: HostSpec) -> None:
        """Start `spec`'s host unless it is starting or answering already.

        A host this client started and that is still alive counts as starting:
        its socket may not exist yet, and a second Blender would take the socket
        from the first.
        """

        process = self._running.get(spec.package)
        if process is not None and process.poll() is None:
            return
        if _answers(spec.socket):
            return
        # A user service may not have been handed the session's display; the
        # host opens a window, so it must be named before the host inherits it.
        adopt_display()
        self._log_dir.mkdir(parents=True, exist_ok=True)
        with open(self._log_dir / f"{spec.package}.log", "ab") as log:
            self._running[spec.package] = subprocess.Popen(
                [os.fspath(spec.executable)],
                env={**spec.environment, **_display(os.environ)},
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )


def _display(environment: os._Environ[str]) -> dict[str, str]:
    """The display variables `adopt_display` may just have set in this process."""

    return {
        key: environment[key] for key in ("WAYLAND_DISPLAY", "DISPLAY") if key in environment
    }
