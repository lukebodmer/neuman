"""The session surface: what an unauthenticated or hostile request may do.

These run the real Starlette app — real routes, real middleware, real
dependency plumbing — with only the outside world faked (see `surface.py`).
That matters because CSRF validation, Origin checking, cookie flags and session
scoping live in `composition/` and nowhere else: a test that stops at the
decision layer cannot see any of them.

The properties here are the ones `docs/security-model.md` assigns to the untrusted network
surface — "every byte validated; CSRF/Origin re-checked per unsafe request" —
and they are checked per request rather than established once at login, because
a session that is valid is not the same thing as a request that was intended.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fakes import FakeRuntime
from surface import CATALOG, ORIGIN, PASSWORD, build_surface

COOKIE = "neuman_session"


@pytest.fixture
def surface(monkeypatch, tmp_path):
    return build_surface(monkeypatch, tmp_path)


@pytest.fixture
def client(surface):
    with surface.client() as running:
        yield running


def authenticate(surface, client, name: str = "alice") -> str:
    """Put a valid session cookie on the client; return its CSRF token."""

    token, csrf = surface.login(name)
    client.cookies.set(COOKIE, token)
    return csrf


# -- authentication ---------------------------------------------------------


def test_unauthenticated_requests_are_refused(client):
    for method, path in (
        ("GET", "/api/me"),
        ("GET", "/api/sessions"),
        ("GET", "/api/active"),
        ("GET", "/api/policy"),
        ("GET", "/api/usage"),
        ("POST", "/api/sessions"),
    ):
        response = client.request(method, path)
        assert response.status_code in (401, 403), f"{method} {path}"


def test_a_forged_cookie_is_not_a_session(client):
    client.cookies.set(COOKIE, "not-a-real-token")
    assert client.get("/api/me").status_code == 401


def test_a_valid_session_resolves_to_its_own_principal(surface, client):
    authenticate(surface, client)
    body = client.get("/api/me").json()
    assert body["account"] == "alice"
    assert body["must_change_password"] is False


# -- CSRF -------------------------------------------------------------------


def test_unsafe_requests_need_a_csrf_token(surface, client):
    """The cookie alone must not be enough to act."""

    authenticate(surface, client)
    assert client.post("/api/sessions", json={}).status_code == 403


def test_a_wrong_csrf_token_is_refused(surface, client):
    authenticate(surface, client)
    response = client.post("/api/sessions", json={}, headers={"x-csrf-token": "wrong-token"})
    assert response.status_code == 403


def test_another_users_csrf_token_is_refused(surface, client):
    """The token is per session, so one user's cannot authorize another's request."""

    _bob_token, bob_csrf = surface.login("bob")
    authenticate(surface, client, "alice")
    response = client.post("/api/sessions", json={}, headers={"x-csrf-token": bob_csrf})
    assert response.status_code == 403


def test_the_matching_csrf_token_is_accepted(surface, client):
    csrf = authenticate(surface, client)
    response = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf})
    assert response.status_code == 201


def test_safe_methods_do_not_require_a_token(surface, client):
    authenticate(surface, client)
    assert client.get("/api/sessions").status_code == 200


# -- Origin -----------------------------------------------------------------


def test_a_cross_origin_unsafe_request_is_rejected(surface, client):
    """Stops a request that carries the cookie automatically from another site."""

    csrf = authenticate(surface, client)
    response = client.post(
        "/api/sessions",
        json={},
        headers={"x-csrf-token": csrf, "origin": "https://evil.test"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "origin_not_allowed"


def test_the_configured_origin_is_allowed(surface, client):
    csrf = authenticate(surface, client)
    response = client.post(
        "/api/sessions", json={}, headers={"x-csrf-token": csrf, "origin": ORIGIN}
    )
    assert response.status_code == 201


def test_a_cross_origin_read_is_not_blocked_here(surface, client):
    """Origin is checked on unsafe methods; reads are governed by the cookie.

    Pinned deliberately: it is the documented split, and a future change that
    starts rejecting safe cross-origin reads should have to edit this line.
    """

    authenticate(surface, client)
    response = client.get("/api/me", headers={"origin": "https://evil.test"})
    assert response.status_code == 200


# -- session scoping --------------------------------------------------------


def test_one_user_cannot_read_anothers_session(surface, client):
    """Ownership is a scope on the query, so a miss is a 404, not a 403."""

    bob = surface.agent_sessions.seed(account_id="acct-bob")
    authenticate(surface, client, "alice")
    assert client.get(f"/api/sessions/{bob.id}").status_code == 404


def _seed_image(surface, session_id: str, call_id: str = "call-1") -> bytes:
    """Store an `fs_image` result's bytes and log the event that names them."""

    raw = b"\x89PNG\r\n\x1a\n" + b"not really a png"
    stored = asyncio.run(
        surface.images.put(session_id=session_id, media_type="image/png", data=raw)
    )
    asyncio.run(
        surface.events.append(
            branch_id=session_id,
            kind="tool_result",
            payload={
                "tool_call_id": call_id,
                "name": "fs_image",
                "content": "Looked at the image (image/png). It follows.",
                "exit_code": 0,
                "image": {"sha256": stored.sha256, "path": "/srv/a.png"},
            },
        )
    )
    return raw


def test_the_image_of_a_tool_call_is_served_to_its_owner(surface, client):
    """The picture is fetched, not streamed with the frame or the projection.

    Both the WebSocket and the reopen projection say a picture exists and where
    to get it; the bytes come from here, so neither pays for a screenshot nobody
    is looking at.
    """

    row = surface.agent_sessions.seed(account_id="acct-alice")
    raw = _seed_image(surface, row.id)
    authenticate(surface, client, "alice")

    response = client.get(f"/api/branches/{row.id}/tools/call-1/image")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert response.content == raw
    assert "private" in response.headers["cache-control"]


def test_another_users_image_is_a_404(surface, client):
    """Scoped like the session it belongs to, so possession of an id is not access."""

    bob = surface.agent_sessions.seed(account_id="acct-bob")
    _seed_image(surface, bob.id)
    authenticate(surface, client, "alice")
    assert client.get(f"/api/branches/{bob.id}/tools/call-1/image").status_code == 404


def test_an_unknown_call_id_has_no_image(surface, client):
    row = surface.agent_sessions.seed(account_id="acct-alice")
    _seed_image(surface, row.id)
    authenticate(surface, client, "alice")
    assert client.get(f"/api/branches/{row.id}/tools/nope/image").status_code == 404


def test_one_user_cannot_close_anothers_session(surface, client):
    bob = surface.agent_sessions.seed(account_id="acct-bob")
    csrf = authenticate(surface, client, "alice")
    response = client.delete(f"/api/sessions/{bob.id}", headers={"x-csrf-token": csrf})
    assert response.status_code == 404


def test_listing_shows_only_your_own_sessions(surface, client):
    surface.agent_sessions.seed(account_id="acct-bob")
    mine = surface.agent_sessions.seed(account_id="acct-alice")
    authenticate(surface, client, "alice")

    listed = {row["id"] for row in client.get("/api/sessions").json()["sessions"]}
    assert listed == {mine.id}


# -- revocation -------------------------------------------------------------


def test_disabling_an_account_invalidates_a_live_session(surface, client):
    """The row is the identity now; disabling it is the revocation."""

    authenticate(surface, client)
    assert client.get("/api/me").status_code == 200

    surface.accounts.rows["alice"].disabled_at = datetime.now(UTC)
    assert client.get("/api/me").status_code == 401


def test_a_vanished_account_invalidates_a_live_session(surface, client):
    authenticate(surface, client)
    surface.accounts.rows.pop("alice", None)
    assert client.get("/api/me").status_code == 401


# -- response hygiene -------------------------------------------------------


def test_security_headers_are_present(client):
    headers = client.get("/healthz").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]


def test_every_response_carries_a_request_id(client):
    assert client.get("/healthz").headers["x-request-id"]


def test_a_supplied_request_id_is_echoed(client):
    """Correlation follows the caller's id when it provides one."""

    response = client.get("/healthz", headers={"x-request-id": "abc123def456"})
    assert response.headers["x-request-id"] == "abc123def456"


def test_health_reports_the_tool_count(client):
    body = client.get("/healthz").json()
    assert body["ok"] is True
    # Every manifest the registry loaded, diagnostics included: an operator
    # reads this to confirm the appliance came up with the tool set it expected,
    # which is a different question from what a user may choose between — so it
    # is deliberately *not* filtered the way `/api/tools` is — and `env_probe`
    # is in the count for the same reason: it is installed on the appliance,
    # which is what this figure reports, even though no role grants it.
    assert body["tools"] == 5


def test_health_fails_closed_when_the_database_is_gone(surface, client):
    """Health must report, never raise: a 503 is the signal to stop routing here."""

    surface.services.database.healthy = False
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["ok"] is False


# -- login ------------------------------------------------------------------


def test_login_sets_an_httponly_secure_cookie(client):
    response = client.post("/api/login", json={"username": "alice", "password": PASSWORD})
    assert response.status_code == 200
    assert response.json()["csrf_token"]

    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "samesite=strict" in cookie.lower()


def test_login_does_not_return_the_session_token_in_the_body(client):
    """The session token belongs in an HttpOnly cookie the page cannot read."""

    body = client.post("/api/login", json={"username": "alice", "password": PASSWORD}).json()
    assert "token" not in body


def test_a_bad_credential_is_a_401(client):
    response = client.post("/api/login", json={"username": "alice", "password": "wrong"})
    assert response.status_code == 401


def test_an_unknown_account_looks_exactly_like_a_bad_password(client):
    """Anything else is an account-enumeration oracle."""

    known = client.post("/api/login", json={"username": "alice", "password": "wrong"})
    unknown = client.post("/api/login", json={"username": "nobody", "password": "wrong"})
    assert known.status_code == unknown.status_code == 401
    assert known.json() == unknown.json()


def test_a_broken_hasher_is_a_500_not_a_401(surface, client):
    """A failure in the hasher is a bug, not a denied credential."""

    from fakes import FakePasswords

    class Broken(FakePasswords):
        async def verify(self, password_hash: str, password: str) -> bool:
            raise RuntimeError("argon2 exploded")

    surface.services.passwords = Broken()
    response = client.post("/api/login", json={"username": "alice", "password": PASSWORD})
    assert response.status_code == 500
    assert response.json()["error"] == "internal"


def test_repeated_failures_are_throttled(client):
    """Neuman must not forward a credential flood to the host's account database."""

    statuses = [
        client.post("/api/login", json={"username": "alice", "password": "wrong"}).status_code
        for _ in range(15)
    ]
    assert 429 in statuses, "no throttle engaged after 15 failures"


def test_a_throttled_response_carries_retry_after(client):
    for _ in range(15):
        response = client.post("/api/login", json={"username": "alice", "password": "wrong"})
        if response.status_code == 429:
            assert int(response.headers["retry-after"]) >= 1
            return
    pytest.fail("never throttled")


def test_a_malformed_login_body_is_a_400(client):
    for body in ({}, {"username": "alice"}, {"username": "", "password": "x"}):
        assert client.post("/api/login", json=body).status_code == 400


def test_an_oversized_username_is_refused_before_the_store(surface, client):
    """Bounded input: a name is not a place to spend a database round trip."""

    response = client.post("/api/login", json={"username": "a" * 200, "password": "x"})
    assert response.status_code == 400


def test_logout_needs_csrf_and_then_ends_the_session(surface, client):
    csrf = authenticate(surface, client)

    assert client.post("/api/logout").status_code == 403
    assert client.post("/api/logout", headers={"x-csrf-token": csrf}).status_code == 200
    assert client.get("/api/me").status_code == 401


# -- the admin surface ------------------------------------------------------


def admin_paths() -> list[tuple[str, str]]:
    """Every admin route, read from the router rather than listed by hand.

    Listing them would mean a new route is protected only if someone remembers
    to add it here — which is the same failure the guard exists to prevent
    (N27).
    """

    from neuman_server.composition.adminapi import admin_routes

    return [
        (method, route.path)
        for route in admin_routes()
        for method in sorted(route.methods or {"GET"})
        if method != "HEAD" and route.path != "/admin/bootstrap"
    ]


def test_every_admin_route_refuses_without_peer_credentials(client):
    """The whole surface, not a sample.

    `GET /admin/audit` returned the entire governance record — every actor,
    action and payload — to an unauthenticated caller, because the read routes
    called operations that take no actor and check nothing. The mutating ones
    were fine, which is why it survived: the pattern looked right wherever
    anyone had looked.

    A TCP connection has no peer credentials, so every one of these must
    refuse. Derived from the router so a route added tomorrow is covered
    today.
    """

    paths = admin_paths()
    assert paths, "no admin routes found; this test would pass vacuously"

    for method, path in paths:
        # Path parameters need *a* value; which one does not matter, because
        # nothing should get far enough to use it.
        concrete = path.replace("{name}", "alice")
        response = client.request(method, concrete)
        assert response.status_code in (401, 403), (
            f"{method} {concrete} answered {response.status_code} without peer "
            "credentials; every admin route must refuse"
        )


def test_the_admin_guard_does_not_touch_the_browser_surface(surface, client):
    """It guards `/admin`, and nothing else.

    A guard that refused too much would break the session surface in a way the
    other tests would catch; one that refused too little is what N27 was. This
    pins the boundary from the permissive side.
    """

    authenticate(surface, client)
    assert client.get("/api/me").status_code == 200
    assert client.get("/healthz").status_code == 200


def as_peer(monkeypatch, client, uid: int, name: str):
    """Make requests look like they arrived over the admin socket as `uid`.

    Two things have to be faked, because production gets them from two places
    the test process does not have:

    * the uid in the scope, which a protocol subclass puts there from
      `SO_PEERCRED` (`adminapi/peercred.py`); TestClient has no connection.
    * the uid→name lookup, which goes through `pwd` and therefore NSS. That is
      deliberate and not something the fake directory can replace: the kernel
      reports a number, and the host is the authority on what account it is
      (D16). So the test patches `pwd`, the same boundary production uses.
    """

    from neuman_server.composition.adminapi import deps
    from neuman_server.composition.adminapi.peercred import PEER_UID

    class Entry:
        pw_name = name

    def fake_getpwuid(value):
        if value != uid:
            raise KeyError(value)
        return Entry()

    monkeypatch.setattr(deps.pwd, "getpwuid", fake_getpwuid)

    app = client._transport.app

    async def wrapped(scope, receive, send):
        if scope.get("type") == "http":
            scope[PEER_UID] = uid
        await app(scope, receive, send)

    client._transport.app = wrapped
    return client


def test_an_identified_peer_without_operator_authority_is_refused(monkeypatch, surface, client):
    """Reachability is not authority, and the guard must check both.

    N27 put a peer check on this prefix and stopped there. Every admin *read*
    route still called an operation that takes no actor, so an account that
    could reach the socket — in the `neuman` group, but in no operator group —
    got the appliance's status, the audit chain, and any account's policy.
    Found in `vm-appliance` by giving an account exactly that combination
    (N32).

    alice is in `eng` and no operator group, so every admin route must refuse
    her with 403 rather than 401: she is identified, and not permitted.
    """

    as_peer(monkeypatch, client, 1001, "alice")

    paths = admin_paths()
    assert paths, "no admin routes found; this test would pass vacuously"

    for method, path in paths:
        concrete = path.replace("{name}", "alice").replace("{account}", "alice")
        response = client.request(method, concrete)
        assert response.status_code == 403, (
            f"{method} {concrete} answered {response.status_code} for a peer with "
            "no operator authority; every admin route must refuse"
        )


def test_an_operator_gets_past_the_guard(monkeypatch, surface, client):
    """The positive case, so the test above cannot pass by refusing everything.

    Without this, a guard that returned 403 unconditionally would satisfy the
    refusal test perfectly — which is the same shape as the bug it checks for.
    """

    surface.accounts.groups["eng"].add("alice")
    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    response = client.get("/admin/status")
    assert response.status_code == 200, response.text


def test_the_overview_reports_identity_rather_than_inventory(monkeypatch, surface, client):
    """The card's fields, asserted as an exact set (D69).

    Exact rather than a subset: this endpoint has no consumers that would be
    broken by a *new* field, so an addition here is a decision about what an
    operator is shown at a glance, and a test that only checked for known keys
    would let the card fill up with numbers nobody meant to put on it.

    The schema is a pair rather than one number so the client can compare them:
    a single value needs the reader to already know what it should be.
    """

    from neuman_server.foundation.db import latest_version

    surface.accounts.groups["eng"].add("alice")
    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    response = client.get("/admin/status")
    assert response.status_code == 200, response.text
    payload = response.json()

    assert set(payload) == {
        "build",
        "started_at",
        "uptime_s",
        "bind",
        "port",
        "tls",
        "schema",
        "session_queues",
    }, sorted(payload)

    assert set(payload["schema"]) == {"applied", "expected"}, payload["schema"]
    assert payload["schema"]["expected"] == latest_version(), (
        "the card's expected version is not the migrations on disk, so a reader "
        "would be told to expect something the build does not carry"
    )
    # The fake reports every bundled migration as applied; a database that
    # disagreed would be the mismatch branch, which is the client's to draw.
    assert payload["schema"]["applied"] == payload["schema"]["expected"], payload["schema"]

    # Under `TestClient` the lifespan has run, so this is a measurement rather
    # than the `None` a hand-built `Services` carries.
    assert isinstance(payload["uptime_s"], float), payload["uptime_s"]
    assert payload["uptime_s"] >= 0, payload["uptime_s"]
    assert payload["started_at"], "uptime with no start time is unreadable as a date"
    assert datetime.fromisoformat(payload["started_at"]).tzinfo is not None, (
        "a start time with no zone is ambiguous in every UI that formats it"
    )


