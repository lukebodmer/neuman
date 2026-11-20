"""Talking to the admin socket.

One HTTP client over an `AF_UNIX` transport. There is no authentication step
because there is nothing to send: the kernel tells the server which uid opened
the connection, and the server resolves it to an account (see the appliance's
`adminapi/peercred.py`). The CLI's credential is the process it runs as.

That is the whole reason the admin surface is a socket. A token would have to
be stored somewhere, and anything stored can be copied off the machine; a uid
on a connection cannot be. It also means this client has no configuration
beyond the socket path — nothing to get wrong, nothing to leak.

**Errors are the server's words, not ours.** When the server refuses, it knows
why and this does not. Re-describing a 403 as "permission denied" loses the
detail the admin needs, so the message is passed through and the exit code
carries the category.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["AdminClient", "AdminError", "Unreachable"]

DEFAULT_SOCKET = Path("/run/neuman/admin/admin.sock")

# A request against a local socket either answers quickly or is not going to.
# The exception is a route that runs `pg_dump` or `pg_restore` inside the
# request: on a database with history that takes minutes, and a deadline shorter
# than the work made the command report failure while the work carried on
# server-side — so the obvious retry raced the first attempt (N166). A caller
# that knows its route is slow passes `read_timeout=None` and keeps the
# connect deadline, which is what actually detects a missing server.
TIMEOUT_S = 30.0


class AdminError(Exception):
    """The server answered, and the answer was a refusal."""

    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self.payload = payload
        super().__init__(self._describe())

    def _describe(self) -> str:
        if isinstance(self.payload, dict):
            error = self.payload.get("error", "error")
            detail = self.payload.get("detail")
            return f"{error}: {detail}" if detail else str(error)
        return f"http {self.status}"


class Unreachable(Exception):
    """The socket could not be reached at all.

    Distinct from `AdminError` because the admin's next move is different:
    a refusal means "you may not", and this means "the appliance is not there"
    — most often that the server is not running, or that this account is not
    in the `neuman` group and so cannot open the socket.
    """


class AdminClient:
    """Requests over the admin socket, returning decoded JSON."""

    def __init__(self, socket_path: Path = DEFAULT_SOCKET) -> None:
        self.socket_path = socket_path

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        params: dict[str, str] | None = None,
        read_timeout: float | None = TIMEOUT_S,
    ) -> Any:
        import httpx

        # `exists()` raises rather than returning False when the *directory*
        # is not traversable, which is the ordinary case for an account that
        # is not in the `neuman` group — the admin directory is 0750. A
        # traceback is the wrong answer to "you may not reach this", so both
        # outcomes become the same refusal.
        try:
            present = self.socket_path.exists()
        except OSError as exc:
            raise Unreachable(f"cannot reach {self.socket_path}: {exc}") from exc
        if not present:
            raise Unreachable(f"no admin socket at {self.socket_path}")

        # `verify=False` because there is no TLS on a Unix socket and nothing
        # to verify — the peer is the kernel's answer, not a certificate's.
        # httpx otherwise builds a default SSL context at construction, which
        # reads a CA bundle from the filesystem and fails where there is none.
        transport = httpx.HTTPTransport(uds=str(self.socket_path), verify=False)
        limits = httpx.Timeout(read_timeout, connect=TIMEOUT_S, pool=TIMEOUT_S)
        try:
            with httpx.Client(transport=transport, timeout=limits, verify=False) as client:
                response = client.request(
                    method,
                    # The host is ignored — the transport is the socket — but
                    # httpx requires an absolute URL to build a request.
                    f"http://admin{path}",
                    json=body,
                    params=params,
                )
        except (httpx.HTTPError, OSError) as exc:
            # OSError as well as HTTPError: connecting to a socket the caller
            # may not open raises PermissionError, and that is a reachability
            # answer rather than a refusal the server issued.
            raise Unreachable(f"cannot reach {self.socket_path}: {exc}") from exc

        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            payload = {"error": "unreadable_response", "detail": response.text[:200]}

        if response.status_code >= 400:
            raise AdminError(response.status_code, payload)
        return payload
