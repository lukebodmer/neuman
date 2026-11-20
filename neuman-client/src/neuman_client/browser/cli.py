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
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

from ..platform import (
    browse_socket,
    browser_copy_source,
    browser_profile_dir,
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
    socket = browse_socket()
    parser.add_argument("--socket", default=str(socket) if socket else None)
    parser.add_argument("--profile", choices=("own", "copy"), default="own")
    parser.add_argument("--user-data-dir", default=str(browser_profile_dir()))
    parser.add_argument("--copy-source", default=str(browser_copy_source()))
    parser.add_argument("--chromium", default=None)
    return parser


def _copy_profile(source: str, destination: str, rsync: str | None) -> None:
    if rsync is None or not os.path.isdir(source):
        return
    os.makedirs(destination, exist_ok=True, mode=0o700)
    subprocess.run(
        [rsync, "-a", "--delete", f"{source}/", f"{destination}/"],
        check=False,
        timeout=120,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    chromium = args.chromium or find_chromium()
    if not chromium:
        print(
            "neuman-browser: no Chromium found; pass --chromium or set NEUMAN_BROWSER_CHROMIUM",
            file=sys.stderr,
        )
        return 1

    profile = Path(args.user_data_dir).expanduser()
    os.makedirs(profile, exist_ok=True, mode=0o700)
    if args.profile == "copy":
        _copy_profile(str(Path(args.copy_source).expanduser()), str(profile), _rsync())

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
    outcome = {"code": 0}

    def _watch_browser() -> None:
        outcome["code"] = browser.wait()
        stop.set()

    threading.Thread(target=_watch_browser, daemon=True).start()

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
        browser.terminate()
        try:
            browser.wait(timeout=5)
        except subprocess.TimeoutExpired:
            browser.kill()

    return outcome["code"]


if __name__ == "__main__":
    raise SystemExit(main())
