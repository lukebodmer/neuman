"""The stored session: its shape, its mode, and a login against a stub core.

The client's session is its own credential for one core, never a grant. What
matters here is that it is stored privately, that a login replaces it, and that a
core which answers without a cookie is an error rather than a silent success.
"""

from __future__ import annotations

import http.server
import socket
import threading

import pytest

from neuman_client.session import Session, SessionError, unreachable


class _Core(http.server.BaseHTTPRequestHandler):
    """A stub `/api/login` that answers with a cookie."""

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("Set-Cookie", "neuman_session=xyz789; HttpOnly; Path=/")
        self.end_headers()
        self.wfile.write(b"{}")

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


def test_the_cookie_is_stored_0600_and_cleared(tmp_path):
    session = Session(tmp_path / "session.json")

    assert session.cookie() is None
    session.store(server="https://appliance:8443", cookie="abc")
    assert session.cookie() == "abc"
    assert session.server() == "https://appliance:8443"
    assert (tmp_path / "session.json").stat().st_mode & 0o777 == 0o600

    session.clear()
    assert session.cookie() is None


def test_login_keeps_the_cookie_the_core_returned(tmp_path, core):
    session = Session(tmp_path / "session.json")

    session.login(server=core, username="alice", password="correct horse")

    assert session.cookie() == "xyz789"
    assert session.server() == core


def test_a_core_that_answers_without_a_cookie_is_an_error(tmp_path):
    class Bare(_Core):
        def do_POST(self) -> None:
            self.send_response(200)
            self.end_headers()

    server = http.server.HTTPServer(("127.0.0.1", 0), Bare)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(SessionError, match="without a session cookie"):
            Session(tmp_path / "session.json").login(
                server=f"http://127.0.0.1:{server.server_port}",
                username="alice",
                password="x",
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_core_that_refuses_the_connection_is_named_as_down(tmp_path):
    """The raw errno told the person at the login form nothing they could act on."""

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    session = Session(tmp_path / "session.json")
    with pytest.raises(SessionError, match="the core is not running") as caught:
        session.login(server=f"http://127.0.0.1:{port}", username="a", password="b")
    assert "Errno" not in str(caught.value)


def test_any_other_failure_keeps_its_reason():
    assert unreachable("https://core", "timed out") == "cannot reach https://core: timed out"
