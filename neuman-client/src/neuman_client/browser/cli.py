"""Run the browser `browse` drives, on the user's machine.

`browse` runs on this machine too, and dials a Unix socket this process serves.
Chromium exposes the Chrome DevTools Protocol on a TCP port and nothing else, so
this starts a real Chromium with remote debugging and bridges its loopback debug
port onto that socket — a socket's mode is an access control a loopback port is
not.

There is no headless mode. A headless browser is blocked by enough sites,
unpredictably, that it is worse than being told there is no browser.

The profile is the security-relevant choice. `own` (the default) is a profile
belonging to Neuman, so a page that injects the model acts with no logins of the
user's. `copy` rsyncs their real profile in once at startup, which lets Neuman
reach pages they are signed into and means the model's next action can carry
their identity. The dangerous one is opt-in.

Where the profile lives is not a constant: a confined browser cannot open every
path its unconfined twin can, so the browser decides the default, and an
unopenable pairing is refused with a sentence rather than left to surface as a
lock file and an exit code (N184).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from ..platform import (
    browse_socket,
    browser_copy_source,
    browser_profile_dir,
    browser_profile_refusal,
    display_args,
    find_chromium,
    runtime_hint,
    state_dir,
)
from .bridge import serve

# The client's own UI, which is where `browse` and the search tools live now.
# The token is in the URL the daemon writes; this is only the fallback.
DEFAULT_URL = "http://127.0.0.1:8642"
DEFAULT_PORT = 9222

# How long the browser has to answer on its DevTools port before the host
# decides it never will. Chromium opens it within a second of starting, and the
# wait is generous because a loaded machine is not a broken one — but it is
# bounded, because a unit that stays up with a port nothing answers on is the
# failure this wait exists to name (N184).
DEVTOOLS_TIMEOUT_S = 20.0

# Chromium's `chrome/common/chrome_result_codes.h`. It means the profile
# belongs to another running instance, or cannot be opened at all — and systemd
# reports it as a bare 21 that names neither (N184).
RESULT_CODE_PROFILE_IN_USE = 21

# Chromium's DevTools surface has been stable for years and the reference
# appliance pins a current one, so this floor is old on purpose: its job is a
# sentence instead of a protocol error on an ancient browser, not a gate on a
# version the bridge could not use anyway. A binary that will not answer
# `--version` is let through, because the error it then gives names itself.
MINIMUM_CHROMIUM_MAJOR = 120


class ProfileError(Exception):
    """`--profile copy` was asked for and cannot be honoured."""


def _chromium_major(chromium: str) -> int | None:
    try:
        completed = subprocess.run(
            [chromium, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+)\.", completed.stdout)
    return int(match.group(1)) if match else None


def _devtools_answers(port: int) -> bool:
    """Whether something accepts a connection on the loopback DevTools port."""

    try:
        socket.create_connection(("127.0.0.1", port), timeout=1.0).close()
    except OSError:
        return False
    return True


def _wait_for_devtools(port: int, alive: Callable[[], bool], timeout: float) -> bool:
    """Whether the DevTools port answers before `timeout`, while the browser lives.

    The bridge is only worth binding when there is something to forward to. A
    browser that is running with nothing on its port looks healthy to systemd:
    the unit stays up serving a socket `browse` can dial and cannot use, which is
    how a confined Chromium fails when `network-bind` is not connected (N184).
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _devtools_answers(port):
            return True
        if not alive():
            return False
        time.sleep(0.1)
    return False


def _explain_exit(code: int, chromium: str, profile: Path) -> None:
    """Say what a bare exit code means, because systemd will not."""

    if code == RESULT_CODE_PROFILE_IN_USE:
        print(
            f"neuman-browser: {chromium} exited 21: it could not take {profile} — "
            f"another instance holds that profile, or it cannot be opened",
            file=sys.stderr,
        )
        return
    print(f"neuman-browser: {chromium} exited {code}", file=sys.stderr)


def _shut_down(browser: subprocess.Popen) -> None:
    browser.terminate()
    try:
        browser.wait(timeout=5)
    except subprocess.TimeoutExpired:
        browser.kill()


def _default_url() -> str:
    """Where the client daemon says its UI is, token included.

    The daemon writes this file when it starts, so a browser launched by hand
    does not have to be handed a URL with a per-install token in it. The
    fallback is the client's own default port, so a launcher that races the
    daemon opens the right host rather than the core's address.
    """

    try:
        value = (state_dir() / "ui-url").read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_URL
    return value or DEFAULT_URL


# rsync is substituted into the wrapper at build time, so the profile copy works
# without it being on the user's PATH. The name is also accepted from the
# environment for a hand-installed copy.
RSYNC_ENV = "NEUMAN_BROWSER_RSYNC"


def _rsync() -> str | None:
    return os.environ.get(RSYNC_ENV) or shutil.which("rsync")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="neuman-browser",
        description="Run the Chromium Neuman drives, as the user.",
    )
    parser.add_argument("--url", default=_default_url(), help="the UI to open")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    bridge = browse_socket()
    parser.add_argument("--socket", default=str(bridge) if bridge else None)
    parser.add_argument("--profile", choices=("own", "copy"), default="own")
    parser.add_argument(
        "--user-data-dir",
        default=None,
        help="the profile to run (default: the one this browser can open)",
    )
    parser.add_argument("--copy-source", default=str(browser_copy_source()))
    parser.add_argument("--chromium", default=None)
    return parser


