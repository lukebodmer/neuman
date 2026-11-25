"""The `/client` channel: who may open it, and what the handshake must say.

The hub's own tests drive it through a fake channel, and a VM test logs in and
connects for real — but neither exercises *this route*, so everything it
refuses was refused without being asserted. These are the refusals a client
daemon meets when it is misconfigured: no session, a dead session, a hello that
is not one, and a home the core cannot use.

The happy path is the one that matters: the connection the route hands the hub
carries the account the *session* resolved to, the tools the client advertised
and the home it reported — never an account name from the client (D67).
"""

from __future__ import annotations

import pytest
from surface import build_surface

COOKIE = "neuman_session"


@pytest.fixture
def surface(monkeypatch, tmp_path):
    return build_surface(monkeypatch, tmp_path)


@pytest.fixture
def client(surface):
    with surface.client() as running:
        yield running


def _hello(**overrides: object) -> dict:
    message = {
        "type": "hello",
        "rpc_version": 2,
        "machine": {"label": "ernest", "kind": "client"},
        "home": "/home/alice",
        "drops": "/home/alice/.local/state/neuman/drops",
        "tools": ["fs_read", "shell"],
    }
    message.update(overrides)
    return message


def _login(surface, client, name: str = "alice") -> None:
    token, _csrf = surface.login(name)
    client.cookies.set(COOKIE, token)


def test_a_connection_without_a_session_is_closed(client):
    """No cookie, no channel: the socket is a daemon's, and a daemon has a session."""

    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/client"):
            pass
    assert caught.value.code == 4401


def test_a_connection_with_a_dead_session_is_closed(client):
    from starlette.websockets import WebSocketDisconnect

    client.cookies.set(COOKIE, "not-a-session")
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/client"):
            pass
    assert caught.value.code == 4401


def test_a_hello_without_a_home_is_refused(surface, client):
    """N145: without it, every `$HOME` grant expands against the wrong machine."""

    from starlette.websockets import WebSocketDisconnect

    _login(surface, client)
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/client") as socket:
            socket.send_json(_hello(home=None))
            message = socket.receive_json()
            assert "home directory" in message["message"], message
            socket.receive_json()
    assert caught.value.code == 4400
    assert surface.services.execution.connections == []


@pytest.mark.parametrize("drops", [None, "relative/drops", "/"])
def test_a_hello_without_a_usable_drops_directory_is_refused(surface, client, drops):
    """D102: a guessed directory would accept attachments the client never wrote,
    and `/` would accept every path on the machine."""

    from starlette.websockets import WebSocketDisconnect

    _login(surface, client)
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/client") as socket:
            socket.send_json(_hello(drops=drops))
            message = socket.receive_json()
            assert "drops directory" in message["message"], message
            socket.receive_json()
    assert caught.value.code == 4400
    assert surface.services.execution.connections == []


def test_a_hello_without_a_machine_is_refused(surface, client):
    """D112: a dispatch has to land somewhere nameable.

    Refused rather than defaulted, which is the whole difference from the old
    behaviour: a guessed label would put a dispatch on one computer and record
    it as another, and the one thing worse than "I do not know where you are" is
    a record that says.
    """

    from starlette.websockets import WebSocketDisconnect

    _login(surface, client)
    broken = _hello()
    del broken["machine"]
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/client") as socket:
            socket.send_json(broken)
            message = socket.receive_json()
            assert "machine" in message["message"], message
            socket.receive_json()
    assert caught.value.code == 4400
    assert surface.services.execution.connections == []


@pytest.mark.parametrize(
    "machine",
    [
        # A label is lower case and starts with a letter or digit: it is typed by
        # people and compared as a string, so a name differing only in case
        # would be two machines or one depending on who was asking.
        {"label": "Ernest Laptop", "kind": "client"},
        {"label": "-ernest", "kind": "client"},
        # Reserved by the schema and not spoken by this build. Refused rather
        # than half-served: there is no channel to dispatch down.
        {"label": "dgx", "kind": "ssh"},
        "ernest",
    ],
)
def test_a_hello_with_an_unusable_machine_is_refused(surface, client, machine):
    from starlette.websockets import WebSocketDisconnect

    _login(surface, client)
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/client") as socket:
            socket.send_json(_hello(machine=machine))
            socket.receive_json()
            socket.receive_json()
    assert caught.value.code == 4400
    assert surface.services.execution.connections == []


