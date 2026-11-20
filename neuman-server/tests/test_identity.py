"""Login, session resolution, and authorization — the fail-closed paths.

The host directory is gone: an account is a row, a password is a hash, and
disabling the row is the revocation. These tests exercise those three facts
rather than the NSS binding the old suite was built around.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeAccounts, FakeAudit, FakeDirectory, FakePasswords, FakeSessions

from neuman_server.adapters.passwords import Argon2Passwords
from neuman_server.adapters.persistence.identity import search_patterns
from neuman_server.decision.identity import (
    AuthUnavailable,
    LoginThrottle,
    login,
    resolve_session,
)
from neuman_server.decision.identity.authz import AdminPolicy, Denied, require_admin


async def _seed(accounts: FakeAccounts, name: str = "alice", password: str = "correct"):
    row = await accounts.create(
        name=name,
        password_hash=f"{FakePasswords.PREFIX}{password}",
        must_change_password=False,
    )
    assert row is not None
    return row


def _directory(accounts: FakeAccounts) -> FakeDirectory:
    return FakeDirectory(store=accounts)


async def _login(
    accounts: FakeAccounts,
    *,
    name: str = "alice",
    password: str = "correct",
    throttle: LoginThrottle | None = None,
    passwords: FakePasswords | None = None,
    sessions: FakeSessions | None = None,
    audit: FakeAudit | None = None,
):
    return await login(
        name=name,
        password=password,
        accounts=accounts,
        passwords=passwords or FakePasswords(),
        sessions=sessions or FakeSessions(),
        audit=audit or FakeAudit(),
        throttle=throttle or LoginThrottle(),
    )


# -- login ------------------------------------------------------------------


async def test_successful_login_mints_a_session_and_a_csrf_token():
    accounts = FakeAccounts()
    await _seed(accounts)
    result = await _login(accounts)
    assert result.ok
    assert result.token and result.csrf_token
    assert result.token != result.csrf_token


async def test_wrong_password_is_denied():
    accounts = FakeAccounts()
    await _seed(accounts)
    result = await _login(accounts, password="wrong")
    assert not result.ok
    assert result.reason == "invalid_credentials"


async def test_unknown_and_wrong_password_are_indistinguishable():
    """Anything else is an account-enumeration oracle."""

    accounts = FakeAccounts()
    await _seed(accounts)
    wrong = await _login(accounts, password="wrong")
    unknown = await _login(accounts, name="ghost", password="whatever")
    assert wrong.reason == unknown.reason == "invalid_credentials"


async def test_a_disabled_account_is_denied_with_the_right_password():
    accounts = FakeAccounts()
    await _seed(accounts)
    await accounts.set_disabled(name="alice", disabled=True)

    result = await _login(accounts)
    assert not result.ok
    assert result.reason == "invalid_credentials"


async def test_an_account_with_no_password_is_denied():
    """A row from before the migration has no hash; it must not be a free pass."""

    accounts = FakeAccounts()
    row = await accounts.create(name="alice", password_hash="fake:placeholder")
    assert row is not None
    row.password_hash = None

    result = await _login(accounts)
    assert not result.ok


async def test_a_broken_hasher_is_a_service_error_not_a_denial():
    class Broken(FakePasswords):
        async def verify(self, password_hash: str, password: str) -> bool:
            raise RuntimeError("argon2 exploded")

    accounts = FakeAccounts()
    await _seed(accounts)
    with pytest.raises(AuthUnavailable):
        await _login(accounts, passwords=Broken())


async def test_repeated_failures_are_throttled():
    accounts = FakeAccounts()
    await _seed(accounts)
    throttle = LoginThrottle(max_failures=3)
    for _ in range(3):
        await _login(accounts, password="wrong", throttle=throttle)

    result = await _login(accounts, throttle=throttle)
    assert not result.ok
    assert result.reason == "throttled"
    assert result.retry_after_s > 0


def test_throttle_window_slides():
    throttle = LoginThrottle(max_failures=2, window=timedelta(minutes=5))
    start = datetime(2026, 1, 1, tzinfo=UTC)
    throttle.record_failure("alice", now=start)
    throttle.record_failure("alice", now=start)

    assert not throttle.check("alice", now=start).allowed
    assert throttle.check("alice", now=start + timedelta(minutes=6)).allowed


def test_success_clears_the_throttle():
    throttle = LoginThrottle(max_failures=2)
    throttle.record_failure("alice")
    throttle.record_success("alice")
    assert throttle.check("alice").allowed


# -- argon2 -----------------------------------------------------------------


async def test_argon2_hashes_verify_and_reject():
    hasher = Argon2Passwords()
    hashed = await hasher.hash("a long enough password")
    assert hashed.startswith("$argon2id$")
    assert await hasher.verify(hashed, "a long enough password")
    assert not await hasher.verify(hashed, "the wrong password")
    assert not await hasher.verify("not a hash", "a long enough password")
    assert not hasher.needs_rehash(hashed)


# -- session resolution -----------------------------------------------------


async def test_session_resolves_to_a_principal():
    accounts, sessions = FakeAccounts(), FakeSessions()
    await _seed(accounts)
    result = await _login(accounts, sessions=sessions)

    principal = await resolve_session(result.token, sessions=sessions, accounts=accounts)
    assert principal is not None
    assert principal.name == "alice"


async def test_disabling_an_account_invalidates_a_live_session():
    accounts, sessions = FakeAccounts(), FakeSessions()
    await _seed(accounts)
    result = await _login(accounts, sessions=sessions)

    await accounts.set_disabled(name="alice", disabled=True)
    principal = await resolve_session(result.token, sessions=sessions, accounts=accounts)
    assert principal is None


async def test_expired_session_is_rejected_and_dropped():
    accounts, sessions = FakeAccounts(), FakeSessions()
    await _seed(accounts)
    result = await _login(accounts, sessions=sessions)

    principal = await resolve_session(
        result.token,
        sessions=sessions,
        accounts=accounts,
        now=datetime.now(UTC) + timedelta(hours=13),
    )
    assert principal is None
    assert not sessions.rows


# -- authorization ----------------------------------------------------------


async def test_operator_authority_comes_from_group_membership():
    accounts = FakeAccounts()
    await _seed(accounts, "alice")
    accounts.groups["neuman-admin"].add("alice")
    directory = _directory(accounts)

    assert (await require_admin("alice", directory=directory)).is_admin


async def test_non_operator_is_denied():
    accounts = FakeAccounts()
    await _seed(accounts, "bob")
    directory = _directory(accounts)

    with pytest.raises(Denied):
        await require_admin("bob", directory=directory)


async def test_a_disabled_account_loses_admin_authority():
    accounts = FakeAccounts()
    await _seed(accounts, "alice")
    accounts.groups["neuman-admin"].add("alice")
    await accounts.set_disabled(name="alice", disabled=True)

    with pytest.raises(Denied):
        await require_admin("alice", directory=_directory(accounts))


async def test_admin_groups_are_configurable():
    accounts = FakeAccounts()
    await _seed(accounts, "bob")
    accounts.groups.setdefault("users", set()).add("bob")
    policy = AdminPolicy(admin_groups=frozenset({"users"}))

    assert (await require_admin("bob", directory=_directory(accounts), policy=policy)).is_admin


async def test_wheel_alone_does_not_confer_admin():
    """The narrowing, asserted rather than assumed.

    `wheel` used to carry admin authority alongside `neuman-admin`, which meant
    anyone who could sudo was handed the governance record. Dropping it is not a
    boundary against a sysadmin — they can edit the configuration and restart the
    server — but it stops the appliance granting it to everyone with sudo on a
    machine that has several administrators.
    """

    accounts = FakeAccounts()
    await _seed(accounts, "root")
    await _seed(accounts, "ops")
    accounts.groups.setdefault("wheel", set()).add("root")
    accounts.groups["neuman-admin"].add("ops")
    directory = _directory(accounts)

    with pytest.raises(Denied, match="not an admin"):
        await require_admin("root", directory=directory)

    assert (await require_admin("ops", directory=directory)).via == "neuman-admin"


def test_a_roster_search_allows_gaps_between_its_letters():
    """D84: `jsm` finds `jsmith` and `john-smith`, and case and spaces do not matter."""

    assert search_patterns("J Sm") == ("jsm%", "%jsm%", "%j%s%m%")


def test_a_roster_search_treats_like_metacharacters_as_letters():
    """`neuman_admin` must not match `neuman-admin`, even with gaps allowed."""

    assert search_patterns("a_%") == ("a\\_\\%%", "%a\\_\\%%", "%a%\\_%\\%%")


async def test_a_page_ranks_the_closest_name_first():
    accounts = FakeAccounts()
    for name in ("adz", "bandit", "cad", "dana"):
        await _seed(accounts, name)

    total, rows = await accounts.page(query="ad", limit=50, offset=0)

    assert total == 3
    assert [row.name for row in rows] == ["adz", "cad", "bandit"]
