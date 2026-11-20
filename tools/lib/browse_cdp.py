"""A Chrome DevTools Protocol client, in the standard library alone.

Neuman's tools run on `python3.withPackages (_: [ ])` — a bare interpreter. Adding
`websockets` here would mean a library declared for the server and not for the
tools, which is exactly the shape of N47: a dependency that exists in one place,
is missing in another, and fails only at runtime.

CDP is JSON over a text WebSocket. The subset needed is small enough to write:
a client handshake, text frames, and masking. No fragmentation of outbound frames
(commands are short), no compression (Chromium does not offer it unnegotiated), and
inbound continuation frames are assembled because a full-page screenshot arrives in
several.

The endpoint is a Unix socket when one is configured, a loopback TCP port
otherwise. A socket is preferred because its filesystem mode is the access
control: a `0600` socket under the user's runtime directory is reachable by that
user and no other, where a loopback port is reachable by every process on the
machine. The browser itself still speaks TCP; a client-side bridge (part of
`neuman-browser`) exposes it on the socket.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import socket
import struct
import time
from typing import Any

__all__ = ["CdpError", "Endpoint", "Session", "create_page", "has_page"]


class CdpError(RuntimeError):
    """The browser could not be driven. The message is safe to show a user."""


class _UnixHTTPConnection(http.client.HTTPConnection):
    """An HTTP connection over an `AF_UNIX` socket.

    `HTTPConnection` only knows how to open a TCP socket; overriding `connect`
    is the whole of what it takes to speak HTTP to a Unix socket. The host
    passed to the parent is a placeholder — it never reaches the wire, only the
    `Host` header, which the browser ignores.
    """

    def __init__(self, socket_path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._socket_path)


class Endpoint:
    """Where Chromium's DevTools endpoint is reachable.

    `socket_path` wins when set; otherwise the tool dials `host:port`. The two
    are not interchangeable in what they cost: a socket can be owned by one
    user, a loopback port cannot.
    """

    def __init__(
        self,
        *,
        socket_path: str | None = None,
        host: str = "127.0.0.1",
        port: int = 9222,
    ) -> None:
        self.socket_path = socket_path
        self.host = host
        self.port = port

    @classmethod
    def from_environment(cls) -> Endpoint:
        """The endpoint the client configured, or a loopback port.

        `NEUMAN_BROWSE_SOCKET` is what the client sets, because the client
        starts both the browser bridge and this tool. `NEUMAN_BROWSE_HOST` and
        `NEUMAN_BROWSE_PORT` are for a Chromium started by hand with
        `--remote-debugging-port`, which is the only case with no bridge.
        """

        socket_path = os.environ.get("NEUMAN_BROWSE_SOCKET")
        if socket_path:
            return cls(socket_path=socket_path)
        return cls(
            host=os.environ.get("NEUMAN_BROWSE_HOST", "127.0.0.1"),
            port=int(os.environ.get("NEUMAN_BROWSE_PORT", "9222")),
        )

    @property
    def label(self) -> str:
        """How the endpoint is named in an error the user has to act on."""

        return self.socket_path if self.socket_path else f"{self.host}:{self.port}"

    def http_connection(self, timeout: float = 5.0) -> http.client.HTTPConnection:
        if self.socket_path:
            return _UnixHTTPConnection(self.socket_path, timeout)
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout)


def _get_json(endpoint: Endpoint, path: str, timeout: float = 5.0) -> Any:
    connection = endpoint.http_connection(timeout)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        if response.status != 200:
            raise CdpError(f"{path} at {endpoint.label} returned {response.status}")
        return json.loads(body)
    except CdpError:
        raise
    except Exception as error:
        raise CdpError(f"could not reach the browser at {endpoint.label}: {error}") from error
    finally:
        connection.close()


def _targets(endpoint: Endpoint) -> list[dict[str, Any]]:
    return _get_json(endpoint, "/json/list")


def _browser_url(endpoint: Endpoint) -> str:
    """The browser-level debugger URL, for commands that act on tabs themselves.

    Distinct from a *page* target's URL: creating and enumerating tabs is a
    browser-scoped command, and the page endpoint will not answer it.
    """

    version = _get_json(endpoint, "/json/version")
    url = version.get("webSocketDebuggerUrl")
    if not url:
        raise CdpError("the browser exposes no debugger URL")
    return url


def _command(
    endpoint: Endpoint, method: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    """One CDP command over a fresh connection, for work done before a Session."""

    ws = _WebSocket(_browser_url(endpoint), endpoint)
    try:
        ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
        # Events interleave with responses, so the id is matched rather than
        # assumed to be next — the same rule `Session._call` follows.
        while True:
            message = json.loads(ws.recv())
            if message.get("id") != 1:
                continue
            if "error" in message:
                raise CdpError(f"{method} failed: {message['error'].get('message', 'unknown')}")
            return message.get("result", {})
    finally:
        ws.close()


def has_page(endpoint: Endpoint, target_id: str) -> bool:
    """Whether a tab with this id is still a live page.

    Checked before reuse: the user can close Neuman's tab, and a remembered id
    that no longer resolves must mean "open a new one", not "fail the search".
    """

    return any(t.get("type") == "page" and t.get("id") == target_id for t in _targets(endpoint))


def create_page(endpoint: Endpoint, url: str = "about:blank") -> str:
    """Open a new tab and return its target id.

    A tab rather than a window, and a *background* tab rather than the active
    one: the browser is the user's, and opening a tab must not yank them off the
    page they were reading. `background` is what keeps the tab from being
    activated; `newWindow` is false so the tab lands in the window they are
    already in.
    """

    result = _command(endpoint, "Target.createTarget", {"url": url, "background": True})
    target_id = result.get("targetId")
    if not target_id:
        raise CdpError("the browser did not return a target id for the new tab")
    return target_id


class _WebSocket:
    """The client half of RFC 6455, for text frames only."""

    def __init__(self, url: str, endpoint: Endpoint, *, timeout: float = 60.0) -> None:
        if not url.startswith("ws://"):
            raise CdpError(f"unexpected debugger URL scheme: {url}")
        rest = url[len("ws://") :]
        hostport, _, path = rest.partition("/")

        # The URL's host:port is what the browser advertises, and it is used for
        # the `Host` header and nothing else. Where to *connect* is the
        # endpoint's decision, because a socket bridge makes the advertised
        # loopback port a fiction.
        if endpoint.socket_path:
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.settimeout(timeout)
            self._sock.connect(endpoint.socket_path)
        else:
            self._sock = socket.create_connection(
                (endpoint.host, endpoint.port), timeout=timeout
            )
        self._buffer = b""

        # A fixed key would be accepted too — the handshake's Sec-WebSocket-Accept is
        # not a security property — but a random one keeps any intermediary honest.
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET /{path} HTTP/1.1\r\n"
            f"Host: {hostport}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(request.encode("ascii"))

        # Read exactly to the end of the headers: anything after them is the first
        # frame, and discarding it would lose a response.
        while b"\r\n\r\n" not in self._buffer:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise CdpError("the browser closed the connection during the handshake")
            self._buffer += chunk
        head, _, self._buffer = self._buffer.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n")[0]:
            raise CdpError(f"the browser refused the upgrade: {head.splitlines()[0]!r}")

    def send(self, text: str) -> None:
        payload = text.encode("utf-8")
        header = bytearray([0x81])  # FIN + text opcode
        mask = os.urandom(4)
        length = len(payload)
        # Client frames must be masked. The length field is the only part with three
        # encodings, and getting the 126/127 boundary wrong is silent corruption.
        if length < 126:
            header.append(0x80 | length)
        elif length < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        header += mask
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def _read(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise CdpError("the browser closed the connection")
            self._buffer += chunk
        out, self._buffer = self._buffer[:count], self._buffer[count:]
        return out

    def recv(self) -> str:
        """One message, reassembled from continuation frames if it was split."""

        parts: list[bytes] = []
        while True:
            first, second = self._read(2)
            final = bool(first & 0x80)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            # Server frames are never masked; if one were, this would need the key.
            data = self._read(length) if length else b""

            if opcode == 0x8:  # close
                raise CdpError("the browser closed the connection")
            if opcode == 0x9:  # ping -> pong, or Chromium eventually gives up
                self._sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
                continue
            if opcode == 0xA:  # pong
                continue

            parts.append(data)
            if final:
                return b"".join(parts).decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self._sock.sendall(bytes([0x88, 0x80]) + os.urandom(4))
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


class Session:
    """One attached page, with the handful of CDP commands this tool needs."""

    def __init__(
        self, endpoint: Endpoint, *, target_id: str | None = None, timeout: float = 60.0
    ) -> None:
        pages = [t for t in _targets(endpoint) if t.get("type") == "page"]
        if target_id is not None:
            # An explicit tab, which is how `browse` names its own so it never
            # navigates whichever tab happened to be last — the user's, very
            # likely, since they are reading Neuman in this browser.
            target = next((t for t in pages if t.get("id") == target_id), None)
            if target is None:
                raise CdpError("the Neuman tab is no longer open")
        else:
            if not pages:
                raise CdpError("the browser has no open tab to drive")
            # The last page, which is the most recently opened — matching what the pi
            # tools did with `.at(-1)`, and what a person would expect "the tab" to mean.
            target = pages[-1]
        url = target.get("webSocketDebuggerUrl")
        if not url:
            raise CdpError("the browser tab exposes no debugger URL")

        self._ws = _WebSocket(url, endpoint, timeout=timeout)
        self._next = 0

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *_exc: object) -> None:
        self._ws.close()

    def _call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next += 1
        wanted = self._next
        self._ws.send(json.dumps({"id": wanted, "method": method, "params": params or {}}))

        # CDP interleaves events with responses, so the matching id has to be found
        # rather than assumed to be next.
        while True:
            message = json.loads(self._ws.recv())
            if message.get("id") != wanted:
                continue
            if "error" in message:
                raise CdpError(f"{method} failed: {message['error'].get('message', 'unknown')}")
            return message.get("result", {})

    def navigate(self, url: str) -> None:
        self._call("Page.enable")
        self._call("Page.navigate", {"url": url})

    def settle(self, *, seconds: float = 2.0) -> None:
        """Wait for JavaScript to render.

        A fixed pause rather than a load event, because the pages worth browsing
        finish loading and *then* render — waiting for `load` returns an empty shell
        on anything built this decade. Crude and reliable beats precise and wrong.
        """

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(0.1)

    def evaluate(self, expression: str) -> Any:
        result = self._call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"].get("text", "evaluation failed")
            raise CdpError(f"the page raised: {detail}")
        return result.get("result", {}).get("value")

    def screenshot(self, *, full_page: bool = False) -> str:
        params: dict[str, Any] = {"format": "png"}
        if full_page:
            # `captureBeyondViewport` is what makes a full-page shot possible without
            # resizing the window the user is watching.
            params["captureBeyondViewport"] = True
        return self._call("Page.captureScreenshot", params).get("data", "")
