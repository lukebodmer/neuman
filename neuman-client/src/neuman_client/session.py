"""The client's session with one core: the cookie it stores and how to obtain it.

The client holds the one credential the core cares about — the session cookie
`/api/login` returns — and the browser never sees it. The cookie lives in the
client's state directory, mode `0600`, and a login replaces it. The password used
to obtain it is never stored.

This is the client's own state, not a grant: it proves which account the daemon
acts for, and the core decides everything else.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

__all__ = ["COOKIE", "Session", "SessionError", "unreachable"]

COOKIE = "neuman_session"


class SessionError(RuntimeError):
    """A session could not be obtained or read."""


def unreachable(server: str, reason: object) -> str:
    """Say why the core did not answer, in terms of what to do about it."""

    if isinstance(reason, ConnectionRefusedError):
        return (
            f"nothing is answering at {server}: the core is not running, or this is the "
            "wrong address (`journalctl -u neuman-server` on the appliance says why)"
        )
    return f"cannot reach {server}: {reason}"


@dataclass(slots=True)
class Session:
    """Where the session for a core lives, and what it holds."""

    path: Path

    def cookie(self) -> str | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        value = data.get("cookie")
        return value if isinstance(value, str) and value else None

    def server(self) -> str | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        value = data.get("server")
        return value if isinstance(value, str) and value else None

    def store(self, *, server: str, cookie: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Created 0600 in one call rather than written then chmodded, so there is
        # no window in which the credential is world-readable.
        handle = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump({"server": server, "cookie": cookie}, stream)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def login(self, *, server: str, username: str, password: str) -> None:
        """Check a credential against the core and keep the cookie it returns."""

        request = urllib.request.Request(
            f"{server.rstrip('/')}/api/login",
            data=json.dumps({"username": username, "password": password}).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                cookies = response.headers.get_all("Set-Cookie") or []
        except urllib.error.HTTPError as error:
            raise SessionError(f"login refused: HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise SessionError(unreachable(server, error.reason)) from error

        for value in cookies:
            name, _, rest = value.partition("=")
            if name.strip() == COOKIE:
                self.store(server=server, cookie=rest.split(";", 1)[0])
                return
        raise SessionError("the core answered the login without a session cookie")
