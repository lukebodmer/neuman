"""The local UI server: static files, the API proxy, and the local credential.

The tests use a stub core — a real HTTP server answering the API this daemon
forwards — so the proxy's rewriting, the login's cookie capture and the local
token check are exercised against a wire rather than a mock.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import os
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import pytest

from neuman_client.drops import GRACE_S, MAX_DROP_BYTES, DropStore
from neuman_client.jobs import JobRegistry
from neuman_client.link import Link
from neuman_client.session import Session
from neuman_client.ui import UiServer

TOKEN = "local-secret"


class _FakeBackground:
    """A job that runs until it is killed, without a process behind it.

    The daemon's registry only asks a background for its clocks, its streams and
    a way to end; a real process would add a shell to a test about HTTP.
    """

    def __init__(self, stop: asyncio.Event) -> None:
        self.out = SimpleNamespace(forwarded=0)
        self.err = SimpleNamespace(forwarded=0)
        self.done = asyncio.Event()
        self.started_at = time.monotonic()
        self.running_ms = 1000
        self.wall_ms = None
        self.exit_code = None
        self.ended = None
        self.lifetime_ms = 3_600_000
        self._stop = stop
        self.killed = False

    async def run(self) -> None:
        await self._stop.wait()
        self.done.set()

    def kill(self) -> None:
        self.killed = True
        self._stop.set()


class _Core(http.server.BaseHTTPRequestHandler):
    """A stub core: `/api/greet` echoes, `/api/login` sets a cookie."""

    def _reply(self, status: int, body: dict, cookies: list[str] | None = None) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        self._reply(200, {"path": self.path, "cookie": self.headers.get("cookie")})

    def do_POST(self) -> None:
        _ = self.rfile.read(int(self.headers.get("content-length") or 0))
        if urlsplit(self.path).path == "/api/login":
            self._reply(200, {"ok": True}, ["neuman_session=serverside; HttpOnly; Path=/"])
        else:
            self._reply(200, {"ok": True})

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def core():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Core)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
async def ui(tmp_path, core):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<h1>neuman</h1>", encoding="utf-8")
    session = Session(tmp_path / "session.json")
    server = UiServer(
        web_root=web,
        server_url=core,
        session=session,
        token=TOKEN,
        port=0,
    )
    await server.start()
    try:
        yield server, session
    finally:
        await server.stop()


def _get(ui: UiServer, path: str, *, cookie: str | None = None) -> tuple[int, bytes]:
    request = Request(f"http://127.0.0.1:{ui.port}{path}")
    if cookie:
        request.add_header("Cookie", cookie)
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


async def test_static_files_are_served_without_a_credential(ui):
    server, _session = ui
    status, body = await asyncio.to_thread(_get, server, "/")
    assert status == 200
    assert b"neuman" in body


async def test_the_token_bootstrap_sets_the_local_cookie(ui):
    server, _session = ui
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    writer.write(f"GET /?token={TOKEN} HTTP/1.1\r\nhost: x\r\n\r\n".encode())
    await writer.drain()
    head = await reader.readuntil(b"\r\n\r\n")
    writer.close()
    await writer.wait_closed()

    assert head.startswith(b"HTTP/1.1 303")
    assert f"set-cookie: neuman_client={TOKEN}".encode() in head.lower()


async def test_the_api_needs_the_local_credential(ui):
    server, _session = ui
    status, _body = await asyncio.to_thread(_get, server, "/api/greet")
    assert status == 401


async def test_the_link_state_is_answered_here_and_never_forwarded(tmp_path, core):
    """The one question the core cannot answer: refused is not absent (D111)."""

    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<h1>neuman</h1>", encoding="utf-8")
    link = Link(client_release="0.1.0", client_rpc=1)
    link.refused(
        {
            "server": {"release": "0.2.0", "rpc": 2},
            "client": {"release": "0.1.0", "rpc": 1},
            "accepted": [2],
        }
    )
    server = UiServer(
        web_root=web,
        server_url=core,
        session=Session(tmp_path / "session.json"),
        token=TOKEN,
        port=0,
        link=link,
    )
    await server.start()
    try:
        status, body = await asyncio.to_thread(
            _get, server, "/api/local", cookie=f"neuman_client={TOKEN}"
        )
    finally:
        await server.stop()

    assert status == 200
    payload = json.loads(body)
    assert payload["state"] == "refused"
    assert payload["visible"] is True
    assert payload["command"] == (
        "sudo apt update && sudo apt install --only-upgrade neuman-client"
    )
    assert "0.2.0" in payload["body"]


async def test_try_again_asks_the_daemon_to_reconnect(tmp_path, core):
    """The button skips a wait; it does not decide anything itself."""

    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<h1>neuman</h1>", encoding="utf-8")
    asked: list[int] = []
    server = UiServer(
        web_root=web,
        server_url=core,
        session=Session(tmp_path / "session.json"),
        token=TOKEN,
        port=0,
        link=Link(client_release="0.1.0", client_rpc=1),
        retry=lambda: asked.append(1),
    )

    def post() -> int:
        request = Request(
            f"http://127.0.0.1:{server.port}/api/local/retry",
            data=b"",
            headers={"Cookie": f"neuman_client={TOKEN}"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            return response.status

    await server.start()
    try:
        status = await asyncio.to_thread(post)
    finally:
        await server.stop()

    assert status == 204
    assert asked == [1]


async def test_a_ui_with_no_daemon_says_so_rather_than_inventing_a_link(ui):
    """`--ui-only` has no executor: a banner about one would be a lie."""

    server, _session = ui
    status, body = await asyncio.to_thread(
        _get, server, "/api/local", cookie=f"neuman_client={TOKEN}"
    )

    assert status == 200
    assert json.loads(body) == {"state": "ui_only", "visible": False}


async def test_a_request_without_the_local_cookie_names_itself(ui):
    """The page must not read this as a wrong password.

    A 401 here is the client's own proxy refusing a request that never reached
    the core; the login form's password goes through the same proxy. The body
    says which refusal it is so the page can tell them apart (N183).
    """

    server, _session = ui
    status, body = await asyncio.to_thread(_get, server, "/api/greet")

    assert status == 401
    assert json.loads(body)["error"] == "not_this_client"


async def test_the_api_proxies_to_the_core_with_the_session_cookie(ui):
    server, session = ui
    session.store(server=server.server_url, cookie="stored-cookie")

    status, body = await asyncio.to_thread(
        _get, server, "/api/greet?x=1", cookie=f"neuman_client={TOKEN}"
    )

    assert status == 200
    payload = json.loads(body)
    assert payload["path"] == "/api/greet?x=1"
    assert payload["cookie"] == "neuman_session=stored-cookie"


async def test_a_login_captures_the_cookie_and_gives_the_browser_none(ui):
    server, session = ui

    def post() -> tuple[int, list[str]]:
        request = Request(
            f"http://127.0.0.1:{server.port}/api/login",
            data=b'{"username":"alice","password":"x"}',
            headers={"content-type": "application/json", "Cookie": f"neuman_client={TOKEN}"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            return response.status, response.headers.get_all("Set-Cookie") or []

    status, cookies = await asyncio.to_thread(post)

    assert status == 200
    assert session.cookie() == "serverside"
    # The core's cookie is the daemon's; the browser gets none of it.
    assert cookies == []


async def test_a_login_with_the_core_down_says_so_rather_than_the_errno(tmp_path):
    """This is the sentence the login form shows, so it has to be one a person can use."""

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    web = tmp_path / "web"
    web.mkdir()
    server = UiServer(
        web_root=web,
        server_url=f"http://127.0.0.1:{port}",
        session=Session(tmp_path / "session.json"),
        token=TOKEN,
        port=0,
    )
    await server.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.port}/api/login",
            data=b"{}",
            headers={"Cookie": f"neuman_client={TOKEN}", "content-type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as caught:
            await asyncio.to_thread(urlopen, request, timeout=10)
        payload = json.loads(caught.value.read())
    finally:
        await server.stop()

    assert caught.value.code == 502
    assert payload["error"] == "core_unreachable"
    assert "the core is not running" in payload["detail"]
    assert "Errno" not in payload["detail"]


async def test_a_path_outside_the_web_root_is_not_served(ui):
    server, _session = ui
    status, _body = await asyncio.to_thread(_get, server, "/../etc/passwd")
    assert status in (400, 404)


async def test_the_session_socket_is_relayed_to_the_core(tmp_path):
    """The one path a hand-rolled proxy gets wrong, and did get wrong.

    The upstream here is the real `websockets` library rather than a stub, and
    that is the point: it refuses a `sec-websocket-key` that does not decode to
    the sixteen bytes RFC 6455 requires, exactly as the core's own server does.
    The first version of this proxy derived the key from a hash — 32 bytes —
    so every session socket failed as an opaque 502 while `/api` worked, which
    is the failure a stub that echoes anything would have passed.
    """

    import websockets

    seen: list[str] = []

    async def echo(ws) -> None:
        seen.append(ws.request.headers.get("cookie", ""))
        async for message in ws:
            await ws.send(message)

    async with websockets.serve(echo, "127.0.0.1", 0) as upstream:
        port = upstream.sockets[0].getsockname()[1]
        web = tmp_path / "web"
        web.mkdir()
        (web / "index.html").write_text("<h1>neuman</h1>", encoding="utf-8")
        session = Session(tmp_path / "session.json")
        session.store(server=f"http://127.0.0.1:{port}", cookie="serverside-cookie")
        server = UiServer(
            web_root=web,
            server_url=f"http://127.0.0.1:{port}",
            session=session,
            token=TOKEN,
            port=0,
        )
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}/ws/sessions/abc?x=1"
            async with websockets.connect(
                url, additional_headers={"Cookie": f"neuman_client={TOKEN}"}
            ) as ws:
                await ws.send("hello")
                assert await asyncio.wait_for(ws.recv(), timeout=10) == "hello"
        finally:
            await server.stop()

    # The core's cookie is the daemon's, and it is the daemon that presents it —
    # the browser only ever holds the local token.
    assert seen == ["neuman_session=serverside-cookie"], seen


def _post(
    ui: UiServer,
    path: str,
    *,
    cookie: str | None = None,
    origin: str | None = None,
    data: bytes = b"",
) -> tuple[int, bytes]:
    request = Request(f"http://127.0.0.1:{ui.port}{path}", method="POST", data=data)
    if cookie:
        request.add_header("Cookie", cookie)
    if origin:
        request.add_header("Origin", origin)
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


@pytest.fixture
async def ui_with_jobs(tmp_path, core):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<h1>neuman</h1>", encoding="utf-8")
    registry = JobRegistry()
    stop = asyncio.Event()
    job = registry.add(
        _FakeBackground(stop),
        dispatch_id="d1c0ffee01",
        session_id="sess1",
        tool="shell",
        args={"command": "make test"},
    )
    server = UiServer(
        web_root=web,
        server_url=core,
        session=Session(tmp_path / "session.json"),
        token=TOKEN,
        port=0,
        jobs=registry,
    )
    await server.start()
    try:
        yield server, job
    finally:
        await server.stop()
        stop.set()
        await asyncio.sleep(0.01)


async def test_the_running_jobs_are_listed_for_the_browser(ui_with_jobs):
    server, job = ui_with_jobs

    status, body = await asyncio.to_thread(
        _get, server, "/api/jobs", cookie=f"neuman_client={TOKEN}"
    )

    assert status == 200
    [row] = json.loads(body)["jobs"]
    assert row["job_id"] == job.id
    assert (row["tool"], row["label"]) == ("shell", "make test")
    assert row["running_ms"] >= 0


async def test_stopping_a_job_kills_it(ui_with_jobs):
    server, job = ui_with_jobs

    status, body = await asyncio.to_thread(
        _post,
        server,
        f"/api/jobs/{job.id}/stop",
        cookie=f"neuman_client={TOKEN}",
        origin=f"http://127.0.0.1:{server.port}",
    )

    assert status == 200, body
    assert json.loads(body) == {"stopped": job.id}
    assert job.background.killed


async def test_a_stop_from_another_origin_is_refused(ui_with_jobs):
    server, job = ui_with_jobs

    status, _body = await asyncio.to_thread(
        _post,
        server,
        f"/api/jobs/{job.id}/stop",
        cookie=f"neuman_client={TOKEN}",
        origin="https://example.test",
    )

    assert status == 403
    assert not job.background.killed


async def test_stopping_an_unknown_job_is_a_404(ui_with_jobs):
    server, _job = ui_with_jobs

    status, _body = await asyncio.to_thread(
        _post,
        server,
        "/api/jobs/000000/stop",
        cookie=f"neuman_client={TOKEN}",
        origin=f"http://127.0.0.1:{server.port}",
    )

    assert status == 404


async def test_the_job_row_needs_the_local_credential(ui_with_jobs):
    server, _job = ui_with_jobs

    status, _body = await asyncio.to_thread(_get, server, "/api/jobs")

    assert status == 401


async def test_a_ui_with_no_registry_has_no_jobs(ui):
    server, _session = ui

    status, body = await asyncio.to_thread(
        _get, server, "/api/jobs", cookie=f"neuman_client={TOKEN}"
    )

    assert status == 200
    assert json.loads(body) == {"jobs": []}


async def test_the_session_socket_needs_the_local_credential(ui):
    import websockets

    server, _session = ui
    with pytest.raises(websockets.exceptions.InvalidStatus) as caught:
        async with websockets.connect(f"ws://127.0.0.1:{server.port}/ws/sessions/abc"):
            pass
    assert caught.value.response.status_code == 401


# -- dropped files (D102) ----------------------------------------------------


class _Attached(http.server.BaseHTTPRequestHandler):
    """A stub core whose `/api/attachments` says what `listed` holds."""

    listed: ClassVar[list[str] | None] = []

    def do_GET(self) -> None:
        if self.listed is None:
            self.send_response(503)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        payload = json.dumps({"attachments": self.listed}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
async def ui_with_drops(tmp_path):
    _Attached.listed = []
    core = http.server.HTTPServer(("127.0.0.1", 0), _Attached)
    thread = threading.Thread(target=core.serve_forever, daemon=True)
    thread.start()
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<h1>neuman</h1>", encoding="utf-8")
    drops = DropStore(tmp_path / "state" / "drops")
    server = UiServer(
        web_root=web,
        server_url=f"http://127.0.0.1:{core.server_port}",
        session=Session(tmp_path / "session.json"),
        token=TOKEN,
        port=0,
        drops=drops,
    )
    await server.start()
    try:
        yield server, drops
    finally:
        await server.stop()
        core.shutdown()
        core.server_close()
        thread.join(timeout=5)


def _drop(server: UiServer, name: str, data: bytes, **kwargs: str) -> tuple[int, bytes]:
    from urllib.parse import quote

    return _post(
        server,
        f"/api/drops?name={quote(name)}",
        cookie=kwargs.get("cookie", f"neuman_client={TOKEN}"),
        origin=kwargs.get("origin", f"http://127.0.0.1:{server.port}"),
        data=data,
    )


async def test_a_dropped_file_is_stored_and_its_path_returned(ui_with_drops):
    server, drops = ui_with_drops

    status, body = await asyncio.to_thread(_drop, server, "report.pdf", b"%PDF-1.7 bytes")

    assert status == 200, body
    path = Path(json.loads(body)["path"])
    assert path.parent.parent == drops.root
    assert path.name == "report.pdf"
    assert path.read_bytes() == b"%PDF-1.7 bytes"


async def test_the_same_file_dropped_twice_is_one_copy(ui_with_drops):
    server, _drops = ui_with_drops

    first = await asyncio.to_thread(_drop, server, "a.txt", b"same")
    second = await asyncio.to_thread(_drop, server, "a.txt", b"same")
    other = await asyncio.to_thread(_drop, server, "a.txt", b"different")

    assert json.loads(first[1]) == json.loads(second[1])
    assert json.loads(other[1]) != json.loads(first[1])


async def test_a_drop_over_the_limit_is_refused_before_it_is_read(ui_with_drops):
    """The cap is checked on `content-length`, so the body is never buffered."""

    server, drops = ui_with_drops
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    writer.write(
        (
            "POST /api/drops?name=big.bin HTTP/1.1\r\nhost: x\r\n"
            f"cookie: neuman_client={TOKEN}\r\n"
            f"content-length: {MAX_DROP_BYTES + 1}\r\n\r\n"
        ).encode()
    )
    await writer.drain()
    head = await reader.readuntil(b"\r\n\r\n")
    writer.close()

    assert head.startswith(b"HTTP/1.1 413")
    assert not drops.root.exists() or not any(drops.root.iterdir())


@pytest.mark.parametrize(
    ("cookie", "origin", "expected"),
    [
        ("neuman_client=wrong", None, 401),
        (None, "https://example.test", 403),
    ],
)
async def test_a_drop_needs_the_local_credential_and_this_origin(
    ui_with_drops, cookie, origin, expected
):
    server, drops = ui_with_drops
    kwargs = {}
    if cookie:
        kwargs["cookie"] = cookie
    if origin:
        kwargs["origin"] = origin

    status, _body = await asyncio.to_thread(_drop, server, "x.txt", b"x", **kwargs)

    assert status == expected
    assert not drops.root.exists() or not any(drops.root.iterdir())


async def test_a_drop_is_swept_once_nothing_attaches_it(ui_with_drops):
    """Kept while a conversation names it, and through the grace period."""

    server, drops = ui_with_drops
    _, body = await asyncio.to_thread(_drop, server, "kept.md", b"kept")
    kept = json.loads(body)["path"]
    _, body = await asyncio.to_thread(_drop, server, "loose.md", b"loose")
    loose = Path(json.loads(body)["path"])

    later = time.time() + GRACE_S + 1
    removed = await asyncio.to_thread(drops.sweep, [kept], now=later)

    assert removed == [loose]
    assert Path(kept).is_file()
    assert not loose.parent.exists()


async def test_a_sweep_keeps_everything_when_the_core_cannot_answer(ui_with_drops):
    """A partial or failed list must never read as "nothing is attached"."""

    server, _drops = ui_with_drops
    _, body = await asyncio.to_thread(_drop, server, "x.md", b"x")
    path = Path(json.loads(body)["path"])
    # The sweep the drop itself started, so it cannot run after the clock moves.
    await server._sweeping
    old = time.time() - GRACE_S - 1
    os.utime(path, (old, old))

    _Attached.listed = None
    server._sweep_drops()
    await server._sweeping

    assert path.is_file()

    _Attached.listed = []
    server._sweep_drops()
    await server._sweeping

    assert not path.exists()
