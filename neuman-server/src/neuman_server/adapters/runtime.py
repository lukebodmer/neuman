"""The runtime host and the fetcher, over their control sockets (D78, D79).

The server has neither a GPU nor write access to the weights, and these are how
it asks the two units that do. Both are HTTP on a Unix socket in the `neuman`
group; the server is the only client.

What the server *tells* the runtime host is not here: that is the document
`composition/runtime.py` writes, which the host re-reads when it changes. This
is the asking half — what is loaded, load this, unload that — and the fetcher's
whole surface.

Every call answers rather than raises when the unit is absent or down: an
appliance without a GPU has no runtime host, and the panel reporting that is
the ordinary case, not a failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..foundation import get_logger

__all__ = ["FetchClient", "RuntimeClient", "uds_transport"]

_log = get_logger("neuman.server.adapters.runtime")

# Any host name: the transport is the socket, and httpx needs a URL to build a
# request line from.
_BASE = "http://runtime"


def uds_transport(path: Path) -> Any:
    import httpx

    return httpx.AsyncHTTPTransport(uds=str(path))


async def _call(
    socket: Path, method: str, path: str, body: dict[str, Any] | None, timeout: float
) -> tuple[int, dict[str, Any]]:
    import httpx

    try:
        async with httpx.AsyncClient(
            transport=uds_transport(socket), base_url=_BASE, timeout=timeout
        ) as client:
            response = await client.request(method, path, json=body)
    except httpx.HTTPError as exc:
        return 503, {"error": "unreachable", "detail": f"{socket.name}: {exc}"}
    try:
        parsed = response.json()
    except ValueError:
        parsed = {"error": response.text[:300]}
    return response.status_code, parsed if isinstance(parsed, dict) else {"value": parsed}


@dataclass(frozen=True, slots=True)
class RuntimeClient:
    """Asks `neuman-runtime` what is loaded, and to load or unload a model."""

    socket: Path
    timeout_s: float = 10.0

    async def status(self) -> dict[str, Any] | None:
        """The host's status, or None when it does not answer."""

        code, body = await _call(self.socket, "GET", "/status", None, self.timeout_s)
        return body if code == 200 else None

    async def load(self, name: str) -> tuple[int, dict[str, Any]]:
        # Returns once the router has the request, not once the weights are
        # resident; status reports that.
        return await _call(self.socket, "POST", "/load", {"name": name}, self.timeout_s)

    async def unload(self, name: str) -> tuple[int, dict[str, Any]]:
        return await _call(self.socket, "POST", "/unload", {"name": name}, self.timeout_s)


@dataclass(frozen=True, slots=True)
class FetchClient:
    """Asks `neuman-fetch` to download or verify a preset's weights."""

    socket: Path
    timeout_s: float = 10.0

    async def status(self) -> dict[str, Any] | None:
        code, body = await _call(self.socket, "GET", "/status", None, self.timeout_s)
        return body if code == 200 else None

    async def fetch(self, preset: str) -> tuple[int, dict[str, Any]]:
        return await _call(self.socket, "POST", "/fetch", {"preset": preset}, self.timeout_s)

    async def cancel(self, preset: str) -> tuple[int, dict[str, Any]]:
        return await _call(self.socket, "POST", "/cancel", {"preset": preset}, self.timeout_s)

    async def delete(self, preset: str) -> tuple[int, dict[str, Any]]:
        return await _call(self.socket, "POST", "/delete", {"preset": preset}, self.timeout_s)