def _copy_profile(source: str, destination: str, rsync: str | None) -> None:
    """Seed the profile, or refuse. Copy mode is opt-in and must not degrade.

    A missing rsync or a missing source used to return quietly, which left the
    browser running with the `own` profile while the operator believed it had
    their logins — a silent change in who a page injected with the model acts
    as. The dangerous mode fails closed instead.
    """

    if rsync is None:
        raise ProfileError("copy needs rsync; install it or set NEUMAN_BROWSER_RSYNC")
    if not os.path.isdir(source):
        raise ProfileError(f"no browser profile at {source}; pass --copy-source")

    os.makedirs(destination, exist_ok=True, mode=0o700)
    try:
        completed = subprocess.run(
            [rsync, "-a", "--delete", f"{source}/", f"{destination}/"],
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ProfileError(f"cannot copy {source}: {error}") from error
    if completed.returncode != 0:
        raise ProfileError(f"rsync failed copying {source}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    chromium = args.chromium or find_chromium()
    if not chromium:
        print(
            "neuman-browser: no Chromium found; pass --chromium or set NEUMAN_BROWSER_CHROMIUM",
            file=sys.stderr,
        )
        return 1

    major = _chromium_major(chromium)
    if major is not None and major < MINIMUM_CHROMIUM_MAJOR:
        print(
            f"neuman-browser: {chromium} is Chromium {major}; "
            f"{MINIMUM_CHROMIUM_MAJOR} or newer is required",
            file=sys.stderr,
        )
        return 1

    profile = (
        Path(args.user_data_dir).expanduser()
        if args.user_data_dir
        else browser_profile_dir(chromium)
    )
    if refusal := browser_profile_refusal(chromium, profile):
        print(f"neuman-browser: {refusal}", file=sys.stderr)
        print(
            "neuman-browser: move it with --user-data-dir or NEUMAN_BROWSER_PROFILE, "
            "or run a browser that can open it",
            file=sys.stderr,
        )
        return 1

    os.makedirs(profile, exist_ok=True, mode=0o700)
    if args.profile == "copy":
        try:
            _copy_profile(str(Path(args.copy_source).expanduser()), str(profile), _rsync())
        except ProfileError as error:
            print(f"neuman-browser: {error}", file=sys.stderr)
            return 1

    socket_path = Path(args.socket).expanduser() if args.socket else None
    if not socket_path:
        print(
            "neuman-browser: no --socket and no user runtime directory to default one",
            file=sys.stderr,
        )
        return 1

    stop = threading.Event()

    def _stop(*_args: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    # A second host on the same port would leave the browser alive with its
    # DevTools port unclaimed, which is indistinguishable from a healthy unit
    # from outside; refuse the port rather than start a browser that cannot be
    # reached (N184).
    if _devtools_answers(args.port):
        print(
            f"neuman-browser: 127.0.0.1:{args.port} already answers, so {chromium} "
            f"cannot claim it; another browser host owns the port. Stop it, or pass "
            f"--port with a free one.",
            file=sys.stderr,
        )
        return 1

    browser = subprocess.Popen(
        [
            chromium,
            f"--remote-debugging-port={args.port}",
            f"--user-data-dir={profile}",
            *display_args(),
            "--no-first-run",
            "--no-default-browser-check",
            args.url,
        ]
    )

    # The bridge is the point of the process, so it runs in the main thread: a
    # socket that cannot be created must fail the unit rather than kill a daemon
    # thread and leave a browser running that nothing can reach.
    outcome = {"code": 0, "quit": False}

    def _watch_browser() -> None:
        exited = browser.wait()
        if not stop.is_set():
            outcome["code"] = exited
            outcome["quit"] = True
        stop.set()

    threading.Thread(target=_watch_browser, daemon=True).start()

    if not _wait_for_devtools(args.port, lambda: browser.poll() is None, DEVTOOLS_TIMEOUT_S):
        exited = browser.poll()
        stop.set()
        _shut_down(browser)
        if exited is None:
            print(
                f"neuman-browser: {chromium} started but nothing answered on "
                f"127.0.0.1:{args.port} within {DEVTOOLS_TIMEOUT_S:g}s, and that port is "
                f"how `browse` reaches it. A snap-confined Chromium needs "
                f"`snap connect chromium:network-bind`; a browser started on another "
                f"instance's profile opens no port at all.",
                file=sys.stderr,
            )
            return 1
        if exited > 0:
            _explain_exit(exited, chromium, profile)
            return exited
        return 1

    try:
        serve(socket_path, args.port, stop)
    except OSError as error:
        print(f"neuman-browser: cannot serve {socket_path}: {error}", file=sys.stderr)
        print(
            f"neuman-browser: the socket lives in the user's runtime directory; "
            f"{runtime_hint()}",
            file=sys.stderr,
        )
        outcome["code"] = 1
    finally:
        stop.set()
        _shut_down(browser)

    if outcome["quit"] and outcome["code"] > 0:
        _explain_exit(outcome["code"], chromium, profile)
    return outcome["code"]


if __name__ == "__main__":
    raise SystemExit(main())
