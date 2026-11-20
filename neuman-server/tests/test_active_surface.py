"""The working set: what the account has open, in every window (D63).

`GET /api/active` is the rail's read. Its membership, its order and its unread
flag are all facts about the account rather than about one browser document, so
two windows — and two devices — draw the same list; only the word `open` is the
window's own.

The store's SQL is modelled by the fake here (the server's suite fakes the
connection; the real statement runs in the appliance VM test), so these assert
the *contract* the route rests on: `read_at` and activity both keep a row, the
order is the conversation rather than the opening, and a reply is unseen until
the watermark passes it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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


def sign_in(surface, client, name: str = "alice") -> str:
    token, csrf = surface.login(name)
    client.cookies.set(COOKIE, token)
    return csrf


def seed(surface, *, age: timedelta, account: str = "alice", read: timedelta | None = None):
    """A session created `age` ago, optionally looked at `read` ago."""

    row = surface.agent_sessions.seed(account_id=f"acct-{account}")
    row.created_at = datetime.now(UTC) - age
    row.last_activity = row.created_at
    if read is not None:
        row.read_at = datetime.now(UTC) - read
    return row


def listed(client) -> list[dict]:
    return client.get("/api/active").json()["sessions"]


def test_activity_inside_the_window_keeps_a_session_in_the_set(surface, client):
    """A conversation used half an hour ago is in the working set; a day-old one is not.

    The window is the account's (`active_minutes`, 30 by default), and it is the
    same number every window reads — which is the whole point of the list being
    the server's.
    """

    sign_in(surface, client)
    recent = seed(surface, age=timedelta(minutes=10))
    stale = seed(surface, age=timedelta(days=1))

    ids = [row["id"] for row in listed(client)]
    assert recent.id in ids
    assert stale.id not in ids


def test_opening_a_session_keeps_it_in_the_set_without_making_it_recent(surface, client):
    """`read_at` is the second clock: opening an old session keeps its row.

    It does **not** move it in the order, which is the conversation's — that is
    the next test. Here the point is only that a session you opened and did not
    type into does not vanish the moment the window turns over.
    """

    sign_in(surface, client)
    old = seed(surface, age=timedelta(days=1), read=timedelta(minutes=1))

    row = next(row for row in listed(client) if row["id"] == old.id)
    assert row["state"] == "idle", row


def test_the_order_is_the_conversation_not_the_opening(surface, client):
    """A just-opened old session sorts below one that was actually used recently.

    Clicking a row must not lift it over the session being worked in: the order
    is "when was anything last said or done here", and `read_at` is deliberately
    not part of it.
    """

    sign_in(surface, client)
    old = seed(surface, age=timedelta(days=2))
    old.last_activity = datetime.now(UTC) - timedelta(hours=3)
    old.read_at = datetime.now(UTC)
    recent = seed(surface, age=timedelta(minutes=20))

    ids = [row["id"] for row in listed(client)]
    assert ids.index(recent.id) < ids.index(old.id), ids


def test_a_reply_is_unseen_until_somebody_looks(surface, client):
    """`finished` is derived from the watermark, and the read route clears it.

    Email's rule, and the reason the watermark is a column: a reply read on one
    device stops being unread everywhere on the next poll, because the flag was
    never in the browser to begin with.
    """

    csrf = sign_in(surface, client)
    row = seed(surface, age=timedelta(minutes=5))
    row.replied_at = datetime.now(UTC)

    state = next(item for item in listed(client) if item["id"] == row.id)["state"]
    assert state == "finished"

    read = client.post(f"/api/sessions/{row.id}/read", json={}, headers={"x-csrf-token": csrf})
    assert read.status_code == 200, read.text

    state = next(item for item in listed(client) if item["id"] == row.id)["state"]
    assert state == "idle"


def test_a_reply_older_than_the_last_look_is_not_unread(surface, client):
    """The watermark is a comparison, not a flag: a looked-at reply is read.

    Without this, a stored boolean could pass the previous test while the
    comparison was inverted or missing.
    """

    sign_in(surface, client)
    row = seed(surface, age=timedelta(minutes=5))
    row.replied_at = datetime.now(UTC) - timedelta(minutes=4)
    row.read_at = datetime.now(UTC) - timedelta(minutes=1)

    state = next(item for item in listed(client) if item["id"] == row.id)["state"]
    assert state == "idle"


def test_marking_read_is_scoped_to_the_owner(surface, client):
    """One account cannot clear another's badge, and gets no hint that it exists."""

    sign_in(surface, client)
    row = seed(surface, age=timedelta(minutes=5), account="alice")

    bob_token, bob_csrf = surface.login("bob")
    client.cookies.set(COOKIE, bob_token)
    refused = client.post(
        f"/api/sessions/{row.id}/read", json={}, headers={"x-csrf-token": bob_csrf}
    )
    assert refused.status_code == 404
    assert row.read_at is None


def test_a_turn_running_survives_a_window_of_zero(surface, client):
    """A window of 0 keeps nothing — except work that is happening now.

    "0 closes a session as soon as you look away" cannot mean a running turn
    disappears from the rail: the registry is the one member of the set that the
    account's preference has no say over.
    """

    from test_ws_surface import _Unfinished

    from neuman_server.composition.app import RunningTurn

    surface.accounts.rows["alice"].active_minutes = 0
    sign_in(surface, client)
    row = seed(surface, age=timedelta(days=1))
    surface.services.turns[row.branch_id] = RunningTurn(
        account="alice",
        account_id="acct-alice",
        session_id=row.id,
        inbox=surface.services.inbox_for(row.branch_id),
        task=_Unfinished(),
    )

    rows = listed(client)
    assert [item["id"] for item in rows] == [row.id], rows
    assert rows[0]["state"] == "running", rows
