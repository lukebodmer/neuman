"""Run the Neuman client: the executor, the UI, and the session they use.

Two commands. `login` checks a credential against a core and keeps the cookie it
returns; `run` (the default) serves the UI locally, connects to the core, and
executes what the core dispatches. The login is the same one a person would
perform in the UI — this command exists so a client can be brought up before the
UI is open, and so a scripted deployment has a way in.
"""

from __future__ import annotations

import argparse
import asyncio
import errno
import getpass
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit

from .daemon import ClientDaemon
from .drops import DropStore
from .executor import Executor
from .hosts import HostManager
from .jobs import JobRegistry
from .platform import (
    browse_socket,
    browser_profile_dir,
    find_chromium,
    home_dir,
    runtime_hint,
    service_status_hint,
    state_dir,
)
from .session import Session, SessionError
from .tools import ToolHost, ToolHostError
from .transport import WebSocketTransport
from .ui import UiServer

__all__ = ["main"]

DEFAULT_SERVER = "https://127.0.0.1:8443"
DEFAULT_UI_PORT = 8642


# Loopback, where http is not a network: the cookie is `Secure`, and a browser
# treats `http://127.0.0.1` as a secure context, but a daemon has no such notion
# — it will happily present the cookie over plain HTTP. So the rule is the
# client's own: http is loopback only, and anything else must be https.
LOOPBACK = ("127.0.0.1", "localhost", "::1", "[::1]")


def _secure_scheme(server: str) -> str | None:
    """The reason `server` is not safe to send a password to, or None.

    The password is the credential that crosses first, and the session cookie
    crosses with every request after it. Both are readable by anything on the
    path, so a remote core over http is refused rather than warned about: a
    warning printed once, at login, is not what anyone reads a week later.
    """

    parts = urlsplit(server if "://" in server else f"http://{server}")
    if parts.scheme == "https":
        return None
    if parts.hostname in LOOPBACK:
        return None
    return (
        f"{server} is not https and is not loopback. The password you are about "
        "to send, and the session cookie afterwards, would cross the network in "
        "clear — put TLS in front of the core, or use a tunnel."
    )


def _client_url(server: str) -> str:
    """The core's `/client` socket, in the WebSocket scheme it is served under."""

    scheme = "wss" if server.startswith("https") else "ws"
    host = server.split("://", 1)[-1].rstrip("/")
    return f"{scheme}://{host}/client"


