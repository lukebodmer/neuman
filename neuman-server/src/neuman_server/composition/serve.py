"""Running two listeners in one process: the network port, and the admin socket.

D9 gives the appliance two surfaces over one core. Until now only one of them
was listening. The admin routes were mounted on the browser app, reachable over
TCP, where they refused for want of peer credentials — the gap N26 recorded and
`guard.py` made safe. This module is the other half: the socket where an
operator can actually succeed.

**Why one process rather than two.** The surfaces share `Services` — one
database pool, one registry, one set of live sessions — and a second process
would need a second pool and a way to keep the two views of session state from
drifting. They also share a lifespan: migrations must run once, before either
surface answers. Two uvicorn servers over one app in one event loop keeps a
single startup ordering and a single source of truth.

**Why the lifespan runs on exactly one of them.** Uvicorn runs an app's
lifespan per `Server`, so two servers over the same app would migrate twice and
start two copies of every background job. The network server owns it; the admin
server is configured with `lifespan="off"` and waits until startup has
finished. That also fixes the ordering an operator would otherwise trip over:
the admin socket appears only once the database is migrated, so a CLI that can
connect is talking to a server that can answer.

**Why the admin socket's mode is set here.** The socket is the privilege
boundary — anything that can connect gets as far as `require_admin` — so its
mode is part of the security argument rather than a deployment detail. uvicorn
creates it 0777 minus umask, which depends on whatever the unit inherited.
Setting it explicitly is not a substitute for peer-credential authentication;
it is the layer that keeps unrelated accounts from reaching the surface at all.
"""

from __future__ import annotations

import asyncio
import os
import socket
from pathlib import Path
from typing import Any

from ..foundation import Settings, get_logger

__all__ = ["ADMIN_SOCKET_MODE", "admin_config", "bind_admin_socket", "serve"]

_log = get_logger("neuman.server")

# rwx for the owner, rwx for the group, nothing for anyone else. The unit runs
# the server as `neuman`; an operator reaches the socket by being in the
# `neuman` group, and the uid the kernel reports is then checked against
# operator authority by the core.
ADMIN_SOCKET_MODE = 0o660


def bind_admin_socket(path: Path) -> socket.socket:
    """Bind the admin socket with its mode set before anything can connect.

    Binding here rather than letting uvicorn do it is what makes the ordering
    safe: `bind` then `chmod` leaves a window in which the socket exists at
    whatever umask produced, and a window is all a local attacker needs. The
    umask is set around the bind so the socket is never briefly permissive.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    # A socket file left by a previous run would make bind() fail with
    # EADDRINUSE even though nothing is listening.
    if path.exists():
        path.unlink()

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    previous = os.umask(0o777 & ~ADMIN_SOCKET_MODE)
    try:
        sock.bind(str(path))
    finally:
        os.umask(previous)
    sock.listen(128)
    return sock


def admin_config(app: Any) -> Any:
    """A uvicorn config whose connections carry their peer's uid.

    Exposed rather than private because the test that proves peer credentials
    arrive must build its server the same way this one does. A test that wired
    its own would be proving its own wiring.
    """

    import uvicorn

    from .adminapi.peercred import peer_credential_protocol

    config = uvicorn.Config(
        app,
        log_config=None,
        access_log=False,
        # The network server owns the lifespan; running it twice would migrate
        # twice and start two copies of every background job.
        lifespan="off",
    )

    # `http_protocol_class` is resolved by `load()`, not by the constructor —
    # reading it before loading raises AttributeError. Loading here also means
    # the wrapper is applied to the implementation uvicorn actually selected
    # (h11 or httptools) rather than to one assumed here. `load()` is
    # idempotent, so uvicorn calling it again later is harmless.
    config.load()
    config.http_protocol_class = peer_credential_protocol(config.http_protocol_class)
    return config


async def serve(app: Any, settings: Settings) -> None:
    """Run both listeners until one stops.

    The network server is authoritative for startup and shutdown; if it exits,
    the admin server is cancelled with it, because an admin socket attached to
    a server that has shut its pool is worse than no socket.
    """

    import uvicorn

    ssl_args: dict[str, Any] = {}
    if settings.tls_enabled:
        ssl_args = {
            "ssl_certfile": str(settings.tls_cert),
            "ssl_keyfile": str(settings.tls_key),
        }

    network = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.bind,
            port=settings.port,
            log_config=None,
            access_log=False,
            **ssl_args,
        )
    )

    admin_sock = bind_admin_socket(settings.admin_socket)
    admin = uvicorn.Server(admin_config(app))

    # Not "listening": neither socket accepts until the lifespan has migrated, and
    # a startup that fails there would otherwise read as healthy until the traceback.
    _log.info(
        "starting",
        fields={
            "bind": settings.bind,
            "port": settings.port,
            "admin_socket": str(settings.admin_socket),
        },
    )

    network_task = asyncio.create_task(network.serve(), name="neuman-network")
    admin_task = asyncio.create_task(admin.serve(sockets=[admin_sock]), name="neuman-admin")

    try:
        done, _pending = await asyncio.wait(
            {network_task, admin_task}, return_when=asyncio.FIRST_COMPLETED
        )
        # Surface a crash rather than exiting 0 because a task "finished".
        for task in done:
            task.result()
    finally:
        admin.should_exit = True
        network.should_exit = True
        for task in (admin_task, network_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(admin_task, network_task, return_exceptions=True)
        admin_sock.close()
        settings.admin_socket.unlink(missing_ok=True)