def test_usage_lists_every_account_rather_than_waiting_to_be_asked(
    monkeypatch, surface, client
):
    """The Usage tab's one read: a roster, not a lookup.

    The tab used to be an account box in front of `/admin/usage/{account}`, so
    it drew nothing until a name was typed and an account that spent nothing
    could not appear at all. Who is spending today is a question about the
    whole appliance, and an account at zero is part of the answer.
    """

    today = datetime.now(UTC).date()
    seen = datetime.now(UTC)
    # Older than the trend window, so the row must still say when rather than
    # reading as "never" because the fortnight it drew is empty.
    older = datetime.now(UTC) - timedelta(days=40)
    surface.usage.everyone = [("alice", 173232041, 131072), ("bob", 0, 0)]
    surface.usage.records = [("alice", today, 173232041, 131072)]
    surface.usage.last_call = {"alice": seen, "bob": older}
    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    response = client.get("/admin/usage")
    assert response.status_code == 200, response.text
    payload = response.json()

    assert set(payload) == {"day", "days", "accounts"}, sorted(payload)
    assert payload["days"] == 14, "the default window is the one the card draws"
    # The day travels with the figures. "Today" on a screenshot is unreadable
    # once the reader is in another zone or reads it tomorrow.
    assert payload["day"] == today.isoformat()

    alice, bob = payload["accounts"]
    # Each row's keys are an exact set, like the overview's: this endpoint has
    # one consumer, so a new field changes what an operator is shown and is a
    # decision rather than a detail.
    assert set(alice) == {
        "name",
        "settled",
        "reserved",
        "tokens_daily",
        "last_call",
        "history",
    }, sorted(alice)
    assert alice["settled"] == 173232041
    assert alice["reserved"] == 131072
    assert alice["tokens_daily"] is None
    assert alice["last_call"] == seen.isoformat()

    # The trend is zero-filled between stored days: a day an account spent
    # nothing on has no row, and a gap in a chart is not the same as a day off.
    assert len(alice["history"]) == 14
    assert alice["history"][0] == {
        "day": (today - timedelta(days=13)).isoformat(),
        "settled": 0,
        "reserved": 0,
    }, alice["history"][0]
    assert alice["history"][-1] == {
        "day": today.isoformat(),
        "settled": 173232041,
        "reserved": 131072,
    }

    assert bob["settled"] == 0 and bob["reserved"] == 0
    assert bob["last_call"] == older.isoformat(), (
        "an account whose last call is outside the window is not 'never'"
    )
    assert all(day["settled"] == 0 for day in bob["history"]), bob["history"]


def test_usage_carries_the_cap_each_account_is_measured_against(monkeypatch, tmp_path):
    """The denominator is composed policy, not a usage column.

    The cap changes with the baseline and with an account's roles, so reading it
    from the usage row would show a total against a limit that is no longer in
    force — the same reason `/api/usage` composes it per request.
    """

    baseline = {
        "schema_version": 2,
        "defaults": {
            "tools": ["fs_read"],
            "files_read": [],
            "files_write": [],
            "auto_accept": "safe",
        },
        "roles": {"capped": {"tokens_daily": 200000}},
        "users": [{"name": "alice", "roles": ["capped"]}, {"name": "bob"}],
        **CATALOG,
    }
    surface = build_surface(monkeypatch, tmp_path, baseline=baseline)
    surface.usage.everyone = [("alice", 199999, 0), ("bob", 0, 0)]
    surface.accounts.groups["neuman-admin"].add("alice")

    with surface.client() as client:
        as_peer(monkeypatch, client, 1001, "alice")
        payload = client.get("/admin/usage").json()

    assert [row["tokens_daily"] for row in payload["accounts"]] == [200000, None], payload


def test_usage_puts_the_account_nearest_its_cap_first(monkeypatch, tmp_path):
    """Sorted by what needs attention, not by raw tokens.

    A capped account at 90% will stop working today; an uncapped one that has
    spent ten times as much has no line to cross. The trend travels with the
    row so the share can be read against the days that produced it, and a
    window the caller asks for is the window it gets.
    """

    baseline = {
        "schema_version": 2,
        "defaults": {
            "tools": ["fs_read"],
            "files_read": [],
            "files_write": [],
            "auto_accept": "safe",
        },
        "roles": {"capped": {"tokens_daily": 1000}},
        "users": [
            {"name": "alice", "roles": ["capped"]},
            {"name": "bob", "roles": ["capped"]},
            {"name": "carol"},
        ],
        **CATALOG,
    }
    surface = build_surface(monkeypatch, tmp_path, baseline=baseline)
    today = datetime.now(UTC).date()
    now = datetime.now(UTC)
    surface.usage.everyone = [("carol", 5000, 0), ("bob", 100, 0), ("alice", 900, 0)]
    surface.usage.records = [
        ("alice", today, 900, 0),
        ("alice", today - timedelta(days=1), 400, 0),
        ("bob", today, 100, 0),
        ("carol", today, 5000, 0),
    ]
    surface.usage.last_call = {
        "alice": now,
        "bob": now - timedelta(hours=1),
        "carol": now,
    }
    surface.accounts.groups["neuman-admin"].add("alice")

    with surface.client() as client:
        as_peer(monkeypatch, client, 1001, "alice")
        payload = client.get("/admin/usage?days=3").json()
        refused = client.get("/admin/usage?days=a-fortnight")

    assert [row["name"] for row in payload["accounts"]] == ["alice", "bob", "carol"], payload
    assert payload["days"] == 3
    alice = payload["accounts"][0]
    assert [row["settled"] for row in alice["history"]] == [0, 400, 900], alice["history"]
    assert refused.status_code == 400, refused.text


# -- the session list, as the client reads it --------------------------------
#
# `/api/sessions` and `/api/sessions/{id}` existed since M7 with nothing calling
# them. Now the client does, so the *shape* is load-bearing: a field renamed here
# is a panel that renders blank rows, and a client cannot assert on its own
# server.


def test_the_session_list_carries_what_a_list_needs(surface, client):
    """Everything a row is drawn from, and nothing the client must infer.

    `last_activity` is the date the list is ordered and grouped by, and
    `pinned_at` is why a row is at the top; both are the server's, so a client
    cannot disagree with the order it was given.
    """

    csrf = authenticate(surface, client)
    client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf})

    rows = client.get("/api/sessions").json()["sessions"]
    assert rows, "no sessions listed after creating one"
    needed = {
        "id",
        "title",
        "created_at",
        "closed_at",
        "last_activity",
        "pinned_at",
        "branch_count",
        "open_branch_id",
    }
    assert set(rows[0]) >= needed, rows[0]


def test_a_fresh_session_has_no_title_rather_than_a_placeholder(surface, client):
    """The client renders "untitled" for this, so it must be null and not "".

    An empty string and a null read the same in JSON and differently in
    JavaScript, and the panel branches on truthiness.
    """

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()
    assert created["title"] is None, created


def test_resuming_a_session_returns_the_projected_transcript(surface, client):
    """What the model saw, in the roles the client knows how to render.

    The panel replays `user`, `assistant` (with `tool_calls`), and `tool`. A
    projection that stopped preserving `tool_calls` would silently turn every
    past tool call into a floating result with no card above it.
    """

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    body = client.get(f"/api/sessions/{created['id']}").json()
    assert "messages" in body, body
    assert body["messages"] == [], "a fresh session should project to nothing"


def test_a_completed_turn_can_be_forked_into_a_sibling_branch(surface, client):
    """A fork adds a branch to the session, not a row to the rail (D83).

    The copied prefix is conversation rather than history: the child can read
    what was said and continue, and inherits none of the source's proposals or
    approvals. The session is one resource throughout.
    """

    row = surface.agent_sessions.seed(account_id="acct-alice")
    branch = row.branch_id
    for kind, payload in (
        ("user_message", {"content": "read the config"}),
        ("response_final", {"message": {"role": "assistant", "content": "done"}}),
        ("harness_event", {"type": "turn_complete", "reason": "stop"}),
    ):
        asyncio.run(surface.events.append(branch_id=branch, kind=kind, payload=payload))
    csrf = authenticate(surface, client, "alice")

    response = client.post(
        f"/api/branches/{branch}/fork",
        json={},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 201, response.text
    created = response.json()["branch"]
    assert created["id"] != branch
    assert created["forked_from"] == branch
    assert created["inherited_event_count"] == 4  # marker + three copied rows

    reopened = client.get(f"/api/sessions/{row.id}").json()
    assert reopened["branch_count"] == 2
    assert len(reopened["branches"]) == 2
    assert len(reopened["messages"]) == 2  # the user turn and the reply


def test_the_projection_keeps_tool_calls_paired_with_their_results():
    """The pairing the transcript renders, asserted on the projection directly.

    `tool_call_id` on a `tool` message must match an `id` in some earlier
    assistant `tool_calls`, or the client has a result it cannot attach.
    """

    from neuman_server.decision.harness import project

    class Event:
        def __init__(self, kind, payload):
            self.kind, self.payload = kind, payload

    messages = project(
        [
            Event("user_message", {"content": "read it"}),
            Event(
                "response_final",
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "fs_read", "arguments": "{}"},
                            }
                        ],
                    }
                },
            ),
            Event("tool_result", {"tool_call_id": "call_1", "content": "contents"}),
        ]
    )

    calls = [c["id"] for m in messages for c in m.get("tool_calls", [])]
    results = [m["tool_call_id"] for m in messages if m["role"] == "tool"]
    assert calls == ["call_1"], messages
    assert results == ["call_1"], messages


# -- session titles ----------------------------------------------------------
#
# `set_title` existed from M7 and had no callers, so every session listed as
# "untitled" and the list was useless for finding one. Third instance of that
# shape — a method written, never called, feature silently absent (see N43's
# `/api/tools` and N48's `provider_model`).


def test_a_title_is_derived_from_the_opening_message():
    """A truncation of what was said, not a guess at what was meant.

    Asking the model for a title costs a request per session, fails when the
    gateway is down, and on a local model pays a cold VRAM load before the first
    real answer.
    """

    from neuman_server.composition.http.ws import _derive_title

    assert _derive_title("read the config file") == "read the config file"


def test_a_long_title_breaks_on_a_word():
    """A label cut mid-word reads as corrupted rather than shortened."""

    from neuman_server.composition.http.ws import _derive_title

    title = _derive_title(
        "Explain how the capability gate decides what a dispatch may read, "
        "in as much detail as you can manage"
    )
    assert len(title) <= 61, title  # 60 plus the ellipsis
    assert title.endswith("…"), title
    assert not title[:-1].endswith(" "), title
    # The break is on a boundary, so the last word is whole.
    assert title[:-1].split()[-1] in ("dispatch", "may", "read")


def test_a_title_with_no_spaces_is_cut_anyway():
    """A pasted token has no word boundary, and a title is still required."""

    from neuman_server.composition.http.ws import _derive_title

    title = _derive_title("x" * 200)
    assert len(title) == 61, title


def test_whitespace_is_collapsed_so_a_title_has_no_newlines():
    """A pasted multi-line prompt must not become a multi-line list row."""

    from neuman_server.composition.http.ws import _derive_title

    assert _derive_title("read  the\n\n  file  please") == "read the file please"


def test_an_empty_message_still_produces_a_title():
    from neuman_server.composition.http.ws import _derive_title

    assert _derive_title("   ") == "untitled"


async def test_a_derived_title_never_overwrites_a_deliberate_one(surface):
    """The bug this guards is invisible until someone renames a session.

    A plain UPDATE would clobber the rename on the next message, and the symptom
    — "my rename keeps vanishing" — points nowhere near the code that did it.
    """

    store = surface.agent_sessions
    row = store.seed(account_id="acct-alice")

    assert await store.title_if_unnamed(row.id, "acct-alice", "derived") is True
    assert row.title == "derived"

    await store.set_title(row.id, "acct-alice", "chosen by hand")
    assert await store.title_if_unnamed(row.id, "acct-alice", "derived again") is False
    assert row.title == "chosen by hand"


async def test_titling_a_session_you_do_not_own_does_nothing(surface):
    """Ownership is a scope on the query, the same as every other session route."""

    store = surface.agent_sessions
    row = store.seed(account_id="acct-bob")

    assert await store.title_if_unnamed(row.id, "acct-alice", "mine now") is False
    assert row.title is None


# -- renaming ----------------------------------------------------------------


