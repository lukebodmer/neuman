"""`neuman-browser` at the points where it refuses to start.

Four of them, all added for the Ubuntu package (D108). A browser old enough that
the DevTools surface may have moved. `--profile copy` when there is no profile
to copy — the one that matters most, because it used to return quietly and
leave the browser on the `own` profile, a silent change in whose identity a
page injected with the model acts as. A profile the browser it was found for
cannot open, and a browser that starts and never answers on the port `browse`
dials: both were field failures that arrived as a bare exit status and a
restart loop instead of a sentence (N184).
"""

from __future__ import annotations

import socket
from pathlib import Path

from neuman_client.browser import cli as browser_cli
from neuman_client.platform import _linux


def _chromium(tmp_path: Path, version: str) -> Path:
    fake = tmp_path / "chromium"
    fake.write_text(f"#!/bin/sh\necho 'Chromium {version}'\n", encoding="utf-8")
    fake.chmod(0o755)
    return fake


def test_a_chromium_below_the_floor_is_a_sentence(tmp_path, capsys):
    fake = _chromium(tmp_path, "100.0.4896.60")

    code = browser_cli.main(
        ["--chromium", str(fake), "--socket", str(tmp_path / "browse.sock")]
    )

    assert code == 1
    error = capsys.readouterr().err
    assert "Chromium 100" in error and "newer is required" in error


def test_an_unreadable_version_is_let_through(tmp_path):
    """A binary that will not answer `--version` is not refused for that.

    The floor is a courtesy so an ancient browser fails with a sentence; a
    wrapper that answers nothing should not be turned into a refusal the CDP
    error would have named better.
    """

    quiet = tmp_path / "chromium"
    quiet.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    quiet.chmod(0o755)

    assert browser_cli._chromium_major(str(quiet)) is None


def test_copy_mode_refuses_without_a_profile(tmp_path, capsys, monkeypatch):
    fake = _chromium(tmp_path, "153.0.8010.36")
    missing = tmp_path / "no-profile"
    # The rsync check comes first, and the build sandbox has no rsync; this
    # test is about the profile, so give it a path to reach that check.
    monkeypatch.setattr(browser_cli, "_rsync", lambda: "/usr/bin/rsync")

    code = browser_cli.main(
        [
            "--chromium",
            str(fake),
            "--socket",
            str(tmp_path / "browse.sock"),
            "--profile",
            "copy",
            "--copy-source",
            str(missing),
        ]
    )

    assert code == 1
    error = capsys.readouterr().err
    assert str(missing) in error


def test_copy_mode_refuses_without_rsync(tmp_path, capsys, monkeypatch):
    fake = _chromium(tmp_path, "153.0.8010.36")
    source = tmp_path / "profile"
    source.mkdir()
    monkeypatch.setattr(browser_cli, "_rsync", lambda: None)

    code = browser_cli.main(
        [
            "--chromium",
            str(fake),
            "--socket",
            str(tmp_path / "browse.sock"),
            "--profile",
            "copy",
            "--copy-source",
            str(source),
        ]
    )

    assert code == 1
    assert "rsync" in capsys.readouterr().err


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _chromium_browser(tmp_path: Path) -> Path:
    """A stand-in named the way Ubuntu's snap wrapper is named (N184)."""

    fake = tmp_path / "chromium-browser"
    fake.write_text("#!/bin/sh\necho 'Chromium 153.0.8010.36'\n", encoding="utf-8")
    fake.chmod(0o755)
    return fake


def test_a_confined_browser_refuses_a_profile_it_cannot_open(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(_linux, "home_dir", lambda: tmp_path)
    fake = _chromium_browser(tmp_path)
    profile = tmp_path / ".local" / "share" / "neuman" / "browser"

    code = browser_cli.main(
        [
            "--chromium",
            str(fake),
            "--socket",
            str(tmp_path / "browse.sock"),
            "--user-data-dir",
            str(profile),
        ]
    )

    assert code == 1
    error = capsys.readouterr().err
    assert "snap-confined" in error and str(profile) in error, error


def test_a_browser_that_refuses_its_profile_says_so(tmp_path, capsys):
    """The field failure: 21 reached systemd as a bare status and nothing else.

    Chromium's `RESULT_CODE_PROFILE_IN_USE` covers both "another instance holds
    it" and "it cannot be opened", and neither is visible anywhere the operator
    looks unless this line is printed (N184).
    """

    fake = tmp_path / "chromium"
    fake.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "Chromium 153.0.8010.36"; exit 0; fi\n'
        "exit 21\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    profile = tmp_path / "profile"

    code = browser_cli.main(
        [
            "--chromium",
            str(fake),
            "--socket",
            str(tmp_path / "browse.sock"),
            "--user-data-dir",
            str(profile),
            "--port",
            str(_free_port()),
        ]
    )

    assert code == 21
    error = capsys.readouterr().err
    assert "exited 21" in error and str(profile) in error, error


def test_a_browser_with_nothing_on_its_port_is_not_ready(tmp_path, capsys, monkeypatch):
    """A running browser with no DevTools port looks healthy and serves nothing."""

    fake = tmp_path / "chromium"
    fake.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "Chromium 153.0.8010.36"; exit 0; fi\n'
        "exec sleep 30\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setattr(browser_cli, "DEVTOOLS_TIMEOUT_S", 0.3)

    code = browser_cli.main(
        [
            "--chromium",
            str(fake),
            "--socket",
            str(tmp_path / "browse.sock"),
            "--user-data-dir",
            str(tmp_path / "profile"),
            "--port",
            str(_free_port()),
        ]
    )

    assert code == 1
    assert "network-bind" in capsys.readouterr().err


def test_a_taken_devtools_port_is_refused_before_a_browser_starts(tmp_path, capsys):
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        code = browser_cli.main(
            [
                "--chromium",
                str(_chromium(tmp_path, "153.0.8010.36")),
                "--socket",
                str(tmp_path / "browse.sock"),
                "--user-data-dir",
                str(tmp_path / "profile"),
                "--port",
                str(holder.getsockname()[1]),
            ]
        )

    assert code == 1
    assert "already answers" in capsys.readouterr().err


def test_the_devtools_wait_returns_as_soon_as_the_port_answers():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)

        assert browser_cli._wait_for_devtools(listener.getsockname()[1], lambda: True, 1.0)
