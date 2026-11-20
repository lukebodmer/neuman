"""Connect to the core as the account, the way a real client does, and read
back the account's working set.

The core dispatches over a WebSocket, and a tool call needs a turn with a model
behind it — so what this proves is the path a real client takes to become
dispatchable: log in, open `/client`, complete the handshake, and be registered
with the tools it says it can run. The call itself is covered by the client's
own tests, which drive the connection and the executor directly.

**And the working set, against the real database.** `GET /api/active` is one
query over `agent_sessions`, its branches and its event log, with the `read_at`
column and an EXISTS over replies — and the server's own tests fake the
connection, so a malformed query would otherwise be found by the first window
that opened the rail. The probe creates a session through the same API the UI
uses and reads it back.

Reads one JSON argument:

    {"server": "http://127.0.0.1:8443", "user": "alice", "password": "...",
     "state": "/tmp/probe-session", "tools": ["fs_read"]}

and prints:

    CONNECTED <user> <comma-separated tools>
    ACTIVE <state> <session id>

or, when the core refused:

    REFUSED <message>
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


async def run(request: dict) -> int:
    from neuman_client.daemon import hello_payload
    from neuman_client.session import Session, SessionError
    from neuman_client.transport import COOKIE, WebSocketTransport

    session = Session(Path(request["state"]))
    try:
        session.login(
            server=request["server"],
            username=request["user"],
            password=request["password"],
        )
    except SessionError as error:
        print(f"REFUSED {error}")
        return 0

    scheme = "wss" if request["server"].startswith("https") else "ws"
    host = request["server"].split("://", 1)[-1].rstrip("/")
    transport = WebSocketTransport(
        url=f"{scheme}://{host}/client",
        cookie=lambda: session.cookie(),
    )
    tools = list(request.get("tools", []))

    try:
        connection = await transport.connect()
    except Exception as error:  # noqa: BLE001 - the test asserts on the line
        print(f"REFUSED {error}")
        return 0

    try:
        await connection.send(
            hello_payload(
                # The client asks the account database on its own host; a probe
                # is not the client, and what this proves is the core's side of
                # the handshake, so this process's own home is answer enough.
                home=str(Path.home()),
                drops=str(Path.home() / ".local" / "state" / "neuman" / "drops"),
                tools=tools,
            )
        )
        welcome = await asyncio.wait_for(connection.recv(), timeout=10)
    except Exception as error:  # noqa: BLE001
        print(f"REFUSED {error}")
        return 0
    finally:
        await connection.close()

    if welcome.get("type") != "welcome":
        print(f"REFUSED {welcome.get('message', welcome.get('type'))}")
        return 0
    # The cookie name is asserted rather than assumed: a change to it is a
    # change to how a daemon authenticates, and this is the one place a real
    # login is exercised end to end.
    assert COOKIE == "neuman_session"
    print(f"CONNECTED {welcome.get('user')} {','.join(sorted(tools))}")
    print(f"ACTIVE {working_set(request['server'], session.cookie())}")
    return 0


def working_set(server: str, cookie: str | None) -> str:
    """Create a session the way the UI does, then read the account's set back.

    Two requests, and both matter: the create is what puts a row in the window,
    and the read is the query this probe exists to run — laterals over branches
    and events, the `read_at` column, and the unread comparison. A session with
    no reply is `idle`, which is also the assertion that the derived flag is not
    simply always true.
    """

    import httpx
    from neuman_client.transport import COOKIE

    with httpx.Client(base_url=server, cookies={COOKIE: cookie or ""}, timeout=10) as client:
        csrf = client.get("/api/me").json()["csrf_token"]
        created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf})
        created.raise_for_status()
        session_id = created.json()["id"]
        rows = client.get("/api/active").json()["sessions"]

    row = next((item for item in rows if item["id"] == session_id), None)
    if row is None:
        return f"missing {session_id}"
    return f"{row['state']} {session_id}"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: client_probe.py '<request json>'", file=sys.stderr)
        return 2
    return asyncio.run(run(json.loads(sys.argv[1])))


if __name__ == "__main__":
    raise SystemExit(main())
