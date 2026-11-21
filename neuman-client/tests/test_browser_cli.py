"""`neuman-browser` at the points where it refuses to start.

Two of them, both added for the Ubuntu package (D108): a browser old enough
that the DevTools surface may have moved, and `--profile copy` when there is no
profile to copy. The second is the one that matters: it used to return quietly
and leave the browser on the `own` profile, which is a silent change in whose
identity a page injected with the model acts as.
"""

from __future__ import annotations

from pathlib import Path

from neuman_client.browser import cli as browser_cli


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
