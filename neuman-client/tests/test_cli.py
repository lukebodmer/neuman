"""The client's command line, at the points where it fails.

`test_ui.py` runs the UI server; `test_daemon.py` runs the daemon. What is left
is what happens when the two cannot start at all — and the one that bites is a
port somebody else already holds, because the module installs a client as a user
service and a person debugging it will run a second copy by hand. A traceback of
store paths answers "why did nothing happen" with silence.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from neuman_client import cli
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
    monkeypatch.setattr(cli, "browser_profile_dir", lambda: tmp_path / "profile")

    code = cli.main(["--state-dir", str(tmp_path), "doctor"])

    out = capsys.readouterr().out
    assert code == 0, out
    assert "https://core.example:8443" in out, out
    assert "SECRET" not in out, "doctor printed the session cookie"


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
