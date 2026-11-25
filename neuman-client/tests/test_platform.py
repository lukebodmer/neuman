"""The two host layouts the client supports.

The point of the seam is that a path or a browser lookup is decided in one
place. These tests pin the Linux layout, the macOS layout, and the overrides
that a service uses to keep its state away from a hand-run client.
"""

from __future__ import annotations

from pathlib import Path

from neuman_client import platform
from neuman_client.platform import _darwin, _linux


def test_linux_uses_the_xdg_layout(monkeypatch):
    monkeypatch.setattr(_linux, "home_dir", lambda: Path("/home/alice"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")

    assert _linux.default_state_dir() == Path("/home/alice/.local/share/neuman/client")
    assert _linux.runtime_dir() == Path("/run/user/1000")
    assert _linux.browser_profile_dir() == Path("/home/alice/.local/share/neuman/browser")
    assert _linux.browser_copy_source() == Path("/home/alice/.config/chromium")


def test_macos_uses_the_native_layout(monkeypatch):
    monkeypatch.setattr(_darwin, "home_dir", lambda: Path("/Users/alice"))
    monkeypatch.setenv("TMPDIR", "/var/folders/ab/cdef/T/")

    assert _darwin.default_state_dir() == Path(
        "/Users/alice/Library/Application Support/Neuman/client"
    )
    assert _darwin.runtime_dir() == Path("/var/folders/ab/cdef/T/")
    assert _darwin.browser_profile_dir() == Path(
        "/Users/alice/Library/Application Support/Neuman/browser"
    )
    assert _darwin.browser_copy_source() == Path(
        "/Users/alice/Library/Application Support/Google/Chrome"
    )


def test_macos_has_no_ozone_flags():
    assert _darwin.display_args() == []


def test_linux_copy_source_follows_the_browser_the_user_runs(monkeypatch, tmp_path):
    """`--profile copy` seeds from wherever their browser keeps its profile.

    The Chromium snap puts it under the snap's own `common` directory, not
    `~/.config`, so a default that only knew the deb layout copies nothing on
    a stock Ubuntu desktop — and copy mode used to proceed anyway, leaving the
    browser with the `own` profile while the operator believed otherwise
    (D108).
    """

    monkeypatch.setattr(_linux, "home_dir", lambda: tmp_path)
    assert _linux.browser_copy_source() == tmp_path / ".config" / "chromium"

    snap = tmp_path / "snap" / "chromium" / "common" / "chromium"
    snap.mkdir(parents=True)
    assert _linux.browser_copy_source() == snap

    apt = tmp_path / ".config" / "chromium"
    apt.mkdir(parents=True)
    assert _linux.browser_copy_source() == apt


def test_the_state_and_socket_overrides_win(monkeypatch, tmp_path):
    monkeypatch.setenv("NEUMAN_CLIENT_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("NEUMAN_BROWSE_SOCKET", str(tmp_path / "browse.sock"))

    assert platform.state_dir() == tmp_path / "state"
    assert platform.browse_socket() == tmp_path / "browse.sock"


def test_the_socket_defaults_through_the_selected_host(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "_host", _linux)
    monkeypatch.delenv("NEUMAN_BROWSE_SOCKET", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    assert platform.browse_socket() == tmp_path / "neuman-browse.sock"


def test_macos_finds_a_chrome_app_bundle(monkeypatch, tmp_path):
    app = tmp_path / "Applications" / "Google Chrome.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)
    executable = app / "Google Chrome"
    executable.write_text("", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(_darwin, "home_dir", lambda: tmp_path)
    monkeypatch.delenv("NEUMAN_BROWSER_CHROMIUM", raising=False)

    assert _darwin.find_chromium() == str(executable)


def test_the_chromium_override_wins(monkeypatch):
    monkeypatch.setenv("NEUMAN_BROWSER_CHROMIUM", "/opt/chrome")

    assert platform.find_chromium() == "/opt/chrome"


def test_a_package_host_answers_in_the_runtime_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "_host", _linux)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    assert platform.host_socket("blender") == tmp_path / "neuman-blender.sock"


def test_the_profile_follows_the_browser_that_will_open_it(monkeypatch, tmp_path):
    """A confined browser can open only some of the paths its twin can (N184).

    Ubuntu's Chromium is a snap, and a snap reaches only non-hidden paths under
    `$HOME`. A profile under `~/.local` is therefore one it can never open, and
    the browser's own message about a lock file names neither the confinement
    nor the fix — so the profile moves instead.
    """

    monkeypatch.setattr(_linux, "home_dir", lambda: tmp_path)
    monkeypatch.delenv("NEUMAN_BROWSER_PROFILE", raising=False)

    assert _linux.browser_profile_dir("/usr/bin/google-chrome-stable") == (
        tmp_path / ".local" / "share" / "neuman" / "browser"
    )
    for snap in ("/snap/bin/chromium", "/usr/bin/chromium-browser"):
        assert _linux.browser_profile_dir(snap) == (
            tmp_path / "snap" / "chromium" / "common" / "neuman-browser"
        )


def test_a_confined_browser_refuses_a_profile_it_cannot_open(monkeypatch, tmp_path):
    monkeypatch.setattr(_linux, "home_dir", lambda: tmp_path)
    hidden = tmp_path / ".local" / "share" / "neuman" / "browser"

    for snap in ("/snap/bin/chromium", "/usr/bin/chromium-browser"):
        refusal = _linux.browser_profile_refusal(snap, hidden)
        assert refusal and "hidden" in refusal, refusal

    assert _linux.browser_profile_refusal("/usr/bin/google-chrome", hidden) is None
    assert _linux.browser_profile_refusal("/snap/bin/chromium", tmp_path / "browser") is None

    outside = _linux.browser_profile_refusal("/snap/bin/chromium", Path("/var/lib/browser"))
    assert outside and "outside" in outside, outside


def test_the_profile_override_wins(monkeypatch, tmp_path):
    """The package owns the unit, so an operator changes the path here (N184)."""

    monkeypatch.setenv("NEUMAN_BROWSER_PROFILE", str(tmp_path / "profile"))
    monkeypatch.setattr(platform, "_host", _linux)

    assert platform.browser_profile_dir("/snap/bin/chromium") == tmp_path / "profile"