def test_a_relative_home_is_refused(surface, client):
    """A path fragment is not a home, and it would silently expand to nothing."""

    from starlette.websockets import WebSocketDisconnect

    _login(surface, client)
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/client") as socket:
            socket.send_json(_hello(home="home/alice"))
            socket.receive_json()
            socket.receive_json()
    assert caught.value.code == 4400
    assert surface.services.execution.connections == []


def test_a_hello_that_is_not_one_is_refused(surface, client):
    from starlette.websockets import WebSocketDisconnect

    _login(surface, client)
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/client") as socket:
            socket.send_json({"type": "message", "rpc_version": 1, "content": "hi"})
            socket.receive_json()
    assert caught.value.code == 4400
    assert surface.services.execution.connections == []


def test_a_good_hello_registers_the_client_and_is_answered(surface, client):
    """The happy path, asserted on what the core now believes.

    The hub is faked here, so what this checks is the route's half: the
    connection it hands over carries the account the *session* resolved to, the
    tools the client advertised and the home it reported — and it is withdrawn
    when the socket closes, so a dead client is not left dispatchable. The
    welcome also says what the appliance is, because a client has no other way
    to learn it (D111).
    """

    from neuman_contracts import dispatch_rpc

    from neuman_server import __version__

    _login(surface, client)
    with client.websocket_connect("/client") as socket:
        socket.send_json(_hello())
        welcome = socket.receive_json()
        # The shape the contract describes, not only the fields this test names:
        # both halves ship `contracts/`, and a frame the schema would refuse is
        # one an older client may refuse too.
        dispatch_rpc.validate(welcome)

        assert welcome == {
            "type": "welcome",
            "rpc_version": 2,
            "user": "alice",
            "machine": {"label": "ernest", "kind": "client"},
            "release": __version__,
            "rpc_versions": [2],
            "minimum_client": {"release": "0.1.1", "rpc": 2},
        }
        (connection,) = surface.services.execution.connections
        assert connection.account == "alice"
        assert connection.machine == "ernest"
        assert connection.tools == frozenset({"fs_read", "shell"})
        assert connection.home == "/home/alice"
        assert connection.rpc_version == 2
        assert connection.release is None, "a hello that did not say is unknown, not empty"

    assert surface.services.execution.connections == []


def test_a_hello_that_names_its_release_is_recorded(surface, client):
    """It is what an operator sees per machine before installing a floor (D111)."""

    _login(surface, client)
    with client.websocket_connect("/client") as socket:
        socket.send_json(_hello(release="0.2.0"))
        socket.receive_json()
        (connection,) = surface.services.execution.connections
        assert connection.release == "0.2.0"


def test_a_shape_this_build_does_not_speak_is_refused_with_both_ends(surface, client):
    """The refusal a daemon can act on, rather than a close code it retries.

    Three facts have to travel or the client cannot say which half to update:
    what the appliance is, what the client said it is, and the shapes that would
    have been accepted. A bare `4400` is the reconnect loop this replaces.
    """

    from neuman_contracts import dispatch_rpc
    from starlette.websockets import WebSocketDisconnect

    from neuman_server import __version__

    _login(surface, client)
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/client") as socket:
            socket.send_json(_hello(rpc_version=99, release="0.1.0"))
            message = socket.receive_json()
            dispatch_rpc.validate(message)
            assert message["type"] == "error"
            assert message["code"] == "version_mismatch"
            assert "99" in message["message"], message
            assert message["versions"]["accepted"] == [2]
            assert message["versions"]["server"] == {
                "release": __version__,
                "rpc": 2,
            }
            assert message["versions"]["client"] == {"release": "0.1.0", "rpc": 99}
            socket.receive_json()
    assert caught.value.code == 4406
    assert surface.services.execution.connections == []


def test_a_hello_with_no_shape_at_all_is_refused_the_same_way(surface, client):
    """A string where a number belongs is not a protocol this build speaks."""

    from starlette.websockets import WebSocketDisconnect

    _login(surface, client)
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/client") as socket:
            socket.send_json(_hello(rpc_version="one"))
            message = socket.receive_json()
            assert message["code"] == "version_mismatch"
            assert message["versions"]["client"]["rpc"] is None
            socket.receive_json()
    assert caught.value.code == 4406
