"""The chat UI, served by the client and proxied to the core.

The browser talks to this daemon on loopback and never to the appliance. That is
what makes the UI same-origin with the executor, and it is why the server's
session surface — TLS, cookies, CSRF, Origin — is not part of this leg at all:
the client holds the session, and the browser holds nothing the core cares about.

`/api/*` is forwarded to the core with the session cookie, with three exceptions
answered here: `/api/jobs` and `/api/jobs/{id}/stop`, because the background
jobs they name exist in this process and nowhere else (D98); `/api/drops`,
because a dropped file is written to this machine and its bytes never go to the
core (D102); and `/api/local`, which says whether this client's own dispatch
socket is connected, refused or waiting — the one fact the core cannot answer,
because to the core a refused client and an absent one are both "no client"
(D111). `/api/login` also
captures the cookie the core set, because that is the one credential the daemon
needs and the browser should not have it; the response to the browser carries no
`Set-Cookie` of the core's. `/ws/*` is upgraded on both sides and the bytes are
pumped: nothing in the frames is parsed, so nothing in them can be mangled.

**The local API has its own credential.** Loopback is not a boundary between
accounts on a shared machine, so every `/api` and `/ws` request must carry a
cookie this daemon minted at startup. `neuman-browser` opens the URL with the
token once; the browser keeps it as a cookie and the user never sees it. Static
files need no credential — they are the same bytes for everyone.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import ssl
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .drops import MAX_DROP_BYTES, DropStore
from .jobs import Job, JobRegistry
from .link import Link
from .session import COOKIE as SESSION_COOKIE
from .session import Session, unreachable

__all__ = ["UiServer"]

_log = logging.getLogger("neuman_client.ui")

# The header block, generously: a browser sends a few kilobytes at most.
_MAX_HEADERS = 64 * 1024

# The largest body read at all. The largest legitimate one is a dropped file, and
# every body is held in memory whole, so a bigger `content-length` is answered
# before a byte of it is read rather than buffered and then refused.
_MAX_BODY = MAX_DROP_BYTES
_CHUNK = 65536

# The WebSocket handshake's fixed GUID (RFC 6455).
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

_LOOPBACK_COOKIE = "neuman_client"

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}

# The same policy the server sent when it served the UI: same-origin files only,
# nothing inline. A browser silently drops what it forbids, so the header has to
# travel with the page wherever the page is served from.
_CSP = (
    "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
    "style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'"
)


class _TooLarge(Exception):
    """A request whose declared body is over `_MAX_BODY`."""


class _Request:
    def __init__(self, method: str, target: str, headers: dict[str, str], body: bytes) -> None:
        self.method = method
        self.target = target
        self.headers = headers
        self.body = body

    @property
    def path(self) -> str:
        return urlsplit(self.target).path


def _parse_headers(block: bytes) -> tuple[str, str, dict[str, str]] | None:
    """The request line and lower-cased headers, or None if the block is not one."""

    lines = block.decode("latin-1").split("\r\n")
    if not lines or not lines[0]:
        return None
    parts = lines[0].split(" ")
    if len(parts) != 3:
        return None
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        if name:
            headers[name.strip().lower()] = value.strip()
    return parts[0], parts[1], headers


def _response(
    status: int,
    reason: str,
    *,
    content_type: str = "text/plain; charset=utf-8",
    body: bytes = b"",
    cookies: list[str] | None = None,
    location: str | None = None,
    headers: dict[str, str] | None = None,
) -> bytes:
    head = [
        f"HTTP/1.1 {status} {reason}",
        f"content-type: {content_type}",
        f"content-length: {len(body)}",
        "connection: close",
        f"content-security-policy: {_CSP}",
    ]
    if location is not None:
        head.append(f"location: {location}")
    for name, value in (headers or {}).items():
        head.append(f"{name.lower()}: {value}")
    for cookie in cookies or []:
        head.append(f"set-cookie: {cookie}")
    return ("\r\n".join(head) + "\r\n\r\n").encode("latin-1") + body


def _not_this_client() -> bytes:
    """The refusal for a request without the local token cookie.

    A 401 here does not mean the core rejected a credential: the request never
    reached it. The body names that case so the page can tell "this tab is not
    the client's browser" from "the session ended" — the login form's password
    goes through the same proxy and would fail the same way (N183).
    """

    return _response(
        401,
        "Unauthorized",
        content_type="application/json",
        body=json.dumps(
            {
                "error": "not_this_client",
                "detail": "This tab is not signed in to the local client; "
                "open the URL from ui-url.",
            }
        ).encode("utf-8"),
    )


def _job_rows(registry: JobRegistry | None) -> list[dict[str, object]]:
    """What the browser's job row draws: enough to tell two builds apart.

    `running_ms` rather than a start timestamp, so the page can turn it into an
    elapsed figure against its own clock without trusting the two machines'
    clocks to agree. The label is the one `job_output` lists — the command, or
    the first line of the code — because that is what identifies a job to a
    person.
    """

    if registry is None:
        return []
    return [
        {
            "job_id": job.id,
            "tool": job.tool,
            "label": job.label,
            "running_ms": job.background.running_ms,
        }
        for job in registry.live()
    ]


def _find(registry: JobRegistry | None, job_id: str) -> Job | None:
    """A running job by id, for the row's Stop. Ended jobs are not found."""

    if registry is None:
        return None
    return next((job for job in registry.live() if job.id == job_id), None)