def _private_write(path: Path, value: str) -> None:
    """Write a small secret, 0600 from creation rather than after."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(value)


def _ui_token(state: Path) -> str:
    """The credential this daemon mints for its own UI.

    Stable across restarts, because a restart should not invalidate the cookie
    the open browser is already holding.
    """

    path = state / "ui-token"
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if value:
        return value
    value = secrets.token_urlsafe(32)
    _private_write(path, value)
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="neuman-client",
        description="Run Neuman's tools on this machine, as you.",
    )
    parser.add_argument("--state-dir", default=str(state_dir()))
    sub = parser.add_subparsers(dest="command")

    socket = browse_socket()

    run = sub.add_parser("run", help="serve the UI, connect, and execute dispatches")
    run.add_argument("--server", default=os.environ.get("NEUMAN_CLIENT_SERVER"))
    run.add_argument("--manifests", default=os.environ.get("NEUMAN_CLIENT_MANIFESTS"))
    run.add_argument("--bin", default=os.environ.get("NEUMAN_CLIENT_BIN"))
    run.add_argument("--web-root", default=os.environ.get("NEUMAN_CLIENT_WEB"))
    run.add_argument("--ui-port", type=int, default=DEFAULT_UI_PORT)
    run.add_argument("--ui-host", default="127.0.0.1")
    run.add_argument(
        "--browse-socket",
        default=str(socket) if socket else None,
        help="the bridge socket the tools dial for `browse`",
    )

    login = sub.add_parser("login", help="check a credential and keep the session")
    login.add_argument(
        "--server", default=os.environ.get("NEUMAN_CLIENT_SERVER", DEFAULT_SERVER)
    )
    login.add_argument(
        "--user",
        required=True,
        help="the account on the core, not this machine's account",
    )
    login.add_argument(
        "--password-file",
        default=None,
        help="read the password from this file instead of prompting (for a service)",
    )

    sub.add_parser("doctor", help="report what this host can and cannot do")

    return parser


def _run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if not args.manifests or not args.bin:
        parser.error("--manifests and --bin are required (or set NEUMAN_CLIENT_MANIFESTS/BIN)")

    state = Path(args.state_dir).expanduser()
    # One path for both ends: the executor writes spilled output here and the
    # tool host lets the read tools reach it.
    spill = state / "spill"
    # Files dropped on the window (D102): written by the UI server, readable
    # by the read tools, and named to the core so it can accept them as
    # attachments. One object, so the three agree on the path.
    drops = DropStore(state / "drops")
    try:
        host = ToolHost(
            manifests_dir=args.manifests,
            bin_dir=args.bin,
            browse_socket=args.browse_socket,
            spill_dir=spill,
            drops_dir=drops.root,
        )
    except ToolHostError as error:
        print(f"neuman-client: unusable manifests: {error}", file=sys.stderr)
        return 2

    session = Session(state / "session.json")

    server = args.server or session.server()
    if server and (unsafe := _secure_scheme(server)):
        print(f"neuman-client: {unsafe}", file=sys.stderr)
        return 2
    if not server:
        print(
            "neuman-client: no --server and no stored session; "
            "run `neuman-client login --server <url>` first",
            file=sys.stderr,
        )
        return 2

    ui: UiServer | None = None
    # One registry, two readers: the daemon runs the jobs, the UI server lists
    # and stops them (D98). Built here because this is where both are wired.
    jobs = JobRegistry()
    if args.web_root:
        ui = UiServer(
            web_root=args.web_root,
            server_url=server,
            session=session,
            token=_ui_token(state),
            port=args.ui_port,
            host=args.ui_host,
            jobs=jobs,
            drops=drops,
        )
        # The browser host reads this rather than being told the URL, so a
        # launch does not have to carry a token through a command line.
        _private_write(state / "ui-url", ui.url)

    daemon = ClientDaemon(
        transport=WebSocketTransport(url=_client_url(server), cookie=session.cookie),
        home=str(home_dir()),
        drops=str(drops.root),
        host=host,
        executor=Executor(spill_dir=spill),
        jobs=jobs,
        hosts=HostManager(state / "hosts"),
    )

    async def both() -> None:
        if ui is not None:
            await ui.start()
        try:
            await daemon.run()
        finally:
            if ui is not None:
                await ui.stop()

    try:
        asyncio.run(both())
    except KeyboardInterrupt:
        return 0
    except OSError as error:
        # The one OSError that reaches here is the UI failing to bind, and the
        # cause is nearly always another client already holding the port — the
        # user service the module installs, most of the time. A traceback for
        # that is a wall of store paths around one sentence.
        if error.errno != errno.EADDRINUSE:
            raise
        print(
            f"neuman-client: cannot serve the UI on {args.ui_host}:{args.ui_port}: "
            "address already in use",
            file=sys.stderr,
        )
        print(
            f"  a client is probably already running — {service_status_hint()}",
            file=sys.stderr,
        )
        print("  or serve this one elsewhere: --ui-port <port>", file=sys.stderr)
        return 1
    return 0


def _doctor(args: argparse.Namespace) -> int:
    """Report the facts a Mac support conversation always starts by asking for.

    No secret is printed: the session file's server is useful, its cookie is
    not. The socket length check is here because macOS's AF_UNIX limit is small
    and a path that is a few bytes too long fails at bind time with an error
    that does not name the constraint.
    """

    state = Path(args.state_dir).expanduser()
    session = Session(state / "session.json")
    socket = browse_socket()
    browser = find_chromium()
    profile = browser_profile_dir()

    print(f"python: {sys.version.split()[0]} ({sys.executable})")
    print(f"host: {sys.platform} {os.uname().machine}")
    print(f"state: {state}")
    print(f"session: {'present' if session.cookie() else 'absent'}")
    print(f"server: {session.server() or 'none stored'}")
    print(f"browser profile: {profile}")
    print(f"browser: {browser or 'not found'}")

    limit = 104 if sys.platform == "darwin" else 108
    length = 0
    if socket is None:
        print(f"browse socket: none ({runtime_hint()})")
    else:
        length = len(os.fsencode(str(socket)))
        print(f"browse socket: {socket}")
        print(f"socket path length: {length} of {limit} bytes")

    if sys.platform == "darwin":
        print(
            "macOS TCC: Desktop, Documents and Downloads can prompt; a "
            "LaunchAgent may need Full Disk Access for the same paths"
        )

    if browser is None or socket is None or length >= limit:
        return 1
    return 0


def _login(args: argparse.Namespace) -> int:
    if unsafe := _secure_scheme(args.server):
        print(f"neuman-client: {unsafe}", file=sys.stderr)
        return 2
    user = args.user
    if args.password_file:
        password = Path(args.password_file).read_text(encoding="utf-8").rstrip("\n")
    else:
        password = getpass.getpass(f"password for {user}@{args.server}: ")
    try:
        Session(Path(args.state_dir).expanduser() / "session.json").login(
            server=args.server, username=user, password=password
        )
    except SessionError as error:
        print(f"neuman-client: {error}", file=sys.stderr)
        return 1
    print(f"neuman-client: logged in to {args.server} as {user}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    if argv is None:
        argv = sys.argv[1:]
    # `run` is the default: `neuman-client` with no subcommand is the common case.
    if not argv:
        argv = ["run"]
    args = parser.parse_args(argv)

    if args.command == "login":
        return _login(args)
    if args.command == "doctor":
        return _doctor(args)
    if args.command in (None, "run"):
        return _run(args, parser)
    parser.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover - parser.error exits


if __name__ == "__main__":
    raise SystemExit(main())
