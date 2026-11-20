"""The admin socket: peer credentials, end to end through a real uvicorn.

This is the test N26 needed. Every other admin test asserts a *refusal*, which
a surface that can never succeed passes perfectly — `peer_name` looked for two
scope keys nothing ever set, so it raised on every connection, and the whole
admin API was indistinguishable from a correctly-guarded one.

So the load-bearing assertion here is the positive one: a request over the Unix
socket is attributed to the uid that made it. It runs a real uvicorn over a
real `AF_UNIX` socket, because the bug lived precisely in the gap between what
ASGI exposes and what uvicorn puts in the scope, and nothing that stubs either
side can see it.
"""

from __future__ import annotations

import asyncio
import os
import socket

import pytest

from neuman_server.composition.adminapi.peercred import PEER_UID, read_peer_uid
from neuman_server.composition.serve import (
    ADMIN_SOCKET_MODE,
    admin_config,
    bind_admin_socket,
)

# -- reading the credential -------------------------------------------------


def test_a_unix_socket_reports_its_peer(tmp_path):
    path = tmp_path / "t.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(str(path))
    accepted, _ = server.accept()
    try:
        assert read_peer_uid(accepted) == os.getuid()
    finally:
        client.close()
        accepted.close()
        server.close()


def test_a_tcp_socket_has_no_peer_credentials():
    """The property the whole guard rests on: no peer over the network.

    If this ever returns a uid, `peer_name` starts authenticating TCP callers
    and the admin API's authentication becomes whatever the kernel felt like.
    """

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(server.getsockname())
    accepted, _ = server.accept()
    try:
        assert read_peer_uid(accepted) is None
    finally:
        client.close()
        accepted.close()
        server.close()


def test_no_socket_is_not_an_error():
    assert read_peer_uid(None) is None


# -- the socket's mode ------------------------------------------------------


def test_the_admin_socket_is_not_world_reachable(tmp_path):
    """The mode is part of the security argument, not a deployment detail.

    uvicorn would create it 0777 minus whatever umask the unit inherited.
    """

    path = tmp_path / "admin.sock"
    sock = bind_admin_socket(path)
    try:
        mode = path.stat().st_mode & 0o777
        assert mode == ADMIN_SOCKET_MODE, oct(mode)
        assert not mode & 0o007, f"world has access: {oct(mode)}"
    finally:
        sock.close()


def test_binding_replaces_a_socket_left_by_a_previous_run(tmp_path):
    """Otherwise a crash means the next start fails with EADDRINUSE."""

    path = tmp_path / "admin.sock"
    first = bind_admin_socket(path)
    first.close()
    second = bind_admin_socket(path)  # must not raise
    second.close()


# -- through a real uvicorn -------------------------------------------------


async def _serve(sock_path, app):
    """Serve `app` on `sock_path` exactly as the appliance would.

    Through `admin_config` rather than a hand-rolled equivalent: a test that
    wires its own server proves its own wiring.
    """

    import uvicorn

    server = uvicorn.Server(admin_config(app))
    listening = bind_admin_socket(sock_path)
    task = asyncio.create_task(server.serve(sockets=[listening]))
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    else:  # pragma: no cover
        pytest.fail("uvicorn never started")
    return server, task, listening


@pytest.mark.asyncio
async def test_a_request_over_the_socket_carries_the_peers_uid(tmp_path):
    """The assertion the admin API exists for.

    Asserting only refusals would pass against a surface with no way to
    succeed, which is exactly what was shipped (N26).
    """

    import httpx

    seen: dict = {}

    async def app(scope, receive, send):
        seen.update(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    path = tmp_path / "admin.sock"
    server, task, listening = await _serve(path, app)
    try:
        transport = httpx.AsyncHTTPTransport(uds=str(path), verify=False)
        # `verify=False`: neither connection here is TLS, and httpx builds a
        # default SSL context at construction — which reads a CA bundle that
        # does not exist inside the Nix build sandbox.
        async with httpx.AsyncClient(transport=transport, verify=False) as client:
            response = await client.get("http://admin/whoami")
        assert response.status_code == 200
        assert seen.get(PEER_UID) == os.getuid(), (
            f"scope carried {seen.get(PEER_UID)!r}; uvicorn's scope has no "
            "peer credentials unless the protocol puts them there"
        )
    finally:
        server.should_exit = True
        await task
        listening.close()


@pytest.mark.asyncio
async def test_the_scope_has_no_peer_uid_over_tcp(tmp_path):
    """The same app on a port must not be attributable.

    This is the other half: the wrapper adds a credential where one exists and
    must add nothing where one does not, or the guard it feeds is decoration.
    """

    import httpx
    import uvicorn

    seen: dict = {}

    async def app(scope, receive, send):
        seen.update(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    # The same configuration the admin socket uses, on a port instead.
    server = uvicorn.Server(admin_config(app))

    listening = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listening.bind(("127.0.0.1", 0))
    listening.listen(16)
    port = listening.getsockname()[1]

    task = asyncio.create_task(server.serve(sockets=[listening]))
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        async with httpx.AsyncClient(verify=False) as client:
            await client.get(f"http://127.0.0.1:{port}/whoami")
        assert PEER_UID not in seen, f"TCP connection was attributed to {seen[PEER_UID]!r}"
    finally:
        server.should_exit = True
        await task
        listening.close()