def test_a_session_can_be_renamed(surface, client):
    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    response = client.patch(
        f"/api/sessions/{created['id']}",
        json={"title": "the auth refactor"},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "the auth refactor"

    listed = client.get("/api/sessions").json()["sessions"]
    assert [row["title"] for row in listed if row["id"] == created["id"]] == [
        "the auth refactor"
    ]


def test_renaming_needs_a_csrf_token(surface, client):
    """A rename is an unsafe request, so the cookie alone must not carry it."""

    authenticate(surface, client)
    row = surface.agent_sessions.seed(account_id="acct-alice")

    assert client.patch(f"/api/sessions/{row.id}", json={"title": "x"}).status_code == 403


def test_one_user_cannot_rename_anothers_session(surface, client):
    """Ownership is a scope on the query, so a miss is a 404 rather than a 403.

    Answering 403 would confirm the session exists, which is an enumeration
    oracle for session ids.
    """

    bob = surface.agent_sessions.seed(account_id="acct-bob")
    csrf = authenticate(surface, client, "alice")

    response = client.patch(
        f"/api/sessions/{bob.id}", json={"title": "mine now"}, headers={"x-csrf-token": csrf}
    )
    assert response.status_code == 404, response.text
    assert bob.title is None


def test_a_renamed_title_is_collapsed_and_bounded(surface, client):
    """The client cannot be trusted to have done it — this is the untrusted
    surface (D1), and a newline in a title is a list row with a newline in it."""

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    response = client.patch(
        f"/api/sessions/{created['id']}",
        json={"title": "  the\n\n  auth   refactor  "},
        headers={"x-csrf-token": csrf},
    )
    assert response.json()["title"] == "the auth refactor"

    long = client.patch(
        f"/api/sessions/{created['id']}",
        json={"title": "y" * 500},
        headers={"x-csrf-token": csrf},
    )
    assert len(long.json()["title"]) == 200, len(long.json()["title"])


def test_an_empty_rename_is_refused(surface, client):
    """Whitespace is not a name, and a blank row is worse than "untitled"."""

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    for title in ("", "   ", "\n\t"):
        response = client.patch(
            f"/api/sessions/{created['id']}",
            json={"title": title},
            headers={"x-csrf-token": csrf},
        )
        assert response.status_code == 400, (title, response.text)


async def test_a_rename_survives_the_next_message(surface, client):
    """The asymmetry the two store methods exist for.

    A deliberate name overwrites; a derived one never does. Without the
    `title IS NULL` condition the next message would silently undo this, and the
    symptom — "my rename keeps vanishing" — points nowhere near the cause.
    """

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()
    client.patch(
        f"/api/sessions/{created['id']}",
        json={"title": "chosen by hand"},
        headers={"x-csrf-token": csrf},
    )

    # Exactly what the composition layer does on every first message.
    store = surface.agent_sessions
    assert await store.title_if_unnamed(created["id"], "acct-alice", "derived") is False
    assert store.rows[created["id"]].title == "chosen by hand"


# -- removing ----------------------------------------------------------------


def test_a_removed_session_leaves_the_listing(surface, client):
    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    response = client.post(
        f"/api/sessions/{created['id']}/remove", headers={"x-csrf-token": csrf}
    )
    assert response.status_code == 200, response.text

    listed = {row["id"] for row in client.get("/api/sessions").json()["sessions"]}
    assert created["id"] not in listed


def test_removing_does_not_close_the_conversation(surface, client):
    """The distinction the column exists for.

    `closed_at` is what makes a row eligible for scheduled deletion. If Remove
    set it, tidying a sidebar would quietly arm retention on a transcript the
    audit chain refers to — so removal must leave it alone.
    """

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    client.post(f"/api/sessions/{created['id']}/remove", headers={"x-csrf-token": csrf})

    row = surface.agent_sessions.rows[created["id"]]
    assert row.removed_at is not None
    assert row.closed_at is None


def test_a_removed_session_survives_for_the_operator(surface, client):
    """Removal is not a delete: the row and its transcript stay on the appliance."""

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    client.post(f"/api/sessions/{created['id']}/remove", headers={"x-csrf-token": csrf})

    assert created["id"] in surface.agent_sessions.rows


def test_a_removed_session_cannot_be_reopened_by_id(surface, client):
    """A stale tab must not resurrect a session removed on another device.

    Removal is a fact on the server, so it has to hold for every browser holding
    the id — otherwise the removal reads as having failed.
    """

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    client.post(f"/api/sessions/{created['id']}/remove", headers={"x-csrf-token": csrf})

    assert client.get(f"/api/sessions/{created['id']}").status_code == 404


def test_removing_a_session_twice_is_not_an_error(surface, client):
    """A repeated click asks for a state the server is already in.

    The second call writes nothing — the timestamp records when the user
    decided, and a retry must not move it — but it is not a failure either.
    """

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    first = client.post(f"/api/sessions/{created['id']}/remove", headers={"x-csrf-token": csrf})
    when = surface.agent_sessions.rows[created["id"]].removed_at

    second = client.post(
        f"/api/sessions/{created['id']}/remove", headers={"x-csrf-token": csrf}
    )
    assert (first.status_code, second.status_code) == (200, 200), second.text
    assert surface.agent_sessions.rows[created["id"]].removed_at == when


def test_removing_needs_a_csrf_token(surface, client):
    """Removal is unsafe and one-way, so the cookie alone must not carry it."""

    authenticate(surface, client)
    row = surface.agent_sessions.seed(account_id="acct-alice")

    assert client.post(f"/api/sessions/{row.id}/remove").status_code == 403
    assert row.removed_at is None


def test_one_user_cannot_remove_anothers_session(surface, client):
    """Ownership is a scope on the query, so a miss is a 404 rather than a 403."""

    bob = surface.agent_sessions.seed(account_id="acct-bob")
    csrf = authenticate(surface, client, "alice")

    response = client.post(f"/api/sessions/{bob.id}/remove", headers={"x-csrf-token": csrf})
    assert response.status_code == 404, response.text
    assert bob.removed_at is None


def test_removing_frees_a_slot_in_the_listing(surface, client):
    """Why the filter is in the query rather than in the client.

    The old version hid rows in the browser *after* the limit had been applied,
    so removing a session shortened the list instead of revealing the one behind
    the cap. Filtering before the limit is the whole fix.
    """

    store = surface.agent_sessions
    for _ in range(51):
        store.seed(account_id="acct-alice")
    csrf = authenticate(surface, client)

    listed = client.get("/api/sessions").json()["sessions"]
    assert len(listed) == 50
    # The one the cap is keeping out of view.
    beyond = (set(store.rows) - {row["id"] for row in listed}).pop()

    client.post(f"/api/sessions/{listed[0]['id']}/remove", headers={"x-csrf-token": csrf})

    after = {row["id"] for row in client.get("/api/sessions").json()["sessions"]}
    assert len(after) == 50
    assert listed[0]["id"] not in after
    assert beyond in after


# -- pinning, and the order of the list ---------------------------------------
#
# Pinning and sorting are both statements about the *listing*, and both are the
# server's because the listing is capped. A pin applied in the browser would put
# a row at the top of whichever fifty the server happened to return, and sorting
# fifty rows chosen by creation is not the fifty most recently used.


def test_a_pinned_session_is_listed_first(surface, client):
    csrf = authenticate(surface, client)
    older = surface.agent_sessions.seed(account_id="acct-alice")
    older.created_at = datetime.now(UTC) - timedelta(days=3)
    newer = surface.agent_sessions.seed(account_id="acct-alice")

    response = client.patch(
        f"/api/sessions/{older.id}", json={"pinned": True}, headers={"x-csrf-token": csrf}
    )
    assert response.status_code == 200, response.text
    assert response.json()["pinned_at"], "a pin was not reported back"

    listed = [row["id"] for row in client.get("/api/sessions").json()["sessions"]]
    assert listed == [older.id, newer.id]


def test_unpinning_lets_a_session_fall_back_into_the_order(surface, client):
    """A pin is a value, not a one-way act — which is why it is a PATCH field.

    Removal has no second value and its own route; a pin does, and
    `{pinned: false}` is a statement no "unpin" route could make differently.
    """

    csrf = authenticate(surface, client)
    older = surface.agent_sessions.seed(account_id="acct-alice")
    older.created_at = datetime.now(UTC) - timedelta(days=3)
    newer = surface.agent_sessions.seed(account_id="acct-alice")
    client.patch(
        f"/api/sessions/{older.id}", json={"pinned": True}, headers={"x-csrf-token": csrf}
    )

    response = client.patch(
        f"/api/sessions/{older.id}", json={"pinned": False}, headers={"x-csrf-token": csrf}
    )
    assert response.status_code == 200, response.text
    assert response.json()["pinned_at"] is None

    listed = [row["id"] for row in client.get("/api/sessions").json()["sessions"]]
    assert listed == [newer.id, older.id]


def test_pinning_twice_keeps_the_first_moment(surface, client):
    """A repeated click asks for a state the server is already in.

    The timestamp records when the user decided, so a retry must not move it —
    the same guard `removed_at` has, for the same reason.
    """

    csrf = authenticate(surface, client)
    row = surface.agent_sessions.seed(account_id="acct-alice")

    client.patch(
        f"/api/sessions/{row.id}", json={"pinned": True}, headers={"x-csrf-token": csrf}
    )
    when = row.pinned_at
    response = client.patch(
        f"/api/sessions/{row.id}", json={"pinned": True}, headers={"x-csrf-token": csrf}
    )

    assert response.status_code == 200, response.text
    assert row.pinned_at == when


def test_the_list_can_be_ordered_by_last_activity_or_by_creation(surface, client):
    """The two dates, and the default.

    `used` is the default because the session you want is almost always the one
    you were last working in. `last_activity` is the newest of the session's
    creation, its newest event and its closing, so a session begun long ago and
    used a minute ago sorts above one created since.
    """

    store = surface.agent_sessions
    old_but_used = store.seed(account_id="acct-alice")
    old_but_used.created_at = datetime.now(UTC) - timedelta(days=5)
    old_but_used.last_activity = datetime.now(UTC) - timedelta(minutes=5)
    new_but_idle = store.seed(account_id="acct-alice")
    new_but_idle.created_at = datetime.now(UTC) - timedelta(minutes=30)
    new_but_idle.last_activity = datetime.now(UTC) - timedelta(days=2)
    authenticate(surface, client)

    used = client.get("/api/sessions?sort=used").json()["sessions"]
    assert [row["id"] for row in used] == [old_but_used.id, new_but_idle.id]
    # The date the client draws on the row and groups it under, so the order and
    # the heading can never disagree.
    assert used[0]["last_activity"] == old_but_used.last_activity.isoformat()

    created = client.get("/api/sessions?sort=created").json()["sessions"]
    assert [row["id"] for row in created] == [new_but_idle.id, old_but_used.id]

    # No parameter is the default sort, not a third order.
    default = client.get("/api/sessions").json()["sessions"]
    assert [row["id"] for row in default] == [old_but_used.id, new_but_idle.id]


def test_pinned_rows_lead_both_orders(surface, client):
    """A pin outranks the date, whichever date the list is sorted by.

    The alternative was to let a pinned session sink as it aged, which is not
    what pinning is for.
    """

    store = surface.agent_sessions
    pinned = store.seed(account_id="acct-alice")
    pinned.created_at = datetime.now(UTC) - timedelta(days=9)
    pinned.last_activity = datetime.now(UTC) - timedelta(days=9)
    store.seed(account_id="acct-alice")
    csrf = authenticate(surface, client)
    client.patch(
        f"/api/sessions/{pinned.id}", json={"pinned": True}, headers={"x-csrf-token": csrf}
    )

    for sort in ("used", "created"):
        listed = client.get(f"/api/sessions?sort={sort}").json()["sessions"]
        assert listed[0]["id"] == pinned.id, sort


def test_an_unknown_sort_is_refused(surface, client):
    """The value names an ORDER BY fragment, so it is a closed set.

    Falling back to the default would hide a typo in a caller behind a list that
    looks plausible but answers a different question.
    """

    authenticate(surface, client)
    assert client.get("/api/sessions?sort=sideways").status_code == 400


def test_pinning_a_session_needs_a_boolean(surface, client):
    csrf = authenticate(surface, client)
    row = surface.agent_sessions.seed(account_id="acct-alice")

    response = client.patch(
        f"/api/sessions/{row.id}", json={"pinned": "yes"}, headers={"x-csrf-token": csrf}
    )
    assert response.status_code == 400, response.text
    assert row.pinned_at is None


def test_pinning_needs_a_csrf_token(surface, client):
    """It writes, so the cookie alone must not carry it."""

    authenticate(surface, client)
    row = surface.agent_sessions.seed(account_id="acct-alice")

    assert client.patch(f"/api/sessions/{row.id}", json={"pinned": True}).status_code == 403
    assert row.pinned_at is None


def test_one_user_cannot_pin_anothers_session(surface, client):
    """Ownership is a scope on the query, so a miss is a 404 rather than a 403."""

    bob = surface.agent_sessions.seed(account_id="acct-bob")
    csrf = authenticate(surface, client, "alice")

    response = client.patch(
        f"/api/sessions/{bob.id}", json={"pinned": True}, headers={"x-csrf-token": csrf}
    )
    assert response.status_code == 404, response.text
    assert bob.pinned_at is None


# -- the client against the router -------------------------------------------
#
# The one thing about the UI that this file has to own, because it is the only
# test that needs both halves at once: the UI's source and the server's route
# table. Everything else about the UI reads its own files and lives in
# `neuman-client/tests/test_web.py`.


def test_every_route_the_browser_client_calls_exists():
    """A client is a second description of an API, and two descriptions drift.

    `neuman-admin` has had this check since M9 (`test_cli.py`), and the browser
    client has not — so a renamed route would be found by a user, in a panel that
    silently stopped working, rather than here. Extracting the calls from the
    source is crude and it is the only thing that cannot go stale.
    """

    import re
    from pathlib import Path

    from neuman_server.composition.http import http_routes

    # Named from this file, so nobody has to know where the UI went. Both of
    # the chat window's scripts call the API; the task view is the second.
    web = Path(__file__).resolve().parent.parent.parent / "neuman-client" / "web" / "src"
    scripts = [web / "neuman.js", web / "tasks.js"]
    assert all(script.exists() for script in scripts), scripts

    calls = {
        call
        for script in scripts
        for call in re.findall(
            r'api\(\s*"(GET|POST|PATCH|DELETE)",\s*[`"]([^`"]+)', script.read_text()
        )
    }
    assert calls, "no API calls found in the client; this test would pass vacuously"

    known = {
        (method, route.path)
        for route in http_routes()
        for method in (getattr(route, "methods", None) or set())
    }

    # The `/api` paths the browser calls that this server does not serve: the
    # running jobs live in the client daemon, so `/api/jobs` and its `/stop` are
    # answered there (`neuman_client/ui.py`, D98). The client's own suite proves
    # they exist and answer (`test_ui.py`); this test only has to know they are
    # not supposed to be here, and a second local route has to be added here
    # deliberately rather than silently skipped.
    client_owned = ("/api/jobs",)

    for method, path in sorted(calls):
        # Template literals carry a JS expression where the router has a
        # parameter: `/api/sessions/${id}` is `/api/sessions/{session_id}`.
        # A query string is not part of the route, so it is dropped before the
        # comparison — `/api/browse?path=${p}` is the `/api/browse` route.
        #
        # An interpolation stands for whatever the router calls that parameter,
        # and the client cannot know the name — `${name}` is the variable, not
        # the placeholder. So a route matches when its literal segments do and
        # each of its parameters sits where the client interpolates one.
        bare = path.split("?", 1)[0]
        if any(bare == prefix or bare.startswith(prefix + "/") for prefix in client_owned):
            continue
        segments = bare.split("/")

        def serves(route: str, segments=segments) -> bool:
            parts = route.split("/")
            if len(parts) != len(segments):
                return False
            return all(
                (part.startswith("{") and re.fullmatch(r"\$\{[^}]+\}", given)) or part == given
                for part, given in zip(parts, segments, strict=True)
            )

        assert any(m == method and serves(route) for m, route in known), (
            f"the client calls {method} {path}, which is not a route the server serves"
        )


# -- the working directory ----------------------------------------------------


def test_a_working_directory_inside_policy_is_accepted(surface, client):
    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    response = client.patch(
        f"/api/branches/{created['branch']['id']}",
        json={"working_dir": "/srv/eng/work"},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json()["working_dir"] == "/srv/eng/work"


def test_a_working_directory_outside_policy_is_refused_by_name(surface, client):
    """403 with `outside_policy`, not a bare 400.

    The user picked a real directory and the answer is that policy does not reach
    it — a different problem from a malformed request, and the only one whose fix
    is an operator changing configuration.

    This is also what keeps the feature from laundering reach: without the check,
    typing a path into a settings field would reach past the grant.
    """

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    response = client.patch(
        f"/api/branches/{created['branch']['id']}",
        json={"working_dir": "/etc"},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"] == "outside_policy"
    assert "/etc" in response.json()["detail"]


def test_a_working_directory_can_be_cleared(surface, client):
    """Null clears it, and null is not "outside policy"."""

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()
    client.patch(
        f"/api/branches/{created['branch']['id']}",
        json={"working_dir": "/srv/eng"},
        headers={"x-csrf-token": csrf},
    )

    response = client.patch(
        f"/api/branches/{created['branch']['id']}",
        json={"working_dir": None},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json()["working_dir"] is None


def test_a_patch_touches_only_the_fields_it_names(surface, client):
    """Which is what PATCH means, and what lets the client send one at a time."""

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    client.patch(
        f"/api/sessions/{created['id']}",
        json={"title": "kept"},
        headers={"x-csrf-token": csrf},
    )
    body = client.patch(
        f"/api/branches/{created['branch']['id']}",
        json={"working_dir": "/srv/eng"},
        headers={"x-csrf-token": csrf},
    ).json()

    assert body["working_dir"] == "/srv/eng", body
    assert body["attachments"] == [], body
    assert client.get(f"/api/sessions/{created['id']}").json()["title"] == "kept"


def test_an_empty_patch_is_refused(surface, client):
    """A PATCH naming no field is a request that means nothing."""

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    response = client.patch(
        f"/api/sessions/{created['id']}", json={}, headers={"x-csrf-token": csrf}
    )
    assert response.status_code == 400, response.text


def test_every_session_response_describes_the_working_directory(surface, client):
    """A route that omits it shows the client an empty field while the server
    holds a value — invisible until someone wonders why the agent is not looking
    where they said.

    The client-vs-router check cannot catch this: the path is right and the
    payload is wrong.
    """

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()
    assert "working_dir" in created["branch"], created

    reopened = client.get(f"/api/sessions/{created['id']}").json()
    assert "working_dir" in reopened["branch"]
    assert "working_dir" in reopened["branches"][0]
    assert "open_branch_id" in client.get("/api/sessions").json()["sessions"][0]


def test_a_working_directory_survives_a_resume(surface, client):
    """Set it, fetch the session, and get it back — which is the whole point."""

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()
    client.patch(
        f"/api/branches/{created['branch']['id']}",
        json={"working_dir": "/srv/eng/work"},
        headers={"x-csrf-token": csrf},
    )

    resumed = client.get(f"/api/sessions/{created['id']}").json()
    assert resumed["branch"]["working_dir"] == "/srv/eng/work", resumed


def test_the_policy_response_separates_granted_from_reachable(surface, client):
    """`readable` is what a chooser may offer; `files_read` is not.

    A grant naming the home is the token, which is not a path until the client
    reports its home — so the raw list contains an entry no dispatch can use,
    and a picker built from it would offer `$HOME` as a directory. `readable`
    is the expansion.
    """

    authenticate(surface, client)
    body = client.get("/api/policy").json()

    assert "readable" in body, body
    assert "writable" in body, body
    for path in body["readable"]:
        assert path.startswith("/"), path
        assert ".." not in path.split("/"), path


def _home_baseline(tmp_path, read):
    """A baseline whose only read grant is whatever `read` names.

    Its own surface per test rather than the shared fixture, whose baseline
    grants `/srv/eng`: against that policy nothing here expands and both the
    right and the wrong implementation look identical.
    """

    baseline = {
        "schema_version": 2,
        "defaults": {
            "tools": ["fs_read"],
            "files_read": read,
            "files_write": [],
            "auto_accept": "safe",
        },
        "roles": {},
        "users": [{"name": "alice"}],
        **CATALOG,
    }
    return baseline


def test_a_home_grant_is_offered_as_the_home(monkeypatch, tmp_path):
    """What the chooser offers for a home grant: the home itself (D105).

    The expanded list is what the PATCH checks against, so the two must agree:
    the home a `$HOME` entry names is offered, a directory inside it is
    accepted — dotfiles included, which is the plain meaning's cost — and a
    path outside it is refused.
    """

    baseline = _home_baseline(tmp_path, ["$HOME"])
    surface = build_surface(monkeypatch, tmp_path, baseline=baseline)
    surface.services.execution.home_value = "/home/alice"
    with surface.client() as client:
        csrf = authenticate(surface, client)
        body = client.get("/api/policy").json()

        assert body["policy"]["files_read"] == ["$HOME"]
        assert body["readable"] == ["/home/alice"], body["readable"]

        # And the offer is honest: what the menu shows, the PATCH accepts,
        # and any directory under it is a working directory like another.
        created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()
        for path in ("/home/alice", "/home/alice/Documents", "/home/alice/.ssh"):
            response = client.patch(
                f"/api/branches/{created['branch']['id']}",
                json={"working_dir": path},
                headers={"x-csrf-token": csrf},
            )
            assert response.status_code == 200, (path, response.text)

        outside = client.patch(
            f"/api/branches/{created['branch']['id']}",
            json={"working_dir": "/srv/eng"},
            headers={"x-csrf-token": csrf},
        )
        assert outside.status_code == 403, outside.text


def test_a_home_grant_expands_against_the_clients_home(monkeypatch, tmp_path):
    """N145: `$HOME` is the home of the machine that runs the tool.

    Any home the core supplied itself would be the bug: a client elsewhere has a
    different one, so the grant would name directories that do not exist where
    the dispatch runs, and the picker would offer them because the same wrong
    home is what the PATCH checks against.

    The policy here says `$HOME`, which is the spelling that does not name a
    machine. The same surface is asked twice: with nothing connected there is no
    home, so the grant reaches nothing rather than a guess; with a client
    connected it is that machine's.
    """

    baseline = _home_baseline(tmp_path, ["$HOME"])
    surface = build_surface(monkeypatch, tmp_path, baseline=baseline)

    with surface.client() as client:
        authenticate(surface, client)
        offline = client.get("/api/policy").json()["readable"]
        assert offline == [], offline

    surface.services.execution.home_value = "/home/remote"
    with surface.client() as client:
        csrf = authenticate(surface, client)
        body = client.get("/api/policy").json()
        readable = body["readable"]

        assert readable == ["/home/remote"], readable
        assert "/home/alice" not in readable, readable

        # And the offer is what the PATCH checks against, so a directory chosen
        # from the menu is accepted rather than refused one step later.
        created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()
        for path in readable:
            response = client.patch(
                f"/api/branches/{created['branch']['id']}",
                json={"working_dir": path},
                headers={"x-csrf-token": csrf},
            )
            assert response.status_code == 200, (path, response.text)


# -- browsing for the working-directory chooser ------------------------------


def test_browsing_needs_a_session(client):
    assert client.get("/api/browse?path=/srv/eng").status_code in (401, 403)


def test_browsing_a_granted_directory_dispatches_the_listing_tool(surface, client):
    """The route runs `fs_list` as the user, inside a profile it derives itself.

    Asserted on the recorded dispatch rather than the response body, because the
    response is whatever the fake returned — the part that enforces anything is
    the profile, and a route that dispatched with an empty one would look
    identical from outside.
    """

    from fakes import DispatchOutcome

    surface.services.execution.outcome = DispatchOutcome(
        exit_code=0, stdout="d\tneuman\nd\ttrading\nf\tnotes.md\n"
    )
    authenticate(surface, client)

    response = client.get("/api/browse?path=/srv/eng")
    assert response.status_code == 200, response.text
    # Parsed, in order, with the kind preserved. Note the file is still here:
    # filtering is `fs_list`'s job via `dirs_only`, asserted below on the
    # dispatch. A test that expected the route to drop it would have been
    # asserting against the fake, which ignores args and replays a fixed stdout.
    assert response.json()["entries"] == [
        {"kind": "d", "name": "neuman"},
        {"kind": "d", "name": "trading"},
        {"kind": "f", "name": "notes.md"},
    ], response.json()

    assert len(surface.services.execution.dispatches) == 1
    dispatch = surface.services.execution.dispatches[0]
    assert dispatch["tool"] == "fs_list"
    assert dispatch["user"] == "alice"
    assert dispatch["args"]["path"] == "/srv/eng"
    # A chooser picks directories, so the listing is asked to omit files rather
    # than the route filtering them after the fact — the tool is what walks the
    # directory.
    assert dispatch["args"]["dirs_only"] is True


def test_browsing_outside_policy_is_refused_without_dispatching(surface, client):
    """Named, and refused *before* the client is asked.

    The client reaches what the user reaches, so a path outside policy is a
    policy answer rather than an opaque failure, and it never becomes a
    dispatch at all.
    """

    authenticate(surface, client)

    response = client.get("/api/browse?path=/etc")
    assert response.status_code == 403, response.text
    assert response.json()["error"] == "outside_policy"
    assert surface.services.execution.dispatches == []


def test_browsing_cannot_escape_by_traversal(surface, client):
    authenticate(surface, client)

    for path in ("/srv/eng/../../etc", "/srv/engine", "relative/path", ""):
        response = client.get(f"/api/browse?path={path}")
        assert response.status_code in (400, 403), (path, response.status_code)
    assert surface.services.execution.dispatches == []


def test_the_caller_cannot_choose_which_tool_runs(surface, client):
    """`/api/browse` dispatches outside the approval flow, so the tool is fixed.

    A `tool` parameter here would be a way to run `shell` with no approval card —
    the one thing this route must not permit. Asserted by trying.
    """

    authenticate(surface, client)

    response = client.get("/api/browse?path=/srv/eng&tool=shell")
    assert response.status_code == 200, response.text
    assert [d["tool"] for d in surface.services.execution.dispatches] == ["fs_list"]


def test_a_failed_listing_is_reported_rather_than_shown_as_empty(surface, client):
    """An unreadable directory and an empty one must not look the same."""

    from fakes import DispatchOutcome

    surface.services.execution.outcome = DispatchOutcome(
        exit_code=1, stderr="fs_list: permission denied: /srv/eng/secret"
    )
    authenticate(surface, client)

    response = client.get("/api/browse?path=/srv/eng/secret")
    assert response.status_code == 422, response.text
    assert "permission denied" in response.json()["detail"]


def test_sentinel_lines_are_not_entries(surface, client):
    """`(empty)` and `(stopped at N entries)` carry no tab, and are not names."""

    from fakes import DispatchOutcome

    surface.services.execution.outcome = DispatchOutcome(exit_code=0, stdout="(empty)\n")
    authenticate(surface, client)
    assert client.get("/api/browse?path=/srv/eng").json()["entries"] == []

    surface.services.execution.outcome = DispatchOutcome(
        exit_code=0, stdout="d\tsrc\n(stopped at 500 entries)\n"
    )
    response = client.get("/api/browse?path=/srv/eng")
    assert response.json()["entries"] == [{"kind": "d", "name": "src"}]


# -- the working directory's development environment -------------------------


def test_probing_an_environment_needs_a_session(client):
    assert client.get("/api/environment?path=/srv/eng").status_code in (401, 403)


def test_probing_dispatches_the_probe_as_the_user(surface, client):
    """The route runs `env_probe` on the acting human's machine.

    A dispatch rather than a read here because the `.direnv` cache lives in the
    user's home, which is `0700`, and this process is `neuman`. Asserted on the
    recorded dispatch, not the body: the body is whatever the fake replayed, and
    the part that matters is which tool ran, as whom, with which surfaces.
    """

    from fakes import DispatchOutcome

    surface.services.execution.outcome = DispatchOutcome(
        exit_code=0,
        stdout='{"path": "/srv/eng", "reason": "ok", '
        '"profile": "/nix/store/abc-env", '
        '"interpreter": "/nix/store/abc-env/bin/python3"}',
    )
    authenticate(surface, client)

    response = client.get("/api/environment?path=/srv/eng")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "path": "/srv/eng",
        "reason": "ok",
        "profile": "/nix/store/abc-env",
        "interpreter": "/nix/store/abc-env/bin/python3",
    }, response.json()

    assert len(surface.services.execution.dispatches) == 1
    dispatch = surface.services.execution.dispatches[0]
    assert dispatch["tool"] == "env_probe"
    assert dispatch["user"] == "alice"
    assert dispatch["args"] == {"cwd": "/srv/eng"}
    # Nothing is written by a probe, and a surface that granted writes would be
    # a wider dispatch than the question needs.
    assert dispatch["surfaces"]["write"] == []
    assert dispatch["surfaces"]["read"], "a path-taking tool needs something to check against"


def test_probing_outside_policy_is_refused_without_dispatching(surface, client):
    """The same reach check the listing route makes, for the same reason."""

    authenticate(surface, client)

    response = client.get("/api/environment?path=/etc")
    assert response.status_code == 403, response.text
    assert response.json()["error"] == "outside_policy"
    assert surface.services.execution.dispatches == []


def test_probing_cannot_escape_by_traversal(surface, client):
    authenticate(surface, client)

    for path in ("/srv/eng/../../etc", "/srv/engine", "relative/path", ""):
        response = client.get(f"/api/environment?path={path}")
        assert response.status_code in (400, 403), (path, response.status_code)
    assert surface.services.execution.dispatches == []


def test_the_caller_cannot_choose_which_tool_probes(surface, client):
    """`/api/environment` dispatches outside the approval flow, so the tool is fixed.

    The same hole the listing route must not have: a `tool` parameter would be a
    way to run `shell` with no approval card.
    """

    from fakes import DispatchOutcome

    surface.services.execution.outcome = DispatchOutcome(
        exit_code=0, stdout='{"reason": "no_environment"}'
    )
    authenticate(surface, client)

    response = client.get("/api/environment?path=/srv/eng&tool=shell")
    assert response.status_code == 200, response.text
    assert [d["tool"] for d in surface.services.execution.dispatches] == ["env_probe"]


def test_an_unrecognised_reason_becomes_env_unreadable(surface, client):
    """The route translates the probe's answer rather than forwarding it.

    The probe runs on the user's machine, so its stdout crosses a trust boundary
    like any other tool output — and the browser switches on `reason` to pick a
    colour. A name nobody designed a state for arrives as "cannot say", which is
    the honest answer and one the UI already has to draw.
    """

    from fakes import DispatchOutcome

    for stdout in (
        '{"reason": "env_haunted"}',
        '{"reason": 7}',
        "not json at all",
        "[]",
        "",
    ):
        surface.services.execution.outcome = DispatchOutcome(exit_code=0, stdout=stdout)
        authenticate(surface, client)

        response = client.get("/api/environment?path=/srv/eng")
        assert response.status_code == 200, (stdout, response.text)
        assert response.json() == {"path": "/srv/eng", "reason": "env_unreadable"}, stdout


def test_a_resolved_probe_reports_only_the_profile_and_interpreter(surface, client):
    """The rc path stays on the user's machine.

    It is the cache's location inside the project and says nothing a user acts
    on, where the profile and the interpreter are what make "which environment
    is this" answerable in the tooltip. A route that forwarded the whole report
    would leak project layout into the browser for no gain.
    """

    from fakes import DispatchOutcome

    surface.services.execution.outcome = DispatchOutcome(
        exit_code=0,
        stdout='{"reason": "ok", "profile": "/nix/store/abc-env", '
        '"interpreter": "/nix/store/abc-env/bin/python3", '
        '"rc_path": "/srv/eng/.direnv/flake-profile-abc123.rc"}',
    )
    authenticate(surface, client)

    body = client.get("/api/environment?path=/srv/eng").json()
    assert "rc_path" not in body, body
    assert set(body) == {"path", "reason", "profile", "interpreter"}, body


def test_a_states_detail_is_not_forwarded(surface, client):
    """A refusal's prose is for the model, not for the status light.

    The resolver's `detail` names paths inside the project. The chooser maps the
    `reason` to a sentence it wrote itself, so forwarding the prose would be a
    second statement of the same fact — and the one that drifts.
    """

    from fakes import DispatchOutcome

    surface.services.execution.outcome = DispatchOutcome(
        exit_code=0,
        stdout='{"reason": "env_unrealised", "detail": "/srv/eng declares ..."}',
    )
    authenticate(surface, client)

    body = client.get("/api/environment?path=/srv/eng").json()
    assert body == {"path": "/srv/eng", "reason": "env_unrealised"}, body


def test_a_failed_probe_dispatch_is_reported(surface, client):
    """Every environment *state* exits zero, so a non-ok outcome is the dispatch.

    A timeout, a cancellation, no client connected. Distinguished from "this
    directory has no environment" because they are different answers: one is a
    fact about the directory and the other is a fact about the appliance.
    """

    from fakes import DispatchOutcome

    surface.services.execution.outcome = DispatchOutcome(
        exit_code=1, stderr="env_probe: cannot read /srv/eng"
    )
    authenticate(surface, client)

    response = client.get("/api/environment?path=/srv/eng")
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "probe_failed"


# -- attachments --------------------------------------------------------------


def test_a_file_inside_policy_can_be_attached(surface, client):
    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()
    assert created["branch"]["attachments"] == []

    response = client.patch(
        f"/api/branches/{created['branch']['id']}",
        json={"attachments": ["/srv/eng/spec.pdf", "/srv/eng/notes.md"]},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json()["attachments"] == ["/srv/eng/spec.pdf", "/srv/eng/notes.md"]


def test_a_file_outside_policy_cannot_be_attached(surface, client):
    """The property that makes attaching safe: it is a reference, not a grant.

    Without this check, typing a path into the attach field would be a way to put
    `/etc/shadow` in front of the model — it would be refused, but later and
    without an explanation, and the prompt would already name a file the user
    cannot see.
    """

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    response = client.patch(
        f"/api/branches/{created['branch']['id']}",
        json={"attachments": ["/etc/shadow"]},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"] == "outside_policy"
    assert surface.agent_sessions.branches[created["branch"]["id"]].attachments == ()


def test_one_unreachable_path_rejects_the_whole_list(surface, client):
    """All or nothing, and nothing is stored.

    A partial accept would leave the client's list and the session's disagreeing,
    and the client would have no way to know which entry was dropped.
    """

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    # The bad entry first *and* last, in separate requests. With it only in the
    # last position, an implementation that checked `attachments[-1:]` — or any
    # single entry — would pass, which is exactly what a first draft of this test
    # did while a deliberately broken route stayed green.
    for entries in (
        ["/etc/passwd", "/srv/eng/ok.md"],
        ["/srv/eng/ok.md", "/etc/passwd"],
        ["/srv/eng/a.md", "/etc/passwd", "/srv/eng/b.md"],
    ):
        response = client.patch(
            f"/api/branches/{created['branch']['id']}",
            json={"attachments": entries},
            headers={"x-csrf-token": csrf},
        )
        assert response.status_code == 403, (entries, response.text)
        assert surface.agent_sessions.branches[created["branch"]["id"]].attachments == ()


DROPS = "/home/alice/.local/state/neuman/drops"


def test_a_dropped_file_can_be_attached_while_its_client_is_connected(surface, client):
    """D102: the client's drops directory is attachable though no policy names it.

    Strictly inside it, and only while a client has said where it is: a path
    that merely starts with the same letters, the directory itself, and a `..`
    walk out of it are all refused, and so is every drop with no client.
    """

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()
    branch_id = created["branch"]["id"]
    dropped = f"{DROPS}/{'a' * 64}/notes.pdf"

    def attach(*entries: str):
        return client.patch(
            f"/api/branches/{branch_id}",
            json={"attachments": list(entries)},
            headers={"x-csrf-token": csrf},
        )

    assert attach(dropped).status_code == 403

    surface.services.execution.drops_value = DROPS
    response = attach(dropped)
    assert response.status_code == 200, response.text
    assert response.json()["attachments"] == [dropped]

    for outside in (DROPS, f"{DROPS}-evil/x.pdf", f"{DROPS}/../session.json"):
        assert attach(outside).status_code == 403, outside
    assert surface.agent_sessions.branches[branch_id].attachments == (dropped,)


def test_the_attached_paths_are_listed_for_the_client_sweep(surface, client):
    """D102: every live conversation's attachments, and not a removed one's."""

    surface.services.execution.drops_value = DROPS
    csrf = authenticate(surface, client)
    kept = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()
    gone = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()
    for created, path in ((kept, f"{DROPS}/k/kept.md"), (gone, f"{DROPS}/g/gone.md")):
        response = client.patch(
            f"/api/branches/{created['branch']['id']}",
            json={"attachments": [path, "/srv/eng/a.md"]},
            headers={"x-csrf-token": csrf},
        )
        assert response.status_code == 200, response.text
    client.post(f"/api/sessions/{gone['id']}/remove", headers={"x-csrf-token": csrf})

    response = client.get("/api/attachments")

    assert response.status_code == 200, response.text
    assert response.json() == {"attachments": [f"{DROPS}/k/kept.md", "/srv/eng/a.md"]}


def test_every_path_is_rechecked_not_just_the_new_one(surface, client):
    """A narrowed policy cannot be outlived by re-sending an older list.

    The client holds the list it last rendered. If only the *added* entry were
    checked, a PATCH that re-sent a previously-valid path would preserve reach
    that policy no longer grants.
    """

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()
    branch_id = created["branch"]["id"]

    # Valid under the current baseline.
    assert (
        client.patch(
            f"/api/branches/{branch_id}",
            json={"attachments": ["/srv/eng/a.md"]},
            headers={"x-csrf-token": csrf},
        ).status_code
        == 200
    )

    # Policy narrows: the baseline no longer grants /srv/eng. The baseline is
    # runtime state now, so the test changes the object the next read composes.
    surface.services.baseline = {
        "schema_version": 2,
        "defaults": {
            "tools": ["fs_read"],
            "files_read": ["/srv/other"],
            "files_write": [],
            "auto_accept": "safe",
        },
        "roles": {},
        "users": [{"name": "alice"}],
        **CATALOG,
    }
    surface.store_baseline()

    # Re-sending the same list must now be refused, not preserved.
    response = client.patch(
        f"/api/branches/{branch_id}",
        json={"attachments": ["/srv/eng/a.md"]},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 403, response.text


def test_attachments_are_deduplicated_in_order(surface, client):
    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    response = client.patch(
        f"/api/branches/{created['branch']['id']}",
        json={"attachments": ["/srv/eng/b.md", "/srv/eng/a.md", "/srv/eng/b.md"]},
        headers={"x-csrf-token": csrf},
    )
    assert response.json()["attachments"] == ["/srv/eng/b.md", "/srv/eng/a.md"]


def test_attachments_can_be_cleared(surface, client):
    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()
    client.patch(
        f"/api/branches/{created['branch']['id']}",
        json={"attachments": ["/srv/eng/a.md"]},
        headers={"x-csrf-token": csrf},
    )

    response = client.patch(
        f"/api/branches/{created['branch']['id']}",
        json={"attachments": []},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json()["attachments"] == []


def test_too_many_attachments_are_refused(surface, client):
    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    response = client.patch(
        f"/api/branches/{created['branch']['id']}",
        json={"attachments": [f"/srv/eng/f{n}.md" for n in range(50)]},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 400, response.text


def test_a_malformed_attachment_list_is_refused(surface, client):
    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    for bad in ("/srv/eng/a.md", [1, 2], [""], [None], {"a": 1}):
        response = client.patch(
            f"/api/branches/{created['branch']['id']}",
            json={"attachments": bad},
            headers={"x-csrf-token": csrf},
        )
        assert response.status_code == 400, (bad, response.status_code)


def test_attachments_survive_a_resume(surface, client):
    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()
    client.patch(
        f"/api/branches/{created['branch']['id']}",
        json={"attachments": ["/srv/eng/a.md"]},
        headers={"x-csrf-token": csrf},
    )

    reopened = client.get(f"/api/sessions/{created['id']}").json()
    assert reopened["branch"]["attachments"] == ["/srv/eng/a.md"]


def test_the_session_filter_searches_what_was_said(surface, client):
    """The filter finds a conversation by its content, not just its title.

    It was a substring match over titles in the browser, so a session was only
    findable by the first few words of its first message — the conversation
    itself was unsearchable. Searching server-side is what makes the rest of it
    reachable without shipping every account's history to the page.
    """

    csrf = authenticate(surface, client)
    hit = client.post(
        "/api/sessions", json={"title": "Tuesday"}, headers={"x-csrf-token": csrf}
    ).json()
    miss = client.post(
        "/api/sessions", json={"title": "Wednesday"}, headers={"x-csrf-token": csrf}
    ).json()
    surface.agent_sessions.said[hit["id"]] = "how do I raise the retention window"
    surface.agent_sessions.said[miss["id"]] = "unrelated chatter about lunch"

    found = client.get("/api/sessions?q=retention").json()["sessions"]
    assert [s["id"] for s in found] == [hit["id"]]
    # And it says *why* it matched, with the term marked for the client to
    # render — a filtered list of bare titles gives no clue what hit.
    assert "<<retention>>" in found[0]["snippet"]


def test_the_session_filter_still_matches_a_deliberate_title(surface, client):
    """A renamed session must stay findable by the name someone chose.

    The title is not necessarily in the conversation — that is the point of
    renaming one — so a content-only search would lose it.
    """

    csrf = authenticate(surface, client)
    created = client.post(
        "/api/sessions", json={"title": "Quarterly audit"}, headers={"x-csrf-token": csrf}
    ).json()

    found = client.get("/api/sessions?q=Quarterly").json()["sessions"]
    assert [s["id"] for s in found] == [created["id"]]
    # No snippet: the match was the title, and a line repeating it would be noise.
    assert "snippet" not in found[0]


def test_an_unfiltered_listing_carries_no_snippets(surface, client):
    """The snippet is an artefact of searching, not a field of a session."""

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()
    surface.agent_sessions.said[created["id"]] = "some conversation"

    listing = client.get("/api/sessions").json()["sessions"]
    assert listing and all("snippet" not in s for s in listing)


def test_the_session_filter_is_scoped_to_the_caller(surface, client):
    """Someone else's conversation must not be searchable, content included.

    The scope is a clause in the query rather than a check in the route, but a
    search path is exactly where a forgotten scope leaks the most: the matching
    text is the private part.
    """

    other = surface.agent_sessions.seed(account_id="acct-bob")
    surface.agent_sessions.said[other.id] = "bob's secret retention notes"

    authenticate(surface, client)
    found = client.get("/api/sessions?q=retention").json()["sessions"]
    assert found == []


def test_usage_reports_todays_tokens_against_the_cap(surface, client):
    """What the Usage tab reads.

    `settled` and `reserved` are reported apart rather than summed: a
    reservation is released when a turn fails, so folding it into one figure
    would show a total that falls back down and read as broken accounting.
    """

    surface.usage.today = {"settled": 12480, "reserved": 4096}
    authenticate(surface, client)

    body = client.get("/api/usage").json()
    assert body["settled"] == 12480
    assert body["reserved"] == 4096
    # From composed policy rather than the usage row: the cap can change under a
    # running day, and a percentage against a stale limit is worse than none.
    assert "tokens_daily" in body


def test_usage_reports_an_uncapped_account_as_uncapped(surface, client):
    """`None`, not a number the client could divide by.

    The default baseline sets no `tokens_daily`, so this is the ordinary case —
    and the client has to draw no bar rather than a full or empty one.
    """

    authenticate(surface, client)
    assert client.get("/api/usage").json()["tokens_daily"] is None


def test_usage_is_scoped_to_the_caller(surface, client):
    """Self-scoped like the rest of this surface: no account id in the request.

    The figure comes from `principal.account_id`, so there is no parameter to
    tamper with — which is the property being pinned, since a usage route that
    took an account name would be an operator surface wearing a user's clothes.
    """

    authenticate(surface, client)
    # Nothing in the query string changes whose usage is returned.
    mine = client.get("/api/usage").json()
    assert client.get("/api/usage?account=bob").json() == mine


def test_the_tool_list_omits_an_ungranted_diagnostic(surface, client):
    """A diagnostic is appliance scaffolding, not a capability anyone chooses.

    Listed struck-through, it reads as a tool an operator could grant — and one
    of them allocates memory until the cgroup stops it. So it is left out
    entirely, while `fs_list` (ungranted but real) is still listed with its
    reason, which is the distinction being asserted here.
    """

    authenticate(surface, client)
    names = {tool["name"] for tool in client.get("/api/tools").json()["tools"]}

    assert "hog" not in names
    assert "fs_list" in names, "an ordinary ungranted tool must still be listed"


def test_a_granted_diagnostic_is_listed_and_marked(surface, client):
    """The case where hiding would be the dishonest choice.

    If a diagnostic really is granted it is a tool this account can call, and a
    chooser that omitted it would leave a capability in force with no control
    over it. So it appears, flagged, rather than disappearing.
    """

    # The baseline is runtime state now, not a file re-read on every request.
    surface.services.baseline = {
        "schema_version": 2,
        "defaults": {
            "tools": ["fs_read", "hog"],
            "files_read": ["/srv/eng"],
            "files_write": [],
            "auto_accept": "safe",
        },
        "roles": {},
        "users": [{"name": "alice"}],
        **CATALOG,
    }
    surface.store_baseline()

    authenticate(surface, client)
    tools = {tool["name"]: tool for tool in client.get("/api/tools").json()["tools"]}

    assert tools["hog"]["allowed"] is True
    assert tools["hog"]["diagnostic"] is True


def test_a_new_session_selects_no_tools_in_particular(surface, client):
    """`None`, not a list of everything installed (D50).

    The two look identical today and diverge the moment the appliance installs
    another tool: a stored list would withhold it from every session created
    before it, indistinguishably from a user having unticked it.
    """

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    assert created["branch"]["tool_selection"] is None


def test_a_tool_selection_is_stored_and_survives_a_resume(surface, client):
    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    response = client.patch(
        f"/api/branches/{created['branch']['id']}",
        json={"tool_selection": ["fs_read"]},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json()["tool_selection"] == ["fs_read"]

    reopened = client.get(f"/api/sessions/{created['id']}").json()
    assert reopened["branch"]["tool_selection"] == ["fs_read"]


def test_a_tool_selection_cannot_name_an_ungranted_tool(surface, client):
    """The safety property at the surface: stored, but narrowed to the grant.

    `fs_list` is installed on this appliance and not granted by the baseline. It
    is dropped rather than refusing the whole request — an ungranted name in the
    list is a stale checkbox from before a revocation, and refusing would leave
    the user unable to save anything until they reloaded.
    """

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    response = client.patch(
        f"/api/branches/{created['branch']['id']}",
        json={"tool_selection": ["fs_read", "fs_list"]},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json()["tool_selection"] == ["fs_read"]


def test_an_empty_tool_selection_is_kept_distinct_from_the_default(surface, client):
    """`[]` is a user asking for no tools; `null` is every granted tool.

    Collapsing them would make Select-none silently mean Select-all, which is
    the one confusion this column's nullability exists to prevent.
    """

    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    emptied = client.patch(
        f"/api/branches/{created['branch']['id']}",
        json={"tool_selection": []},
        headers={"x-csrf-token": csrf},
    )
    assert emptied.status_code == 200, emptied.text
    assert emptied.json()["tool_selection"] == []

    # And back to the default, which is what Select all writes.
    restored = client.patch(
        f"/api/branches/{created['branch']['id']}",
        json={"tool_selection": None},
        headers={"x-csrf-token": csrf},
    )
    assert restored.json()["tool_selection"] is None


def test_a_malformed_tool_selection_is_refused(surface, client):
    csrf = authenticate(surface, client)
    created = client.post("/api/sessions", json={}, headers={"x-csrf-token": csrf}).json()

    for bad in ("fs_read", [1, 2], [""], [None], {"a": 1}):
        response = client.patch(
            f"/api/branches/{created['branch']['id']}",
            json={"tool_selection": bad},
            headers={"x-csrf-token": csrf},
        )
        assert response.status_code == 400, (bad, response.status_code)


def test_browsing_can_include_files(surface, client):
    """The attachment picker needs files; the directory chooser does not.

    Asserted on the dispatch, because whether files come back is `fs_list`'s
    decision and the fake replays a fixed stdout regardless.
    """

    authenticate(surface, client)

    client.get("/api/browse?path=/srv/eng")
    client.get("/api/browse?path=/srv/eng&files=1")

    assert [d["args"]["dirs_only"] for d in surface.services.execution.dispatches] == [
        True,
        False,
    ]


# -- the model panel ----------------------------------------------------------


def test_model_state_needs_a_session(client):
    assert client.get("/api/models/state").status_code in (401, 403)


def test_an_authenticated_non_operator_is_refused(surface, client):
    """The check that matters, and the one N32 was about.

    Every operator gate before this lived on the admin socket, where peer
    credentials do half the work. This is the session surface, so "signed in" and
    "holds authority" are genuinely separate — and a test that only proved an
    anonymous request fails would pass against a route with no operator check at
    all.
    """

    csrf = authenticate(surface, client, "alice")  # eng, not neuman-admin

    for method, path in (
        ("GET", "/api/models/state"),
        ("POST", "/api/models/qwen/load"),
        ("POST", "/api/models/qwen/unload"),
    ):
        response = client.request(
            method,
            path,
            json={} if method == "POST" else None,
            headers={"x-csrf-token": csrf},
        )
        assert response.status_code == 403, (path, response.status_code, response.text)
        assert response.json()["error"] == "forbidden", path


def test_an_operator_may_read_model_state(surface, client):
    csrf = authenticate(surface, client, "carol")  # neuman-admin
    del csrf

    response = client.get("/api/models/state")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "models" in body and "gpu" in body


def test_the_identity_response_reports_operator_status(surface, client):
    """So the client knows whether to draw the panel.

    A hint for the UI only: every route re-checks, so a forged flag gets a prettier
    button and the same 403.
    """

    authenticate(surface, client, "alice")
    assert client.get("/api/me").json()["is_admin"] is False

    with surface.client() as other:
        authenticate(surface, other, "carol")
        assert other.get("/api/me").json()["is_admin"] is True


def _local_baseline() -> dict[str, Any]:
    """A baseline with one model on this machine."""

    return {
        "schema_version": 2,
        "defaults": {"tools": ["fs_read"], "models": ["qwen-here"]},
        "roles": {},
        "users": [{"name": "carol"}],
        "models": {
            "qwen-here": {
                "provider_model": "qwen-here",
                "preset": "qwen3_8-27b",
                "context_tokens": 65536,
                "local": {"residency": "on-demand", "idle_minutes": 15},
            }
        },
    }


def test_the_panel_reports_what_the_runtime_host_runs(monkeypatch, tmp_path):
    """Loaded or not is the host's to say; the panel lists what the baseline chose."""

    runtime = FakeRuntime(
        {
            "problem": None,
            "chat": [{"name": "qwen-here", "loaded": True, "state": "loaded"}],
        }
    )
    surface = build_surface(monkeypatch, tmp_path, baseline=_local_baseline(), runtime=runtime)
    with surface.client() as client:
        authenticate(surface, client, "carol")
        body = client.get("/api/models/state").json()

    models = {m["name"]: m for m in body["models"]}
    assert models["qwen-here"]["loaded"] is True
    # From the preset's pinned files: an estimate the UI labels as one.
    assert models["qwen-here"]["size_mb"] > 16000
    chat = {m["name"]: m for m in body["chat"]}
    assert chat["qwen-here"]["local"] is True
    assert chat["qwen-here"]["loaded"] is True


def test_a_host_that_does_not_answer_is_unknown_not_off(monkeypatch, tmp_path):
    """None, which the panel draws as unknown; False would claim the model is off."""

    class Silent(FakeRuntime):
        async def status(self):
            return None

    surface = build_surface(monkeypatch, tmp_path, baseline=_local_baseline(), runtime=Silent())
    with surface.client() as client:
        authenticate(surface, client, "carol")
        models = client.get("/api/models/state").json()["models"]

    assert models and all(m["loaded"] is None for m in models), models


def test_loading_passes_the_host_a_name(monkeypatch, tmp_path):
    runtime = FakeRuntime()
    surface = build_surface(monkeypatch, tmp_path, baseline=_local_baseline(), runtime=runtime)
    with surface.client() as client:
        csrf = authenticate(surface, client, "carol")
        for path in ("/api/models/qwen-here/load", "/api/models/qwen-here/unload"):
            response = client.post(path, json={}, headers={"x-csrf-token": csrf})
            assert response.status_code == 200, response.text

    assert runtime.calls == [("load", "qwen-here"), ("unload", "qwen-here")]


def test_a_name_the_baseline_does_not_run_never_reaches_the_host(monkeypatch, tmp_path):
    """What stops `/api/models/neuman-server/unload` from meaning anything.

    The name is matched against what the operator chose before the host is
    asked, and the host matches it again against what it was told to run; it is
    never a unit or a path in either place.
    """

    runtime = FakeRuntime()
    surface = build_surface(monkeypatch, tmp_path, baseline=_local_baseline(), runtime=runtime)
    with surface.client() as client:
        csrf = authenticate(surface, client, "carol")
        for path in ("/api/models/neuman-server/unload", "/api/models/m/load"):
            response = client.post(path, json={}, headers={"x-csrf-token": csrf})
            assert response.status_code == 404, (path, response.text)

    assert runtime.calls == []


def test_model_actions_need_a_csrf_token(monkeypatch, tmp_path):
    runtime = FakeRuntime()
    surface = build_surface(monkeypatch, tmp_path, baseline=_local_baseline(), runtime=runtime)
    with surface.client() as client:
        authenticate(surface, client, "carol")
        assert client.post("/api/models/qwen-here/load", json={}).status_code == 403

    assert runtime.calls == []


def test_the_panel_offers_only_models_policy_grants(monkeypatch, tmp_path):
    """The panel replaces the header dropdown, so policy is still the ceiling.

    Two independent reasons it holds, and the test checks both because either alone
    could be removed:

    1. The route iterates the *granted* names, not the catalog.
    2. `ModelCatalog.describe` iterates the names it was *given*, so it cannot
       return an entry for one it was not asked about.

    The second is why a first version of this test was vacuous: the fake mirrored
    that scoping faithfully, so no catalog contents could make an ungranted model
    appear. It is checked here against a catalog that declares more than policy
    grants, which is the only arrangement where the two halves differ.
    """

    import re
    from pathlib import Path

    # 1. The route's loop.
    route = (
        Path(__file__).parent.parent
        / "src"
        / "neuman_server"
        / "composition"
        / "http"
        / "models.py"
    ).read_text()
    code = re.sub(r"#.*", "", route)
    assert "for name in granted:" in code, (
        "the chat list is not built from the granted names; it may offer models policy "
        "does not allow"
    )

    # 2. `describe` iterating its argument rather than the catalog.
    store = (
        Path(__file__).parent.parent
        / "src"
        / "neuman_server"
        / "decision"
        / "harness"
        / "catalog.py"
    ).read_text()
    describe = re.search(r"def describe\(.*", store, re.S)
    assert describe, "describe() is gone; this test would pass vacuously"
    assert "for name in names:" in describe.group(0), (
        "describe() is not scoped to the names it was given, so it can return the whole catalog"
    )

    # And end to end: a granted name appears, and the response carries only that.
    baseline = {
        "schema_version": 2,
        "defaults": {
            "tools": ["fs_read"],
            "files_read": ["/srv/eng"],
            "files_write": [],
            "auto_accept": "safe",
            "models": ["granted-one"],
        },
        "roles": {},
        "users": [{"name": "carol"}],
        "providers": {
            "test-provider": {
                "url": "https://cloud.test/v1",
                "api_key_file": None,
            }
        },
        # Two declared, one granted. A panel built from the catalog
        # rather than from policy would show both.
        "models": {
            "granted-one": {
                "provider": "test-provider",
                "provider_model": "provider/granted-one",
            },
            "ungranted-two": {
                "provider": "test-provider",
                "provider_model": "provider/ungranted-two",
            },
        },
    }

    surface = build_surface(monkeypatch, tmp_path, baseline=baseline)
    with surface.client() as client:
        authenticate(surface, client, "carol")
        names = [m["name"] for m in client.get("/api/models/state").json()["chat"]]

    assert names == ["granted-one"], names


def test_a_chat_model_says_whether_it_is_local_and_what_it_costs(monkeypatch, tmp_path):
    """ "deepseek or qwen" tells a user nothing about what selecting one means.

    Local versus cloud is whether the runtime host serves the entry — one with no
    provider — rather than a guess from a loopback URL, which a hand-declared
    provider on this machine also has. Cost comes from `input_cost_per_m` and
    `output_cost_per_m`, which sat in the catalog unread for the whole life of the
    table that used to hold them -- and which the CLI writing that table had no
    flags to set, so every registered model cost zero.
    """

    baseline = {
        "schema_version": 2,
        "defaults": {
            "tools": ["fs_read"],
            "files_read": ["/srv/eng"],
            "files_write": [],
            "auto_accept": "safe",
            "models": ["on-my-gpu", "in-the-cloud"],
        },
        "roles": {},
        "users": [{"name": "carol"}],
        # One run here and one hosted -- and different costs, so a route
        # reporting one model's price for another cannot pass.
        "providers": {
            "cloud": {
                "url": "https://openrouter.test/api/v1",
                "api_key_file": "/run/secrets/openrouter",
            },
        },
        "models": {
            "on-my-gpu": {
                "provider_model": "on-my-gpu",
                "preset": "qwen3_8-27b",
                "context_tokens": 65536,
                "local": {"residency": "on-demand"},
                "input_cost_per_m": 0,
            },
            "in-the-cloud": {
                "provider": "cloud",
                "provider_model": "provider/x",
                "vision": True,
                "input_cost_per_m": 3.0,
            },
        },
    }

    surface = build_surface(monkeypatch, tmp_path, baseline=baseline)

    with surface.client() as client:
        authenticate(surface, client, "carol")
        chat = {m["name"]: m for m in client.get("/api/models/state").json()["chat"]}

    assert chat["on-my-gpu"]["local"] is True, chat["on-my-gpu"]
    assert chat["in-the-cloud"]["local"] is False, chat["in-the-cloud"]
    # Vision, because it decides whether fs_image works at all.
    assert chat["in-the-cloud"]["vision"] is True
    # And the costs are per model, not one number reported for both.
    assert chat["on-my-gpu"]["input_cost_per_m"] != chat["in-the-cloud"]["input_cost_per_m"]


def test_a_granted_name_with_no_catalog_row_is_marked_unregistered(monkeypatch, tmp_path):
    """Granting a name does not make it selectable.

    `policy.models` is a grant; the catalog is what makes a name resolvable. A name in
    one and not the other produces `model_error` on the first message (N48's shape), so
    the panel says so rather than offering it.
    """

    baseline = {
        "schema_version": 2,
        "defaults": {
            "tools": ["fs_read"],
            "files_read": ["/srv/eng"],
            "files_write": [],
            "auto_accept": "safe",
            "models": ["never-registered"],
        },
        "roles": {},
        "users": [{"name": "carol"}],
        # Deliberately no `models` block: the name is granted and
        # declared nowhere, which is the whole case under test.
    }

    surface = build_surface(monkeypatch, tmp_path, baseline=baseline)

    with surface.client() as client:
        authenticate(surface, client, "carol")
        chat = client.get("/api/models/state").json()["chat"]

    assert len(chat) == 1
    assert chat[0]["registered"] is False, chat[0]


def test_the_chooser_never_offers_a_name_shape_as_a_directory():
    """`fs_list` groups a large directory; the chooser must never navigate to a group.

    Past its threshold the tool prints `d<TAB>FILE#<TAB>x 2036<TAB>(…)`, which
    is the right answer for a model reading a data directory and the wrong one
    for a surface whose entries are clickable: `FILE#` is a shape, not a name,
    and offering it would send the chooser to a directory that does not exist.

    The route asks for unsummarised output, so this should never arrive. It is
    rejected structurally as well, because the cost of being wrong is a
    navigation surface that lies — and the route and the parser are edited by
    different people at different times.
    """

    from neuman_server.composition.http.browse import _parse

    grouped = "\n".join(
        [
            "(4080 entries, grouped by name shape; # is a run of digits)",
            "f\t._FILE#\tx 2036\t(._FILE1 .. ._FILE999)",
            "d\tSERIES#\tx 12\t(SERIES1 .. SERIES9)",
            "d\treal-directory",
            "f\treadme.txt",
        ]
    )

    assert _parse(grouped) == [
        {"kind": "d", "name": "real-directory"},
        {"kind": "f", "name": "readme.txt"},
    ]


def test_the_chooser_asks_for_names_rather_than_a_summary():
    """The `summarise: False` is load-bearing, so it is asserted rather than assumed.

    Without it a directory of more than 200 subdirectories would be grouped,
    and `_parse` would drop every one of them — the chooser would show an
    empty folder for a directory that is merely large.
    """

    import inspect

    from neuman_server.composition.http import browse

    source = inspect.getsource(browse)
    assert '"summarise": False' in source, (
        "the browse route must ask fs_list for real names, not a grouped summary"
    )


# -- choosing an approval mode (N88) ------------------------------------------
#
# The one policy field a user may author for themselves. What makes that safe is
# `auto_accept_max`, the operator's ceiling.
#
# Only the request-shape cases live here: the surface harness fakes its
# connection, so anything that actually writes an overlay and appends to the
# audit chain is exercised in `nix/tests/appliance.nix` against real Postgres.
# A fake that accepted the write would prove the fake agrees with the route.


def test_an_unknown_mode_is_a_bad_request(surface, client):
    """Not a ceiling problem, so not a 403: the request itself is wrong.

    Refused before anything is composed or written, which is why this one can
    run against the faked connection.
    """

    csrf = authenticate(surface, client)

    response = client.put(
        "/api/policy/auto-accept",
        json={"auto_accept": "green"},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 400, response.text
    assert response.json()["allowed"] == ["never", "safe", "yolo"]


def test_choosing_a_mode_needs_the_csrf_token(surface, client):
    """A state-changing route, so it carries the same guard as every other one.

    Worth its own test rather than assumed: this route was added after the
    others, and the one that forgets the header is the one that matters.
    """

    authenticate(surface, client)

    response = client.put("/api/policy/auto-accept", json={"auto_accept": "yolo"})
    assert response.status_code in (401, 403), response.text


def test_an_account_can_set_a_default_model(surface, client):
    """A new branch starts from a choice the person made, not the first option.

    The value is stored on the account and returned with the identity, so the
    composer can paint it before it asks for anything else (N173).
    """

    csrf = authenticate(surface, client)
    assert client.get("/api/me").json()["defaults"] == {"model": None, "reasoning": None}

    response = client.patch(
        "/api/me/defaults", json={"model": "m"}, headers={"x-csrf-token": csrf}
    )
    assert response.status_code == 200, response.text
    assert response.json()["defaults"] == {"model": "m", "reasoning": None}

    assert client.get("/api/me").json()["defaults"]["model"] == "m"


def test_a_default_model_must_be_granted(surface, client):
    """A default that cannot run would make every new conversation start wrong."""

    csrf = authenticate(surface, client)
    response = client.patch(
        "/api/me/defaults", json={"model": "ghost"}, headers={"x-csrf-token": csrf}
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"] == "model_not_granted"


def test_the_module_passes_the_reply_budget_to_the_server():
    """A Nix option nothing reads is the default wearing a disguise.

    Three of this tree's bugs were exactly that: `working_dir` reaching the
    prompt and no dispatch (N84), `contextSize` reaching llama.cpp and not the
    budget (N86), `max_tokens` existing on `HarnessConfig` and nowhere in
    `Settings` (N90). Asserted against the module source because the question
    is whether the option is connected at all, and a VM boot to answer it costs
    twelve minutes.
    """

    from pathlib import Path

    module = (
        Path(__file__).parent.parent.parent / "nix" / "module" / "default.nix"
    ).read_text()

    assert "maxTokens = lib.mkOption" in module, "no module option for the reply budget"
    assert "NEUMAN_MAX_TOKENS = toString cfg.maxTokens;" in module, (
        "the option is declared and never passed to the server"
    )


def test_the_server_reads_the_reply_budget_from_settings():
    """And the other end of the same wire.

    The env var has to reach `HarnessConfig`, not merely `Settings` — the
    harness takes its own config object, so a value parsed and left there would
    still leave the dataclass default in force.
    """

    from pathlib import Path

    ws = (
        Path(__file__).parent.parent.parent
        / "neuman-server"
        / "src"
        / "neuman_server"
        / "composition"
        / "http"
        / "ws.py"
    ).read_text()

    assert "max_tokens=settings.max_tokens," in ws, (
        "the harness is built without the configured reply budget"
    )


def test_the_model_catalog_carries_a_reply_ceiling_end_to_end():
    """Six links, and a value that exists at any one of them and not the next
    is the shape of four of this tree's bugs.

    Asserted against the sources because the alternative is a VM boot per link,
    and the question is only whether each one carries the field.

    The chain moved when the catalog did: the declaration is the schema and the
    module rather than a migration and a CLI flag, and it is read out of the
    baseline rather than a table. The links are the same ones, and the failure
    they guard against -- a value written down and never wired up -- is too.
    """

    from pathlib import Path

    root = Path(__file__).parent.parent.parent
    server = root / "neuman-server" / "src" / "neuman_server"

    # Declared in the contract the baseline is validated against.
    schema = (root / "contracts" / "schemas" / "baseline.schema.json").read_text()
    assert '"max_tokens"' in schema

    # Read out of the catalog, both for `resolve` and for the operator's view.
    catalog = (server / "decision" / "harness" / "catalog.py").read_text()
    assert catalog.count("max_tokens") >= 3, catalog.count("max_tokens")

    # Reported by the admin API, which is the one surface that can confirm a
    # declaration took effect.
    api = (server / "composition" / "adminapi" / "gateway.py").read_text()
    assert '"max_tokens": int(entry.get("max_tokens", 0) or 0)' in api

    # Carried to the decision layer.
    ports = (server / "decision" / "harness" / "ports.py").read_text()
    assert "max_tokens: int = 0" in ports

    # Preferred over the appliance default where stated, in one place rather
    # than at each site that needs it: `_reply_budget`, the usage reservation
    # and `_prompt_budget` all resolve the ceiling through `_reply_ceiling`,
    # because the third of those used to subtract the appliance-wide value
    # instead and collapsed a small model's history budget to its floor.
    loop = (server / "decision" / "harness" / "loop.py").read_text()
    assert "def _reply_ceiling" in loop
    assert loop.count("self._reply_ceiling(target)") >= 3, loop.count(
        "self._reply_ceiling(target)"
    )

    # Reaching the baseline from both kinds of model -- the asymmetry where only
    # local models were declarable is what this half of the chain used to hide.
    # Both come from a preset's starting policy: a hosted route's, and a local
    # route's, which `build_local_entry` copies into the entry the runtime's
    # model is resolved through.
    presets = (root / "contracts" / "schemas" / "presets.schema.json").read_text()
    assert presets.count('"max_tokens"') >= 2, "the local policy lost its reply ceiling"
    preset_logic = (server / "decision" / "harness" / "presets.py").read_text()
    assert '"max_tokens"' in preset_logic
    assert 'entry["max_tokens"] = policy["max_tokens"]' in preset_logic


def test_an_operator_can_list_the_accounts_the_appliance_has_seen(monkeypatch, surface, client):
    """The question an operator asks first, which had no command.

    `policy show` needed a name you already knew, so "who exists on this
    appliance, and who can administer it" was answerable only with `psql`.
    """

    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    response = client.get("/admin/accounts")
    assert response.status_code == 200, response.text

    accounts = {a["name"]: a for a in response.json()["accounts"]}
    assert {"alice", "bob", "carol"} <= set(accounts), accounts

    assert accounts["alice"]["admin"] is True
    assert accounts["carol"]["admin"] is True
    assert accounts["bob"]["admin"] is False
    for account in accounts.values():
        assert account["created"], "an account has a creation time"


def test_the_roster_pages_searches_and_carries_its_figures(monkeypatch, surface, client):
    """One page, one group read, one usage read, one session count.

    The list used to be every account with a `groups_of` query each, which is a
    page of fifty costing fifty queries before a row is drawn. The search has
    to run before the slice for the same reason: filtering the page already
    fetched would fail to find the person the operator named.
    """

    surface.accounts.groups["neuman-admin"].add("alice")
    asyncio.run(surface.accounts.create(name="dave", password_hash="x"))
    surface.usage.today_by_id = {"acct-alice": (1000, 10)}
    surface.agent_sessions.seed(account_id="acct-alice")
    surface.agent_sessions.seed(account_id="acct-alice")
    as_peer(monkeypatch, client, 1001, "alice")

    page = client.get("/admin/accounts?limit=2&offset=0").json()
    assert page["total"] == 4, page
    assert page["limit"] == 2 and page["offset"] == 0
    assert [row["name"] for row in page["accounts"]] == ["alice", "bob"], page

    alice = page["accounts"][0]
    assert alice["settled"] == 1000 and alice["reserved"] == 10, alice
    assert alice["sessions"] == 2, alice
    assert alice["admin"] is True
    assert alice["groups"] == ["eng", "neuman-admin"], alice["groups"]

    second = client.get("/admin/accounts?limit=2&offset=2").json()
    assert [row["name"] for row in second["accounts"]] == ["carol", "dave"], second

    # A name is found even when it is not on the first page; a group lists its
    # members.
    found = client.get("/admin/accounts?q=car").json()
    assert [row["name"] for row in found["accounts"]] == ["carol"], found
    assert found["total"] == 1
    operators = client.get("/admin/accounts?q=neuman-admin").json()
    assert [row["name"] for row in operators["accounts"]] == ["alice", "carol"], operators

    assert client.get("/admin/accounts?limit=x").status_code == 400


def test_creating_an_account_assigns_roles_and_groups_in_one_write(
    monkeypatch, surface, client
):
    """The onboarding form must not leave an account without its starting role."""

    surface.accounts.groups["neuman-admin"].add("alice")
    surface.services.baseline["roles"] = {
        "reader": {"tools": ["fs_read"], "files_read": ["/srv/eng"]},
    }
    surface.store_baseline()
    as_peer(monkeypatch, client, 1001, "alice")

    response = client.post(
        "/admin/accounts",
        json={
            "name": "dave",
            "password": PASSWORD,
            "roles": ["reader"],
            "groups": ["eng"],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["roles"] == ["reader"]

    assert surface.accounts.rows["dave"].name == "dave"
    assert "eng" in surface.accounts.groups
    assert "dave" in surface.accounts.groups["eng"]

    entry = next(user for user in surface.services.baseline["users"] if user["name"] == "dave")
    assert entry["roles"] == ["reader"]


def test_creating_an_account_refuses_unknown_or_conflicting_roles(monkeypatch, surface, client):
    surface.accounts.groups["neuman-admin"].add("alice")
    surface.services.baseline["roles"] = {
        "strict": {"network": "none"},
        "open": {"network": "any"},
    }
    surface.store_baseline()
    as_peer(monkeypatch, client, 1001, "alice")

    unknown = client.post(
        "/admin/accounts",
        json={
            "name": "dave",
            "password": PASSWORD,
            "roles": ["missing"],
        },
    )
    assert unknown.status_code == 400, unknown.text
    assert unknown.json()["error"] == "unknown_role"
    assert "dave" not in surface.accounts.rows

    conflict = client.post(
        "/admin/accounts",
        json={
            "name": "dave",
            "password": PASSWORD,
            "roles": ["strict", "open"],
        },
    )
    assert conflict.status_code == 400, conflict.text
    assert conflict.json()["error"] == "role_conflict"
    assert "dave" not in surface.accounts.rows

    surface.grants.grants.add(("eng", "open"))
    group_conflict = client.post(
        "/admin/accounts",
        json={
            "name": "dave",
            "password": PASSWORD,
            "roles": ["strict"],
            "groups": ["eng"],
        },
    )
    assert group_conflict.status_code == 400, group_conflict.text
    assert group_conflict.json()["error"] == "role_conflict"
    assert "dave" not in surface.accounts.rows


def test_a_baseline_write_refuses_roles_that_disagree_on_a_scalar(monkeypatch, surface, client):
    """A role edit must not make an existing account's policy ambiguous."""

    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    current = client.get("/admin/baseline").json()["baseline"]
    document = json.loads(json.dumps(current))
    document["roles"] = {
        "strict": {"network": "none"},
        "open": {"network": "any"},
    }
    for user in document["users"]:
        if user["name"] == "alice":
            user["roles"] = ["strict", "open"]

    response = client.put("/admin/baseline", json={"baseline": document, "base": current})
    assert response.status_code == 400, response.text
    assert response.json()["error"] == "role_conflict"
    assert "alice: network" in response.json()["detail"]


def test_a_group_grant_refuses_a_role_that_would_conflict(monkeypatch, surface, client):
    """Adding a group role can make a member's existing role set ambiguous."""

    surface.accounts.groups["neuman-admin"].add("alice")
    surface.accounts.groups["eng"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    current = client.get("/admin/baseline").json()["baseline"]
    document = json.loads(json.dumps(current))
    document["roles"] = {
        "strict": {"network": "none"},
        "open": {"network": "any"},
    }
    for user in document["users"]:
        if user["name"] == "alice":
            user["roles"] = ["strict"]
    saved = client.put("/admin/baseline", json={"baseline": document, "base": current})
    assert saved.status_code == 200, saved.text

    response = client.post("/admin/grants", json={"group": "eng", "role": "open"})
    assert response.status_code == 400, response.text
    assert response.json()["error"] == "role_conflict"
    assert "alice: network" in response.json()["detail"]


def test_adding_a_member_refuses_a_group_role_that_would_conflict(monkeypatch, surface, client):
    """Membership is a role write even when the role was granted earlier."""

    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    current = client.get("/admin/baseline").json()["baseline"]
    document = json.loads(json.dumps(current))
    document["roles"] = {
        "strict": {"network": "none"},
        "open": {"network": "any"},
    }
    for user in document["users"]:
        if user["name"] == "alice":
            user["roles"] = ["strict"]
    saved = client.put("/admin/baseline", json={"baseline": document, "base": current})
    assert saved.status_code == 200, saved.text

    granted = client.post("/admin/grants", json={"group": "ops", "role": "open"})
    assert granted.status_code == 200, granted.text

    response = client.post("/admin/groups/ops/members", json={"account": "alice"})
    assert response.status_code == 400, response.text
    assert response.json()["error"] == "role_conflict"
    assert "network" in response.json()["detail"]


def test_the_onboarding_preview_composes_the_draft_without_an_account(
    monkeypatch, surface, client
):
    surface.accounts.groups["neuman-admin"].add("alice")
    surface.services.baseline["roles"] = {
        "strict": {"network": "none", "files_read": ["/srv/eng"]},
        "open": {"network": "any", "files_read": ["/srv/ops"]},
    }
    surface.store_baseline()
    as_peer(monkeypatch, client, 1001, "alice")

    response = client.post(
        "/admin/policy/preview",
        json={"roles": ["strict", "open"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["policy"]["files_read"] == ["/srv/eng", "/srv/ops"]
    assert body["conflicts"] == [
        {
            "field": "network",
            "values": [
                {"role": "open", "value": "any"},
                {"role": "strict", "value": "none"},
            ],
        }
    ]


def test_the_tool_catalog_is_the_registry_not_one_accounts_grants(monkeypatch, surface, client):
    """The matrix needs every tool, including ones no role grants.

    Reading the catalog out of an account's effective policy would hide every
    tool no role granted — exactly the set an operator opens this to grant.
    """

    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    body = client.get("/admin/tools").json()
    names = [tool["name"] for tool in body["tools"]]
    assert "fs_read" in names, names
    assert "hog" in names, "a diagnostic no role grants is missing from the catalog"

    hog = next(tool for tool in body["tools"] if tool["name"] == "hog")
    assert hog["diagnostic"] is True
    assert set(hog) == {"name", "summary", "needs", "mutating", "critical", "diagnostic"}


def test_the_policy_read_includes_the_overlay_as_written(monkeypatch, surface, client):
    """The editor sends back fields it did not touch, so it must be given them.

    Reconstructing an overlay from the effective policy would drop a field
    whose layer happened to agree with the composed value, and clearing it
    later would silently do nothing.
    """

    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    fragment = {"max_iterations": 7, "files_read": ["/tmp/extra"]}
    written = client.put("/admin/policy/alice/overlay", json=fragment)
    assert written.status_code == 200, written.text

    body = client.get("/admin/policy/alice").json()
    assert body["overlay"] == {"files_read": ["/tmp/extra"], "max_iterations": 7}, body[
        "overlay"
    ]

    # The inherited view leaves the overlay out, so the editor can show what
    # the roles already give and write only the difference.
    assert body["inherited"]["max_iterations"] == 500
    assert "/tmp/extra" not in body["inherited"]["files_read"]
    # A value no layer set carries no provenance; the client reads that as
    # `defaults`, the same fallback it uses for the effective view.
    assert body["inherited_provenance"].get("max_iterations", "defaults") == "defaults"


def test_renaming_a_role_moves_declared_references_and_group_grants(
    monkeypatch, surface, client
):
    """A rename is not just a key edit: a stale grant is a silent revocation."""

    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    current = client.get("/admin/baseline").json()["baseline"]
    document = json.loads(json.dumps(current))
    document["roles"] = {"me": {"tools": ["fs_read"]}}
    for user in document["users"]:
        if user["name"] == "alice":
            user["roles"] = ["me"]
    saved = client.put("/admin/baseline", json={"baseline": document, "base": current})
    assert saved.status_code == 200, saved.text

    granted = client.post("/admin/grants", json={"group": "eng", "role": "me"})
    assert granted.status_code == 200, granted.text

    renamed = client.post(
        "/admin/roles/me/rename", json={"to": "full-access", "base": document}
    )
    assert renamed.status_code == 200, renamed.text
    body = renamed.json()
    assert "me" not in body["baseline"]["roles"]
    assert "full-access" in body["baseline"]["roles"]
    alice = next(user for user in body["baseline"]["users"] if user["name"] == "alice")
    assert alice["roles"] == ["full-access"]

    grants = client.get("/admin/grants").json()["grants"]
    assert {"group": "eng", "role": "full-access"} in grants, grants
    assert {"group": "eng", "role": "me"} not in grants, grants

    policy = client.get("/admin/policy/alice").json()
    assert {"name": "full-access", "source": "declared", "known": True} in policy["roles"]


def test_a_role_rename_refuses_a_stale_baseline_and_an_unknown_role(
    monkeypatch, surface, client
):
    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    current = client.get("/admin/baseline").json()["baseline"]
    stale = client.post("/admin/roles/me/rename", json={"to": "full-access", "base": {}})
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"] == "baseline_changed"

    unknown = client.post("/admin/roles/me/rename", json={"to": "full-access", "base": current})
    assert unknown.status_code == 400, unknown.text
    assert unknown.json()["error"] == "unknown_role"


def test_the_policy_read_names_the_roles_in_force_and_their_source(
    monkeypatch, surface, client
):
    """An operator editing a role has to see whether the account holds it.

    The roles reach an account by declaration or by a group grant, and only the
    first is edited on the account page. A list of names without the path would
    send the operator to the wrong place to remove it.
    """

    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    current = client.get("/admin/baseline").json()["baseline"]
    document = json.loads(json.dumps(current))
    document["roles"] = {"eng": {"tools": ["shell"], "max_iterations": 40}}
    for user in document["users"]:
        if user["name"] == "alice":
            user["roles"] = ["eng"]
    saved = client.put("/admin/baseline", json={"baseline": document, "base": current})
    assert saved.status_code == 200, saved.text

    granted = client.post("/admin/grants", json={"group": "eng", "role": "eng"})
    assert granted.status_code == 200, granted.text

    body = client.get("/admin/policy/alice").json()
    assert {"name": "eng", "source": "declared", "known": True} in body["roles"]
    assert {"name": "eng", "source": "group:eng", "known": True} in body["roles"]
    assert "shell" in body["inherited"]["tools"]
    assert body["provenance"]["max_iterations"] == "role:eng"


def test_the_policy_read_reports_roles_that_disagree_on_a_scalar(monkeypatch, surface, client):
    """Alphabetical role order is not a policy an operator can see."""

    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    # The write paths reject this now; the read path still has to report a
    # conflict already present in a database restored from an older build.
    surface.services.baseline["roles"] = {
        "strict": {"network": "none"},
        "open": {"network": "any"},
    }
    surface.store_baseline()
    for user in surface.services.baseline["users"]:
        if user["name"] == "alice":
            user["roles"] = ["strict", "open"]

    body = client.get("/admin/policy/alice").json()
    assert body["conflicts"] == [
        {
            "field": "network",
            "values": [
                {"role": "open", "value": "any"},
                {"role": "strict", "value": "none"},
            ],
        }
    ]


def test_usage_can_be_asked_for_one_account(monkeypatch, surface, client):
    """One account's page asks for its own history, not everyone's.

    The roster on a large appliance would otherwise send every other account's
    days across the socket to draw one chart.
    """

    surface.usage.everyone = [("alice", 10, 0), ("bob", 20, 0)]
    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    body = client.get("/admin/usage?account=bob").json()
    assert [row["name"] for row in body["accounts"]] == ["bob"], body


def test_a_baseline_save_is_a_compare_and_set(monkeypatch, surface, client):
    """An edit made against a stale document is refused, not merged.

    Roles, models, providers and declared users are sections of one document.
    A tab that has been open while another added a role would otherwise write
    its stale copy back and erase that role, with neither operator seeing it.
    """

    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    current = client.get("/admin/baseline").json()["baseline"]
    document = json.loads(json.dumps(current))
    document.setdefault("roles", {})["new"] = {"tools": ["fs_read"]}

    saved = client.put("/admin/baseline", json={"baseline": document, "base": current})
    assert saved.status_code == 200, saved.text
    after = client.get("/admin/baseline").json()["baseline"]
    assert after["roles"]["new"] == {"tools": ["fs_read"]}, after["roles"]


def test_a_baseline_save_against_a_stale_document_is_refused(monkeypatch, surface, client):
    """409 rather than a silent overwrite; the client reloads and reapplies."""

    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    from neuman_server.composition.adminapi import settings as admin_settings

    async def stale(store, key, *, expected, value):
        return False

    monkeypatch.setattr(admin_settings.SettingsStore, "replace_if", stale)
    current = client.get("/admin/baseline").json()["baseline"]
    response = client.put("/admin/baseline", json={"baseline": current, "base": current})
    assert response.status_code == 409, response.text
    assert response.json()["error"] == "baseline_changed"


def test_a_baseline_replaced_whole_still_works_for_scripts(monkeypatch, surface, client):
    """No `base` is the CLI's spelling: replace the document, not merge it."""

    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    current = client.get("/admin/baseline").json()["baseline"]
    response = client.put("/admin/baseline", json={"baseline": current})
    assert response.status_code == 200, response.text


def test_every_admin_read_answers_for_an_operator(monkeypatch, surface, client):
    """The guard is not the whole route, and a handler that raises is a 500.

    `test_an_operator_gets_past_the_guard` checks `/admin/status` and stops, so
    it proves the *guard* admits an operator and nothing about the handlers
    behind it. That left `GET /admin/sessions` answering 500 in production
    while the suite was green: `admin_actor` had lost its `conn` parameter and
    become synchronous, `audit.py` and `system.py` were updated to match, and
    `sessions.py` and `policy.py` were not — so every route in those two files
    raised `TypeError: admin_actor() takes 1 positional argument but 2 were
    given` on its first real call (N118).

    Derived from the router rather than listed, so a route added tomorrow is
    covered today. Reads only: the mutating routes need bodies and have their
    own tests, and a POST that half-succeeds is not something to fire blindly
    at a shared fixture.
    """

    surface.accounts.groups["eng"].add("alice")
    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    reads = [(method, path) for method, path in admin_paths() if method == "GET"]
    assert reads, "no admin read routes found; this test would pass vacuously"

    unreachable: list[str] = []
    for method, path in reads:
        concrete = (
            path.replace("{name}", "alice")
            .replace("{account}", "alice")
            .replace("{session_id}", "0" * 32)
        )
        try:
            response = client.request(method, concrete)
        except AttributeError as exc:
            # `_FakeConnection` implements what lifespan asks and deliberately
            # not `transaction()`, because stubbing it would let the migration
            # migration runner "apply" DDL into a void and make a broken migration look
            # fine. A route whose read appends to the audit chain therefore
            # cannot complete here — recorded rather than silently skipped, so
            # the gap is visible and the count below still has to hold.
            if "transaction" not in str(exc):
                raise
            unreachable.append(f"{method} {concrete}")
            continue
        # 404 is a legitimate answer for an id the fixture does not have; 5xx
        # never is, and that is the whole point of the assertion.
        assert response.status_code < 500, (
            f"{method} {concrete} answered {response.status_code} for an "
            f"authorized operator: {response.text[:300]}"
        )

    # The fixture cannot reach the chain-appending reads, and this test would
    # pass vacuously if it ever stopped reaching any of the others.
    answered = len(reads) - len(unreachable)
    assert answered >= 4, (answered, unreachable)


def test_an_ambiguous_session_prefix_is_refused_rather_than_guessed(
    monkeypatch, surface, client
):
    """Two sessions behind one prefix must be an error, never a choice.

    The admin read path resolves a short id so the twelve characters
    `neuman-admin session list` prints are usable — but a prefix matching two
    sessions could belong to two different accounts, and picking the newer one
    would hand an admin someone else's transcript while looking like success.

    Checked before the audit append, which is why this is reachable under a
    fixture that cannot complete the chain-appending part of the route.
    """

    surface.accounts.groups["eng"].add("alice")
    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    shared = "abcdef123456"
    surface.agent_sessions.seed(account_id="acct-one", session_id=shared + "a" * 20)
    surface.agent_sessions.seed(account_id="acct-two", session_id=shared + "b" * 20)

    response = client.get(f"/admin/sessions/{shared}?view=kinds")

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error"] == "ambiguous_session"
    assert len(body["candidates"]) == 2


def test_the_short_id_the_cli_prints_resolves(monkeypatch, surface, client):
    """`session list` prints twelve characters; they have to work.

    They did not. Only the full thirty-two-character id resolved, so the id the
    CLI displayed was refused by the very commands it was printed for, and the
    refusal was a bare `not_found` that reads as a missing session rather than a
    truncated id. This is the positive half of the prefix behaviour — without
    it, refusing every prefix would satisfy the ambiguity test above.
    """

    surface.accounts.groups["eng"].add("alice")
    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    row = surface.agent_sessions.seed(account_id="acct-one", session_id="c" * 32)

    response = client.get(f"/admin/sessions/{row.id[:12]}?view=kinds")

    assert response.status_code == 200, response.text
    # Answered *for the session it resolved to*, not for the fragment typed.
    assert response.json()["session_id"] == row.id


def _seed_events(surface, session_id: str, *, chunks: int, results: int = 0) -> None:
    """A session shaped like a real one: many streamed deltas, few results."""

    for index in range(chunks):
        asyncio.run(
            surface.events.append(
                branch_id=session_id, kind="response_chunk", payload={"text": str(index)}
            )
        )
    for index in range(results):
        asyncio.run(
            surface.events.append(
                branch_id=session_id,
                kind="tool_result",
                payload={"tool": "shell", "stdout": f"result {index}"},
            )
        )


def test_the_admin_session_list_says_what_each_conversation_spent(monkeypatch, surface, client):
    """A token column, summed from each session's own log.

    One query for the page rather than one per row: the list can be every
    session on the appliance, and a table that costs N round trips to draw is
    the shape the usage roster was just changed to avoid. A session with no
    model call yet reads as zero rather than blank, so the column is a number
    on every row.
    """

    surface.accounts.groups["eng"].add("alice")
    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    spent = surface.agent_sessions.seed(account_id="acct-alice")
    quiet = surface.agent_sessions.seed(account_id="acct-alice")
    for total in (5419, 1200):
        asyncio.run(
            surface.events.append(
                branch_id=spent.id,
                kind="response_final",
                payload={"message": {}, "usage": {"total_tokens": total}},
            )
        )

    response = client.get("/admin/sessions")
    assert response.status_code == 200, response.text
    rows = {row["id"]: row for row in response.json()["sessions"]}

    assert rows[spent.id]["tokens"] == 6619, rows[spent.id]
    assert rows[quiet.id]["tokens"] == 0, rows[quiet.id]


def test_the_purge_plan_names_the_account(monkeypatch, surface, client):
    """A deletion is approved by reading the plan, so it names a person."""

    surface.accounts.groups["eng"].add("alice")
    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")
    row = surface.agent_sessions.seed(account_id="acct-alice")
    now = datetime.now(UTC)
    candidate = SimpleNamespace(
        session=SimpleNamespace(
            id=row.id, account_id=row.account_id, title=None, created_at=now
        ),
        last_activity=now,
    )

    async def purgeable(cutoff, *, account_id=None):
        return [candidate]

    async def count_for_sessions(ids):
        return 0

    monkeypatch.setattr(surface.agent_sessions, "purgeable", purgeable, raising=False)
    monkeypatch.setattr(surface.events, "count_for_sessions", count_for_sessions, raising=False)

    response = client.post("/admin/sessions/purge/plan", json={"older_than_seconds": 60})
    assert response.status_code == 200, response.text
    assert [entry["account"] for entry in response.json()["sessions"]] == ["alice"]


def test_the_admin_session_list_names_the_account(monkeypatch, surface, client):
    """The operator reads a name, not the store's key, filtered or not."""

    surface.accounts.groups["eng"].add("alice")
    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")
    surface.agent_sessions.seed(account_id="acct-alice")

    for path in ("/admin/sessions", "/admin/sessions?account=alice"):
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert [row["account"] for row in response.json()["sessions"]] == ["alice"]


def test_the_admin_event_window_reads_a_window_and_not_the_log(monkeypatch, surface, client):
    """A press on `read` costs the page, not the session.

    The route used to fetch every row of the session and slice a page out of
    the list. On a session shape a real one reaches — 12,300 rows, 13 MB of
    payload — that measured 480ms against 69ms for the same window resolved in
    SQL, and every one of those rows was decoded and thrown away. The fake
    records each whole-log read, so a route that goes back to one fails here
    instead of getting slower without a symptom.
    """

    surface.accounts.groups["eng"].add("alice")
    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    row = surface.agent_sessions.seed(account_id="acct-one", session_id="e" * 32)
    _seed_events(surface, row.id, chunks=100)
    surface.events.whole_log_reads.clear()

    response = client.get(f"/admin/sessions/{row.id}?view=events&limit=2")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_events"] == 100, "the page stopped saying how much it was not showing"
    assert body["returned"] == 2
    assert [event["seq"] for event in body["events"]] == [1, 2]
    assert body["next_after"] == 2, "the page no longer says where to resume"
    assert surface.events.whole_log_reads == [], "the route read the whole log to show two rows"


def test_the_admin_tail_is_the_newest_of_the_kind_asked_for(monkeypatch, surface, client):
    """`last` with a kind filter is how a failed turn's reason is found.

    The filter has to be applied *before* the limit, or a tail of two tool
    results returns two streamed chunks and the diagnosis is empty. That
    ordering is a property of the query now, not of a Python slice, so it is
    worth pinning where the query is built.
    """

    surface.accounts.groups["eng"].add("alice")
    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    row = surface.agent_sessions.seed(account_id="acct-one", session_id="f" * 32)
    _seed_events(surface, row.id, chunks=20, results=3)

    response = client.get(f"/admin/sessions/{row.id}?view=events&last=2&kind=tool_result")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_events"] == 3, "the count ignored the kind filter"
    assert [event["kind"] for event in body["events"]] == ["tool_result", "tool_result"]
    # The newest two of the three, in seq order — read descending, printed ascending.
    assert [event["seq"] for event in body["events"]] == [22, 23]
    assert "next_after" not in body, "a tail that reached the end still says to resume"


def test_one_event_is_served_whole_from_its_seq(monkeypatch, surface, client):
    """`view=event` is the end of a diagnosis, so it is never a fragment."""

    surface.accounts.groups["eng"].add("alice")
    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    row = surface.agent_sessions.seed(account_id="acct-one", session_id="a" * 32)
    big = "x" * 20_000
    asyncio.run(surface.events.append(branch_id=row.id, kind="tool_result", payload={"o": big}))

    response = client.get(f"/admin/sessions/{row.id}?view=event&seq=1")

    assert response.status_code == 200, response.text
    assert response.json()["payload"]["o"] == big, "one event was clipped like a window"

    missing = client.get(f"/admin/sessions/{row.id}?view=event&seq=99")
    assert missing.status_code == 200
    assert missing.json()["error"] == "no event with seq 99"


def test_a_prefix_that_is_not_an_id_is_simply_not_found(monkeypatch, surface, client):
    """Ids are `uuid4().hex`, so a non-hex prefix cannot name one.

    Answered without asking the database, which is also what keeps `LIKE`'s
    wildcards out of the query rather than escaped inside it: `%` would
    otherwise have matched every session on the appliance.
    """

    surface.accounts.groups["eng"].add("alice")
    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    surface.agent_sessions.seed(account_id="acct-one", session_id="d" * 32)

    assert client.get("/admin/sessions/%?view=kinds").status_code == 404
    assert client.get("/admin/sessions/not-hex?view=kinds").status_code == 404


# -- the interface preferences -------------------------------------------------
#
# The palette, the reasoning default, and how long a session stays open after it
# is last used — all of which belong to the account rather than to the browser
# (D53). Request-shape cases only, the same arrangement `auto-accept` uses above:
# the surface harness fakes its connection, so a test that asserted the write
# landed would only prove the fake agrees with the route.


def test_the_identity_response_carries_the_interface_preferences(surface, client):
    """The client needs them before first paint, so they ride with the identity.

    Both routes, because a field present on one and absent from the other gives a
    preference that applies on sign-in and reverts on reload — the failure
    `_preferences` is one helper to prevent.
    """

    authenticate(surface, client)

    prefs = {"palette": "dark", "collapse_reasoning": False, "active_minutes": 30}

    me = client.get("/api/me")
    assert me.status_code == 200, me.text
    assert me.json()["interface"] == prefs

    login = client.post("/api/login", json={"username": "alice", "password": PASSWORD})
    assert login.status_code == 200, login.text
    assert login.json()["interface"] == prefs


def test_an_unknown_palette_is_a_bad_request(surface, client):
    """Refused by name, with the known set, before SQL sees it.

    The column has a CHECK constraint that would also catch this, but it arrives
    as a 500 describing a database — and the client that sent the value deserves
    to be told which field was wrong and what was allowed.
    """

    csrf = authenticate(surface, client)

    response = client.patch(
        "/api/me/interface",
        json={"palette": "solarized"},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 400, response.text
    assert response.json()["known"] == [
        "dark",
        "light",
        "nord",
        "everforest-dark",
        "everforest-light",
        "gruvbox-dark",
        "gruvbox-light",
    ]


def test_a_preference_write_needs_the_csrf_token(surface, client):
    """State-changing, so it carries the guard every other such route carries."""

    authenticate(surface, client)

    response = client.patch("/api/me/interface", json={"palette": "nord"})
    assert response.status_code in (401, 403), response.text


def test_one_preference_changes_without_disturbing_the_others(surface, client):
    """PATCH semantics: absent means unchanged, not "set to the default".

    The reason this is a PATCH and not a PUT. Two tabs are ordinary here — the
    settings dialog is reachable from any of them — and a whole-record write
    would let the palette picker in one revert a reasoning checkbox changed in
    the other, or reset the working-set window from a tab nobody touched it in.
    """

    csrf = authenticate(surface, client)
    headers = {"x-csrf-token": csrf}

    first = client.patch("/api/me/interface", json={"palette": "nord"}, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json() == {
        "palette": "nord",
        "collapse_reasoning": False,
        "active_minutes": 30,
    }

    second = client.patch(
        "/api/me/interface", json={"collapse_reasoning": True}, headers=headers
    )
    assert second.status_code == 200, second.text
    # The palette survived a write that did not mention it.
    assert second.json() == {
        "palette": "nord",
        "collapse_reasoning": True,
        "active_minutes": 30,
    }

    third = client.patch("/api/me/interface", json={"active_minutes": 0}, headers=headers)
    assert third.status_code == 200, third.text
    # And so did both of the earlier ones.
    assert third.json() == {
        "palette": "nord",
        "collapse_reasoning": True,
        "active_minutes": 0,
    }


def test_a_non_boolean_reasoning_flag_is_refused(surface, client):
    """`"true"` is not `true`, and a truthy string would silently become one.

    Worth asserting because the column is a BOOLEAN: a string arriving here
    would either be coerced by the driver or raise from inside it, and neither is
    a 400 naming the field.
    """

    csrf = authenticate(surface, client)

    response = client.patch(
        "/api/me/interface",
        json={"collapse_reasoning": "yes"},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"] == "invalid_collapse_reasoning"


def test_the_working_set_window_is_a_bounded_whole_number(surface, client):
    """Minutes, from 0 (keep nothing) to a day, and both ends are real answers.

    The type is checked by name because `bool` is an `int` in Python: `true`
    would otherwise become a one-minute window, which is the kind of silent
    coercion the reasoning flag's test exists for. The bound is checked here
    rather than left to the column's CHECK so the refusal can name it.
    """

    csrf = authenticate(surface, client)
    headers = {"x-csrf-token": csrf}

    for value in (True, "30", 1441, -1, 2.5):
        response = client.patch(
            "/api/me/interface", json={"active_minutes": value}, headers=headers
        )
        assert response.status_code == 400, (value, response.text)
        assert response.json()["error"] == "invalid_active_minutes", value

    # The ends of the range are accepted, and the reply is the whole preference
    # set rather than the field alone — the client applies it as one object.
    for value in (0, 1440):
        response = client.patch(
            "/api/me/interface", json={"active_minutes": value}, headers=headers
        )
        assert response.status_code == 200, (value, response.text)
        assert response.json()["active_minutes"] == value


# -- the account's own instructions -------------------------------------------
#
# The middle prompt tier (D65). Request shapes here, the same arrangement the
# palette uses above: the surface harness fakes its connection, so a test that
# asserted the row's contents would only prove the fake agrees with the route.
# The turn path that actually carries the text is asserted in
# `test_ws_surface.py`.


def test_the_identity_response_carries_the_accounts_instructions(surface, client):
    """Both routes, for the reason the interface preferences are on both.

    A field present on one and absent from the other gives instructions that
    apply on sign-in and vanish on reload — the failure `_preferences` is one
    helper to prevent.
    """

    authenticate(surface, client)
    surface.accounts.rows["alice"].instructions = "Keep replies short."

    me = client.get("/api/me")
    assert me.status_code == 200, me.text
    assert me.json()["instructions"] == "Keep replies short."

    login = client.post("/api/login", json={"username": "alice", "password": PASSWORD})
    assert login.status_code == 200, login.text
    assert login.json()["instructions"] == "Keep replies short."


def test_the_accounts_instructions_are_stored_and_returned(surface, client):
    """A whole-value write: what the textarea sent is what the next turn reads."""

    csrf = authenticate(surface, client)

    response = client.patch(
        "/api/me/instructions",
        json={"instructions": "Be brief."},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"instructions": "Be brief."}
    assert client.get("/api/me").json()["instructions"] == "Be brief."


def test_an_empty_string_clears_the_accounts_instructions(surface, client):
    """Clearing is a real value, not a missing one.

    The column is NOT NULL for this reason: a user who deletes the text has said
    something, and a reader that could not tell "no preferences" from "never
    asked" would be the ambiguity the column's default exists to avoid.
    """

    csrf = authenticate(surface, client)
    headers = {"x-csrf-token": csrf}

    client.patch("/api/me/instructions", json={"instructions": "Be brief."}, headers=headers)
    cleared = client.patch("/api/me/instructions", json={"instructions": ""}, headers=headers)

    assert cleared.status_code == 200, cleared.text
    assert cleared.json() == {"instructions": ""}
    assert client.get("/api/me").json()["instructions"] == ""


def test_instructions_over_the_ceiling_are_refused_with_the_limit(surface, client):
    """Refused by name and with the number, before the CHECK can see it.

    The column has a constraint that would also catch this, but it arrives as a
    500 describing a database — and the client that sent the text deserves to be
    told how much of it was too much.
    """

    csrf = authenticate(surface, client)

    response = client.patch(
        "/api/me/instructions",
        json={"instructions": "x" * 2001},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 400, response.text
    assert response.json() == {"error": "instructions_too_long", "limit": 2000}


def test_a_non_string_instruction_is_refused(surface, client):
    csrf = authenticate(surface, client)

    response = client.patch(
        "/api/me/instructions",
        json={"instructions": ["be brief"]},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"] == "invalid_instructions"


def test_an_instruction_write_needs_the_csrf_token(surface, client):
    """State-changing, so it carries the guard every other such route carries."""

    authenticate(surface, client)

    response = client.patch("/api/me/instructions", json={"instructions": "Be brief."})
    assert response.status_code in (401, 403), response.text


def test_a_nul_in_the_instructions_is_a_bad_request(surface, client):
    """Postgres TEXT cannot hold a NUL, so without the check this is a 500."""

    csrf = authenticate(surface, client)

    response = client.patch(
        "/api/me/instructions",
        json={"instructions": "a\u0000b"},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"] == "invalid_instructions"


def test_the_approval_picker_offers_exactly_the_server_modes():
    """Three colours, three modes, and the same three the server accepts.

    The client hard-codes the list to colour and describe it, so it is a second
    copy of `AUTO_ACCEPT_MODES` — and a mode added to the server and not the UI
    is one nobody can select. Compared against the real tuple rather than a
    literal, so adding a fourth mode fails here rather than shipping a client
    that cannot reach it.
    """

    import re
    from pathlib import Path

    from neuman_server.decision.policy.fragments import AUTO_ACCEPT_MODES

    ui = Path(__file__).resolve().parent.parent.parent / "neuman-client" / "web" / "src"
    script = (ui / "neuman.js").read_text()

    block = re.search(r"const APPROVAL_MODES = \[(.*?)\n\];", script, re.S)
    assert block, "the client's mode list is not where this test expects it"
    offered = re.findall(r'mode:\s*"([a-z]+)"', block.group(1))
    assert tuple(offered) == AUTO_ACCEPT_MODES, (
        f"client offers {offered}, server accepts {list(AUTO_ACCEPT_MODES)}"
    )


def test_a_backup_will_not_write_over_an_existing_destination(
    monkeypatch, surface, client, tmp_path
):
    """409, and nothing touched.

    `create_backup` refuses a destination that already holds something, which
    is what makes a retry after a slow attempt safe (N166). The mapping from
    that refusal to a status code lives in this route, so it is checked here:
    a 500 would read as "the appliance is broken" for a request the operator
    can fix by naming another destination.
    """

    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    destination = tmp_path / "backup"
    destination.mkdir()
    (destination / "neuman.dump").write_bytes(b"the first attempt")

    response = client.post("/admin/backup", json={"destination": str(destination)})

    assert response.status_code == 409, response.text
    assert response.json()["error"] == "destination_in_use"
    assert (destination / "neuman.dump").read_bytes() == b"the first attempt"


def test_a_load_without_room_is_refused_and_names_what_holds_the_memory(monkeypatch, tmp_path):
    """Measured need against the sampler's free memory; the load is never asked for."""

    runtime = FakeRuntime(
        {
            "problem": None,
            "chat": [
                {"name": "qwen-here", "loaded": False, "state": "unloaded"},
                {"name": "other-model", "loaded": True, "state": "loaded"},
            ],
        }
    )
    baseline = _local_baseline()
    baseline["models"]["qwen-here"]["local"].update(gpu_layers=99, speculation="draft-mtp")
    surface = build_surface(monkeypatch, tmp_path, baseline=baseline, runtime=runtime)
    surface.services.settings.gpu_sample_path.write_text(
        json.dumps(
            {"available": True, "devices": [{"memory_total_mb": 24576, "memory_used_mb": 3072}]}
        ),
        encoding="utf-8",
    )
    with surface.client() as client:
        csrf = authenticate(surface, client, "carol")
        response = client.post(
            "/api/models/qwen-here/load", json={}, headers={"x-csrf-token": csrf}
        )
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert "isn't room on the GPU for qwen-here" in detail, detail
        assert "needs 22.2 GiB and 21.0 GiB is free" in detail, detail
        assert "Unload other-model first" in detail, detail
        assert runtime.calls == []

        # A compacted cache is not held to the measurement it changed.
        surface.services.baseline["models"]["qwen-here"]["local"]["kv_cache"] = "q8_0"
        response = client.post(
            "/api/models/qwen-here/load", json={}, headers={"x-csrf-token": csrf}
        )
        assert response.status_code == 200, response.text
        runtime.calls.clear()

        # Nor is a window lowered to fit.
        surface.services.baseline["models"]["qwen-here"]["local"]["kv_cache"] = "f16"
        surface.services.baseline["models"]["qwen-here"]["context_tokens"] = 32768
        response = client.post(
            "/api/models/qwen-here/load", json={}, headers={"x-csrf-token": csrf}
        )
        assert response.status_code == 200, response.text
    assert runtime.calls == [("load", "qwen-here")]


def test_the_panel_waits_for_a_newer_gpu_sample_rather_than_polling(monkeypatch, tmp_path):
    """The bar lagged two five-second clocks. A held request answers on the change."""

    import threading
    import time

    surface = build_surface(monkeypatch, tmp_path, baseline=_local_baseline())
    sample = surface.services.settings.gpu_sample_path

    def write(sampled_at, used):
        sample.write_text(
            json.dumps(
                {
                    "available": True,
                    "sampled_at": sampled_at,
                    "devices": [{"memory_total_mb": 24576, "memory_used_mb": used}],
                }
            ),
            encoding="utf-8",
        )

    write(100.0, 900)
    with surface.client() as client:
        authenticate(surface, client, "carol")

        # Newer than what the panel has: answered at once.
        started = time.monotonic()
        body = client.get("/api/models/state?after=50").json()
        assert body["gpu"]["sampled_at"] == 100.0
        assert time.monotonic() - started < 0.5

        # Not newer: held until the runtime host writes the next sample.
        threading.Timer(0.6, write, args=(101.0, 23000)).start()
        started = time.monotonic()
        body = client.get("/api/models/state?after=100").json()
        waited = time.monotonic() - started
        assert body["gpu"]["sampled_at"] == 101.0
        assert body["gpu"]["devices"][0]["memory_used_mb"] == 23000
        assert 0.5 < waited < 2.0, waited

        # Nothing changes: answered when the hold runs out, with the same sample.
        from neuman_server.composition import waiting

        monkeypatch.setattr(waiting, "_HOLD_S", 0.3)
        assert client.get("/api/models/state?after=101").json()["gpu"]["sampled_at"] == 101.0

        # No sample at all: answered at once, so the panel falls back to a slow poll
        # rather than holding requests nothing will answer.
        sample.unlink()
        started = time.monotonic()
        client.get("/api/models/state?after=101")
        assert time.monotonic() - started < 0.5


def _admin_with_a_granted_group(monkeypatch, surface, client):
    surface.accounts.groups["neuman-admin"].add("alice")
    surface.accounts.groups["eng"] = {"alice"}
    surface.services.baseline["roles"] = {"open": {"network": "any"}}
    as_peer(monkeypatch, client, 1001, "alice")
    granted = client.post("/admin/grants", json={"group": "eng", "role": "open"})
    assert granted.status_code == 200, granted.text


def test_deleting_a_group_takes_its_members_and_grants(monkeypatch, surface, client):
    """D85: a leftover grant would reach whoever a later group of that name holds."""

    _admin_with_a_granted_group(monkeypatch, surface, client)

    response = client.delete("/admin/groups/eng")

    assert response.status_code == 200, response.text
    assert "eng" not in [g["name"] for g in client.get("/admin/groups").json()["groups"]]
    assert client.get("/admin/grants").json()["grants"] == []


def test_the_administrator_group_cannot_be_deleted(monkeypatch, surface, client):
    _admin_with_a_granted_group(monkeypatch, surface, client)

    response = client.delete("/admin/groups/neuman-admin")

    assert response.status_code == 400, response.text
    assert response.json()["error"] == "builtin_group"
    assert "alice" in surface.accounts.groups["neuman-admin"]


def test_deleting_a_group_that_does_not_exist_is_not_found(monkeypatch, surface, client):
    _admin_with_a_granted_group(monkeypatch, surface, client)

    assert client.delete("/admin/groups/nobody").status_code == 404


def test_a_new_group_does_not_land_on_an_existing_one(monkeypatch, surface, client):
    """Ensuring here would hand the new group the old one's members and roles."""

    _admin_with_a_granted_group(monkeypatch, surface, client)

    response = client.post("/admin/groups", json={"name": "eng"})

    assert response.status_code == 409, response.text
    assert response.json()["error"] == "group_exists"


def test_a_grant_creates_its_group(monkeypatch, surface, client):
    """A grant with no group row is in force for nobody and shown nowhere (D85)."""

    _admin_with_a_granted_group(monkeypatch, surface, client)

    granted = client.post("/admin/grants", json={"group": "ops", "role": "open"})

    assert granted.status_code == 200, granted.text
    assert "ops" in [g["name"] for g in client.get("/admin/groups").json()["groups"]]


def test_the_last_administrator_cannot_leave(monkeypatch, surface, client):
    """D85: every path off the group refuses to leave nobody who can sign in."""

    surface.accounts.groups["neuman-admin"] = {"alice"}
    as_peer(monkeypatch, client, 1001, "alice")

    for response in (
        client.delete("/admin/groups/neuman-admin/members/alice"),
        client.patch("/admin/accounts/alice", json={"admin": False}),
        client.patch("/admin/accounts/alice", json={"disabled": True}),
    ):
        assert response.status_code == 400, response.text
        assert response.json()["error"] == "last_admin"
    assert surface.accounts.groups["neuman-admin"] == {"alice"}


def test_an_administrator_can_leave_once_another_remains(monkeypatch, surface, client):
    surface.accounts.groups["neuman-admin"] = {"alice"}
    as_peer(monkeypatch, client, 1001, "alice")
    added = client.post("/admin/groups/neuman-admin/members", json={"account": "bob"})
    assert added.status_code == 200, added.text

    response = client.delete("/admin/groups/neuman-admin/members/bob")

    assert response.status_code == 200, response.text


def test_the_baseline_comes_with_what_an_unset_field_falls_to(monkeypatch, surface, client):
    """The panel names the built-in value, so it has to be told it."""

    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")

    body = client.get("/admin/baseline").json()

    assert body["built_in"]["network"] == "none"
    assert body["built_in"]["auto_accept"] == "safe"
