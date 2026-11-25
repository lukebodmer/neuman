"""The client's command line, at the points where it fails.

`test_ui.py` runs the UI server; `test_daemon.py` runs the daemon. What is left
is what happens when the two cannot start at all — and the one that bites is a
port somebody else already holds, because the module installs a client as a user
service and a person debugging it will run a second copy by hand. A traceback of
store paths answers "why did nothing happen" with silence.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import socket
import threading
from pathlib import Path

import pytest

from neuman_client import cli
from neuman_client.daemon import RPC_VERSION
from neuman_client.platform import service_status_hint


def _web_root(tmp_path: Path) -> Path:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<title>Neuman</title>", encoding="utf-8")
    return web


def _manifests(tmp_path: Path) -> Path:
    manifests = tmp_path / "manifests"
    tool = manifests / "fs_read"
    tool.mkdir(parents=True)
    (tool / "manifest.toml").write_text(
        '[tool]\nname = "fs_read"\nexec = "fs_read"\nwait_ms = 30000\n',
        encoding="utf-8",
    )
    return manifests


def test_a_busy_ui_port_is_a_message_not_a_traceback(tmp_path, capsys):
    """The failure a second copy hits, and the only sensible answer to it.

    Nothing is wrong when this happens: the service the module installs is
    already serving the UI. So the message has to name the service, because
    "address already in use" from a command the user just typed reads as a bug
    in the command.
    """

    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]

    try:
        code = cli.main(
            [
                # A global option, so it comes before the subcommand.
                "--state-dir",
                str(tmp_path / "state"),
                "run",
                "--server",
                "http://127.0.0.1:1",
                "--manifests",
                str(_manifests(tmp_path)),
                "--bin",
                str(tmp_path / "bin"),
                "--web-root",
                str(_web_root(tmp_path)),
                "--ui-port",
                str(port),
            ]
        )
    finally:
        holder.close()

    assert code == 1
    err = capsys.readouterr().err
    assert "address already in use" in err, err
    assert service_status_hint() in err, err
    assert "Traceback" not in err, err


def test_ui_url_is_written_only_after_the_ui_binds(tmp_path, monkeypatch):
    """The browser reads this file to decide where to open; a stale URL is a dead page.

    The file used to be written before the port was bound and left behind when
    the client stopped, so a restart's browser could open a URL nothing answered
    and never complete the token bootstrap (N183). The client now removes the
    stale file and writes the new one after the bind.
    """

    state = tmp_path / "state"
    state.mkdir()
    (state / "ui-url").write_text("http://127.0.0.1:1/?token=stale", encoding="utf-8")

    seen: list[str] = []

    class _Stop(Exception):
        pass

    class _Daemon:
        def __init__(self, **_kwargs) -> None:
            from neuman_client import __version__
            from neuman_client.link import Link

            # The UI server reads the daemon's own link state; this fake has no
            # socket, so its link says nothing rather than something untrue.
            self.link = Link(client_release=__version__, client_rpc=1)

        def retry(self) -> None:
            pass

        async def watch_build(self, _restart, **_kwargs) -> None:
            # Only a package installed under the process makes this finish.
            await asyncio.Event().wait()

        async def run(self) -> None:
            # The UI is bound and the file is written before the daemon starts,
            # so by now it must name the bound UI rather than the stale URL.
            seen.append((state / "ui-url").read_text(encoding="utf-8").strip())
            raise _Stop

    monkeypatch.setattr(cli, "ClientDaemon", _Daemon)

    with pytest.raises(_Stop):
        cli.main(
            [
                "--state-dir",
                str(state),
                "run",
                "--server",
                "http://127.0.0.1:1",
                "--manifests",
                str(_manifests(tmp_path)),
                "--bin",
                str(tmp_path / "bin"),
                "--web-root",
                str(_web_root(tmp_path)),
                "--ui-port",
                "0",
            ]
        )

    assert seen, "the daemon never ran"
    url = seen[0]
    assert url != "http://127.0.0.1:1/?token=stale"
    assert url.startswith("http://127.0.0.1:")
    assert "?token=" in url


def test_plain_http_is_loopback_only():
    """The rule that makes the cookie's `Secure` attribute mean something.

    A browser refuses to send a `Secure` cookie over http; a daemon has no such
    notion and would send it, and the password before it. So the client decides:
    loopback is not a network, and anything else is https or nothing.
    """

    assert cli._secure_scheme("https://core.example:8443") is None
    assert cli._secure_scheme("http://127.0.0.1:8443") is None
    assert cli._secure_scheme("http://localhost:8443") is None
    assert cli._secure_scheme("http://[::1]:8443") is None

    for unsafe in ("http://core.example:8443", "core.example:8443", "http://10.0.0.5"):
        reason = cli._secure_scheme(unsafe)
        assert reason and "https" in reason, (unsafe, reason)


def test_login_refuses_to_send_a_password_in_clear(tmp_path, capsys):
    code = cli.main(
        [
            "--state-dir",
            str(tmp_path),
            "login",
            "--server",
            "http://core.example:8443",
            "--user",
            "alice",
        ]
    )

    assert code == 2
    err = capsys.readouterr().err
    assert "not https" in err, err
    assert not (tmp_path / "session.json").exists(), "nothing was stored"


def test_doctor_reports_the_host_without_printing_the_cookie(tmp_path, capsys, monkeypatch):
    """The report a support conversation starts with, minus the secret."""

    (tmp_path / "session.json").write_text(
        json.dumps({"server": "https://core.example:8443", "cookie": "SECRET"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "browse_socket", lambda: tmp_path / "browse.sock")
    monkeypatch.setattr(cli, "find_chromium", lambda: "/Applications/Google Chrome")
    monkeypatch.setattr(cli, "browser_profile_dir", lambda _browser=None: tmp_path / "profile")

    code = cli.main(["--state-dir", str(tmp_path), "doctor"])

    out = capsys.readouterr().out
    assert code == 0, out
    assert "https://core.example:8443" in out, out
    assert "SECRET" not in out, "doctor printed the session cookie"


def test_doctor_fails_on_a_profile_the_browser_cannot_open(tmp_path, capsys, monkeypatch):
    """The acceptance check has to fail for the pair, not for each fact (N184).

    A host whose browser and profile were reported side by side as two healthy
    lines, where the browser could never open the profile, is what sent an
    install to a chat instead of to an error message.
    """

    monkeypatch.setattr(cli, "browse_socket", lambda: tmp_path / "browse.sock")
    monkeypatch.setattr(cli, "find_chromium", lambda: "/snap/bin/chromium")
    monkeypatch.setattr(cli, "browser_profile_dir", lambda _browser=None: tmp_path / "browser")
    monkeypatch.setattr(
        cli, "browser_profile_refusal", lambda _browser, _profile: "snap-confined"
    )

    code = cli.main(["--state-dir", str(tmp_path), "doctor"])

    out = capsys.readouterr().out
    assert code == 1, out
    assert "browser profile unreachable: snap-confined" in out, out


def test_doctor_fails_when_the_appliance_speaks_a_newer_shape(tmp_path, capsys, monkeypatch):
    """The acceptance check names the command, not the version to look up (D111)."""

    (tmp_path / "session.json").write_text(
        json.dumps({"server": "https://core.example:8443", "cookie": "SECRET"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "browse_socket", lambda: tmp_path / "browse.sock")
    monkeypatch.setattr(cli, "find_chromium", lambda: "/usr/bin/google-chrome")
    monkeypatch.setattr(cli, "browser_profile_dir", lambda _browser=None: tmp_path / "profile")
    monkeypatch.setattr(
        cli,
        "_appliance_versions",
        lambda _server: {
            "product": "neuman",
            "release": "0.2.0",
            # One shape ahead of this client. The fixture states a *newer*
            # appliance, so it moves with the client's own shape — a number
            # pinned here would stop testing the refusal the day the client
            # caught up with the fixture.
            "rpc_versions": [RPC_VERSION + 1],
            "minimum_client": {"release": "0.2.0", "rpc": RPC_VERSION + 1},
        },
    )

    code = cli.main(["--state-dir", str(tmp_path), "doctor"])

    out = capsys.readouterr().out
    assert code == 1, out
    assert "appliance: 0.2.0" in out, out
    assert "appliance is newer than this client" in out, out
    assert "--only-upgrade neuman-client" in out, out


def test_login_names_the_account_on_the_core(tmp_path, capsys):
    """The local account is not the login name, so there is no default.

    A workstation account and an appliance account need not be spelled the same
    (D67); asking for the name is what keeps the client from guessing the wrong
    one and storing a session for it.
    """

    with pytest.raises(SystemExit) as caught:
        cli.main(
            [
                "--state-dir",
                str(tmp_path),
                "login",
                "--server",
                "https://core.example:8443",
            ]
        )

    assert caught.value.code == 2
    assert "--user" in capsys.readouterr().err
    assert not (tmp_path / "session.json").exists()


# -- the browser unit's readiness check --------------------------------------


def _ui_server(status: int) -> http.server.ThreadingHTTPServer:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(status)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_wait_for_ui_returns_on_the_token_bootstrap(tmp_path):
    """A 303, not merely a file: the browser unit's readiness condition (N183)."""

    server = _ui_server(303)
    try:
        url = f"http://127.0.0.1:{server.server_port}/?token=SECRET"
        (tmp_path / "ui-url").write_text(url, encoding="utf-8")

        assert cli.main(["--state-dir", str(tmp_path), "wait-for-ui", "--timeout", "3"]) == 0
    finally:
        server.shutdown()


def test_wait_for_ui_times_out_without_printing_the_token(tmp_path, capsys):
    """The URL carries the per-install token; a failure must not log it."""

    server = _ui_server(200)
    try:
        url = f"http://127.0.0.1:{server.server_port}/?token=SECRET"
        (tmp_path / "ui-url").write_text(url, encoding="utf-8")
        code = cli.main(
            [
                "--state-dir",
                str(tmp_path),
                "wait-for-ui",
                "--timeout",
                "0.5",
                "--interval",
                "0.1",
            ]
        )
    finally:
        server.shutdown()

    assert code == 1
    error = capsys.readouterr().err
    assert "SECRET" not in error
    assert str(server.server_port) in error


def test_wait_for_ui_says_when_there_is_no_url_yet(tmp_path, capsys):
    code = cli.main(
        ["--state-dir", str(tmp_path), "wait-for-ui", "--timeout", "0.3", "--interval", "0.1"]
    )

    assert code == 1
    assert "no ui-url" in capsys.readouterr().err