class UiServer:
    """Serve the UI locally and carry its API to the core."""

    def __init__(
        self,
        *,
        web_root: str | Path,
        server_url: str,
        session: Session,
        token: str,
        port: int = 8642,
        host: str = "127.0.0.1",
        jobs: JobRegistry | None = None,
        drops: DropStore | None = None,
        link: Link | None = None,
        retry: Callable[[], None] | None = None,
    ) -> None:
        self._web_root = Path(web_root)
        self._server = server_url.rstrip("/")
        self._session = session
        self._token = token
        self._port = port
        self._host = host
        # The same registry the daemon runs jobs in. None for a UI that has no
        # executor beside it (a test, or `--ui-only`): its job row is then empty
        # rather than served from a second copy of the state.
        self._jobs = jobs
        # Where a dropped file is written (D102). None for a UI with no daemon
        # beside it: nothing would report the directory to the core, so a path
        # in it could never be attached.
        self._drops = drops
        # What the daemon knows about its own link, so the page can say why the
        # agent has no tools instead of showing a working chat with a silent
        # executor behind it (D111). None for a UI with no daemon: the page then
        # learns nothing and shows no banner.
        self._link = link
        # Ask the daemon to try again now. Called when a login stores a session,
        # because the most common refusal a retry can fix is "not signed in yet".
        self._retry = retry
        self._sweeping: asyncio.Task[None] | None = None
        self._server_socket: asyncio.AbstractServer | None = None

    @property
    def server_url(self) -> str:
        """The core this proxy forwards to."""

        return self._server

    @property
    def url(self) -> str:
        """Where the browser should open the UI, token included."""

        # The bound port, not the requested one: `0` asks the OS to choose, and
        # a URL written before the bind would name port 0 (N183).
        return f"http://127.0.0.1:{self.port}/?token={self._token}"

    @property
    def port(self) -> int:
        """The port actually bound, which is not `_port` when it was 0."""

        if self._server_socket is not None:
            return int(self._server_socket.sockets[0].getsockname()[1])
        return self._port

    async def start(self) -> None:
        self._server_socket = await asyncio.start_server(self._handle, self._host, self._port)
        # A detach removes a path from the core's list and nothing here hears
        # of it, so drops are swept when the daemon starts as well as after each
        # new one — a conversation deleted last week does not wait on the next
        # file someone happens to drop.
        self._sweep_drops()

    async def stop(self) -> None:
        if self._sweeping is not None:
            self._sweeping.cancel()
            self._sweeping = None
        if self._server_socket is not None:
            self._server_socket.close()
            await self._server_socket.wait_closed()
            self._server_socket = None

    async def serve(self) -> None:
        await self.start()
        try:
            assert self._server_socket is not None
            await self._server_socket.serve_forever()
        finally:
            await self.stop()

    # -- requests ------------------------------------------------------------

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await self._read_request(reader)
        except _TooLarge:
            writer.write(
                _response(
                    413,
                    "Content Too Large",
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "error": "too_large",
                            "detail": f"The limit is {MAX_DROP_BYTES // (1024 * 1024)} MiB.",
                        }
                    ).encode("utf-8"),
                )
            )
            try:
                await writer.drain()
            finally:
                writer.close()
            return
        except Exception:  # noqa: BLE001 - a malformed request is not worth a task
            writer.close()
            return
        if request is None:
            writer.close()
            return

        try:
            if request.path.startswith("/ws/"):
                await self._proxy_ws(request, reader, writer)
                return
            # Answered here rather than by the core: the jobs live in this
            # process, so a round trip could only be a slower copy of what is
            # already here.
            if request.path == "/api/jobs" or request.path.startswith("/api/jobs/"):
                await self._api_jobs(request, writer)
                return
            # Local for the same reason: the bytes land on this machine, and
            # never cross to the core (D102). Everything else goes to the core.
            if request.path == "/api/drops":
                await self._api_drops(request, writer)
                return
            # Local because the core cannot answer it: a client it refused and a
            # client that never started are the same fact from over there
            # (D111).
            if request.path == "/api/local" or request.path == "/api/local/retry":
                await self._api_local(request, writer)
                return
            if request.path.startswith("/api/"):
                await self._proxy_api(request, writer)
                return
            await self._serve_static(request, writer)
        except Exception:  # noqa: BLE001 - never leave a connection open on a bug
            try:
                writer.write(_response(502, "Bad Gateway", body=b"proxy error"))
            except OSError:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except OSError:
                pass

    async def _read_request(self, reader: asyncio.StreamReader) -> _Request | None:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
        if len(head) > _MAX_HEADERS:
            return None
        parsed = _parse_headers(head[: -len(b"\r\n\r\n")])
        if parsed is None:
            return None
        method, target, headers = parsed
        length = int(headers.get("content-length") or 0)
        if length > _MAX_BODY:
            raise _TooLarge
        body = await reader.readexactly(length) if length else b""
        return _Request(method, target, headers, body)

    def _authorised(self, request: _Request) -> bool:
        cookies = request.headers.get("cookie", "")
        for part in cookies.split(";"):
            name, _, value = part.strip().partition("=")
            if name == _LOOPBACK_COOKIE and value == self._token:
                return True
        return False

    async def _serve_static(self, request: _Request, writer: asyncio.StreamWriter) -> None:
        # The token bootstrap: the browser's first hit carries it in the query,
        # and this is the only place a cookie is set for the browser itself.
        query = parse_qs(urlsplit(request.target).query)
        if query.get("token", [None])[0] == self._token:
            writer.write(
                _response(
                    303,
                    "See Other",
                    location="/",
                    cookies=[
                        f"{_LOOPBACK_COOKIE}={self._token}; Path=/; HttpOnly; SameSite=Strict"
                    ],
                )
            )
            return

        relative = request.path.lstrip("/") or "index.html"
        path = (self._web_root / relative).resolve()
        # A resolved path outside the root is a traversal, not a file.
        if not str(path).startswith(str(self._web_root.resolve())) or not path.is_file():
            writer.write(_response(404, "Not Found", body=b"not found"))
            return
        writer.write(
            _response(
                200,
                "OK",
                content_type=_CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
                body=path.read_bytes(),
            )
        )

    async def _api_jobs(self, request: _Request, writer: asyncio.StreamWriter) -> None:
        """The jobs running now, and stopping one.

        Local, because the jobs are: the model's `job_output` and this row read
        the same registry, so a person can stop what the model started without
        asking the model to (D98). The loopback cookie is the credential, as on
        every `/api` request, and a mutation additionally has to come from this
        origin — same-origin is what the cookie's `SameSite=Strict` and the
        core's own origin guard assume, and this route is not behind the core's.
        """

        if not self._authorised(request):
            writer.write(_not_this_client())
            return

        if request.path == "/api/jobs":
            if request.method != "GET":
                writer.write(_response(405, "Method Not Allowed"))
                return
            writer.write(
                _response(
                    200,
                    "OK",
                    content_type="application/json",
                    body=json.dumps({"jobs": _job_rows(self._jobs)}).encode("utf-8"),
                )
            )
            return

        if request.method != "POST":
            writer.write(_response(405, "Method Not Allowed"))
            return
        if not self._same_origin(request):
            writer.write(_response(403, "Forbidden", body=b"origin_not_allowed"))
            return

        prefix = "/api/jobs/"
        tail = request.path[len(prefix) :] if request.path.startswith(prefix) else ""
        job_id = tail[: -len("/stop")] if tail.endswith("/stop") else ""
        if not job_id or "/" in job_id:
            # Only `POST /api/jobs/<id>/stop` kills. A bare `POST /api/jobs/<id>`
            # is a shape this API does not have, and reading it as one would
            # stop a job on a malformed request.
            writer.write(_response(404, "Not Found"))
            return
        job = _find(self._jobs, job_id)
        if job is None:
            writer.write(
                _response(
                    404,
                    "Not Found",
                    content_type="application/json",
                    body=b'{"error": "not_found"}',
                )
            )
            return
        job.background.kill()
        writer.write(
            _response(
                200,
                "OK",
                content_type="application/json",
                body=json.dumps({"stopped": job.id}).encode("utf-8"),
            )
        )

    async def _api_drops(self, request: _Request, writer: asyncio.StreamWriter) -> None:
        """Store a file dropped on the window and answer with its path (D102).

        The raw body is the file and `?name=` is its name: one file per request,
        so there is no multipart to parse and nothing to buffer beyond the body
        `_read_request` already capped. The path that comes back is attached by
        the page through the ordinary PATCH, where the core checks it is in this
        client's `drops` directory — this route grants nothing on its own.
        """

        if not self._authorised(request):
            writer.write(_not_this_client())
            return
        if request.method != "POST":
            writer.write(_response(405, "Method Not Allowed"))
            return
        if not self._same_origin(request):
            writer.write(_response(403, "Forbidden", body=b"origin_not_allowed"))
            return
        if self._drops is None:
            writer.write(_response(404, "Not Found", body=b"this client keeps no drops"))
            return
        name = parse_qs(urlsplit(request.target).query).get("name", [""])[0]
        if not name:
            writer.write(
                _response(
                    400,
                    "Bad Request",
                    content_type="application/json",
                    body=b'{"error": "invalid_request", "detail": "A drop needs a name."}',
                )
            )
            return

        path = await asyncio.to_thread(self._drops.save, name, request.body)
        writer.write(
            _response(
                200,
                "OK",
                content_type="application/json",
                body=json.dumps({"path": str(path)}).encode("utf-8"),
            )
        )
        self._sweep_drops()

    def _sweep_drops(self) -> None:
        """Delete the drops no conversation attaches, in the background.

        The list comes from the core, which is the only place every branch's
        attachments are; a sweep with anything less than its complete answer
        would delete files still in use, so any failure — signed out, core
        unreachable, a shape this client does not know — keeps everything.
        """

        if self._drops is None or (self._sweeping is not None and not self._sweeping.done()):
            return
        drops = self._drops

        async def sweep() -> None:
            ask = _Request("GET", "/api/attachments", {"accept": "application/json"}, b"")
            try:
                status, _headers, body, _cookies = await asyncio.to_thread(self._forward, ask)
                if status != 200:
                    return
                attached = json.loads(body).get("attachments")
                if not isinstance(attached, list) or not all(
                    isinstance(entry, str) for entry in attached
                ):
                    return
                removed = await asyncio.to_thread(drops.sweep, attached)
            except Exception as error:  # noqa: BLE001 - a sweep is never worth a crash
                _log.warning("could not sweep dropped files: %s", error)
                return
            if removed:
                _log.info("removed %d dropped file(s) no conversation attaches", len(removed))

        self._sweeping = asyncio.create_task(sweep())

    def _same_origin(self, request: _Request) -> bool:
        """A browser's mutation has to name the origin this request was sent to.

        The core's rule (`http/middleware.py`), because this route is in front
        of it: compare the `Origin` against the `Host` the browser addressed,
        not against the address the server happens to have bound — the two
        differ behind a port forward or a `--ui-host`, and comparing with the
        bound one would refuse a same-origin request.

        A request with no `Origin` at all is not a browser's (curl, a script),
        and it already had to carry the loopback cookie. A browser always sends
        one on a POST, so a cross-site page cannot reach the kill: it has the
        cookie only if `SameSite=Strict` let it, and it does not have this.
        """

        origin = request.headers.get("origin")
        host = request.headers.get("host")
        return origin is None or (host is not None and origin == f"http://{host}")

    async def _api_local(self, request: _Request, writer: asyncio.StreamWriter) -> None:
        """What this client knows about its own link, and the way to retry it.

        Served here rather than forwarded because the core cannot answer it: a
        client it refused and a client that never started are the same fact from
        its side, and the person's window is the one place the difference is
        visible (D111). No credential is involved beyond the loopback cookie the
        rest of `/api` already needs — the answer names versions, never a secret.
        """

        if not self._authorised(request):
            writer.write(_not_this_client())
            return

        if request.path == "/api/local/retry":
            if request.method != "POST":
                writer.write(_response(405, "Method Not Allowed"))
                return
            if not self._same_origin(request):
                writer.write(_response(403, "Forbidden", body=b"origin_not_allowed"))
                return
            if self._retry is not None:
                self._retry()
            writer.write(_response(204, "No Content"))
            return

        if request.method != "GET":
            writer.write(_response(405, "Method Not Allowed"))
            return
        if self._link is None:
            # A UI with no executor beside it: nothing to report, and a banner
            # about a daemon that was never started would be a lie.
            body = json.dumps({"state": "ui_only", "visible": False}).encode("utf-8")
        else:
            body = json.dumps(self._link.to_json()).encode("utf-8")
        writer.write(
            _response(
                200,
                "OK",
                content_type="application/json",
                body=body,
                headers={"Cache-Control": "no-store"},
            )
        )

    async def _proxy_api(self, request: _Request, writer: asyncio.StreamWriter) -> None:
        if not self._authorised(request):
            writer.write(_not_this_client())
            return

        status, headers, body, set_cookies = await asyncio.to_thread(self._forward, request)

        # A login's cookie is the daemon's, not the browser's. Keep it here and
        # hand the browser a plain success.
        for value in set_cookies or []:
            name, _, rest = value.partition("=")
            if name.strip() == SESSION_COOKIE:
                self._session.store(server=self._server, cookie=rest.split(";", 1)[0])
                # A daemon refused for want of a session can stop waiting now;
                # the person just supplied the one thing that could change the
                # answer.
                if self._retry is not None:
                    self._retry()

        writer.write(
            _response(
                status,
                "OK" if status < 400 else "Error",
                content_type=headers.get("content-type", "application/octet-stream"),
                body=body,
            )
        )

    def _forward(
        self, request: _Request
    ) -> tuple[int, dict[str, str], bytes, list[str] | None]:
        """One API call, in a thread. `urllib` handles TLS, chunking and gzip."""

        import urllib.error
        import urllib.request

        url = f"{self._server}{request.path}"
        if urlsplit(request.target).query:
            url += f"?{urlsplit(request.target).query}"

        forwarded = urllib.request.Request(
            url, data=request.body or None, method=request.method
        )
        # `x-csrf-token` travels with the request because the core re-checks it
        # per unsafe request. The browser holds it — it came back in the login
        # body — and the daemon does not, so dropping it here would make every
        # write fail with a 403 that looks like a policy refusal.
        for name in ("content-type", "accept", "accept-language", "x-csrf-token"):
            if name in request.headers:
                forwarded.add_header(name, request.headers[name])
        cookie = self._session.cookie()
        if cookie:
            forwarded.add_header("Cookie", f"{SESSION_COOKIE}={cookie}")

        try:
            with urllib.request.urlopen(forwarded, timeout=120) as response:
                return (
                    response.status,
                    {k.lower(): v for k, v in response.headers.items()},
                    response.read(),
                    response.headers.get_all("Set-Cookie"),
                )
        except urllib.error.HTTPError as error:
            return (
                error.code,
                {k.lower(): v for k, v in error.headers.items()},
                error.read(),
                error.headers.get_all("Set-Cookie"),
            )
        except urllib.error.URLError as error:
            detail = json.dumps(
                {"error": "core_unreachable", "detail": unreachable(self._server, error.reason)}
            )
            return 502, {"content-type": "application/json"}, detail.encode(), None

    # -- the session WebSocket ------------------------------------------------

    async def _proxy_ws(
        self,
        request: _Request,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        if not self._authorised(request):
            writer.write(_not_this_client())
            return

        key = request.headers.get("sec-websocket-key")
        if not key:
            writer.write(_response(400, "Bad Request", body=b"not a websocket upgrade"))
            return

        parts = urlsplit(self._server)
        secure = parts.scheme == "https"
        port = parts.port or (443 if secure else 80)
        context = ssl.create_default_context() if secure else None
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                parts.hostname,
                port,
                ssl=context,
                server_hostname=parts.hostname if secure else None,
            )
        except OSError:
            writer.write(_response(502, "Bad Gateway", body=b"cannot reach the core"))
            return

        target = request.path
        if urlsplit(request.target).query:
            target += f"?{urlsplit(request.target).query}"
        cookie = self._session.cookie()
        # Sixteen bytes, which is the only length RFC 6455 allows — a server is
        # entitled to check, and the core's does. Anything longer reads as a
        # malformed handshake: 400 upstream, which this proxy can only report as
        # a 502, so every session socket fails while `/api` works.
        upstream_key = base64.b64encode(secrets.token_bytes(16)).decode()
        request_lines = [
            f"GET {target} HTTP/1.1",
            f"host: {parts.netloc}",
            "upgrade: websocket",
            "connection: Upgrade",
            f"sec-websocket-key: {upstream_key}",
            "sec-websocket-version: 13",
        ]
        if cookie:
            request_lines.append(f"cookie: {SESSION_COOKIE}={cookie}")
        upstream_writer.write(("\r\n".join(request_lines) + "\r\n\r\n").encode("latin-1"))
        await upstream_writer.drain()

        try:
            block = await asyncio.wait_for(upstream_reader.readuntil(b"\r\n\r\n"), timeout=30)
        except (TimeoutError, asyncio.IncompleteReadError):
            upstream_writer.close()
            writer.write(_response(502, "Bad Gateway", body=b"the core refused the upgrade"))
            return

        if not block.startswith(b"HTTP/1.1 101"):
            upstream_writer.close()
            writer.write(_response(502, "Bad Gateway", body=b"the core refused the upgrade"))
            return

        # Accept the browser with its own key, then pump raw bytes. The framing
        # is symmetric and per-direction, so relaying it untouched is correct —
        # and means this proxy cannot disagree with what it carries.
        accept = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
        writer.write(
            (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "upgrade: websocket\r\n"
                "connection: Upgrade\r\n"
                f"sec-websocket-accept: {accept}\r\n\r\n"
            ).encode("latin-1")
        )
        await writer.drain()

        async def pump(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
            try:
                while True:
                    data = await source.read(_CHUNK)
                    if not data:
                        return
                    sink.write(data)
                    await sink.drain()
            except OSError:
                return

        try:
            await asyncio.gather(
                pump(reader, upstream_writer),
                pump(upstream_reader, writer),
                return_exceptions=True,
            )
        finally:
            upstream_writer.close()
            try:
                await upstream_writer.wait_closed()
            except OSError:
                pass
