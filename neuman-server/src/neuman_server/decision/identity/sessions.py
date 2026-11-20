"""Login, session lifecycle, and revocation.

The login operation is the one place authentication, account state, and
throttling meet. It is a *core operation* rather than a transport handler
precisely so no surface can perform half of it (D9).

**The account row is the identity.** There is no host directory to bind to and
no uid to compare. An unknown name, a disabled account, an account with no
password, and a wrong password all produce the same answer, and all spend the
same argon2 time: the differences are audited and logged, never returned
(D68).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ...foundation.logging import get_logger
from ..audit import chain
from ..audit.ports import AuditRepository
from . import tokens
from .ports import AccountRecord, AccountRepository, PasswordHasher, SessionRepository
from .throttle import LoginThrottle

__all__ = ["LoginResult", "Principal", "login", "logout", "resolve_session", "revoke_all"]

_log = get_logger("neuman.server.identity")

INVALID = "invalid_credentials"
THROTTLED = "throttled"


class AuthUnavailable(RuntimeError):
    """Kept as a name for the unexpected: hashing itself failed.

    A password check no longer depends on a second process, so the ordinary
    failure has no service-vs-denial ambiguity. If argon2 raises something
    other than a mismatch — a bug or a broken build — that is a 500, not a 401,
    and this is how the route tells them apart.
    """


@dataclass(frozen=True, slots=True)
class Principal:
    """Who a request is acting for.

    `active_minutes` travels with the principal because it is the account's own
    answer to "how long does a session stay in the working set" (D63), and every
    route that asks is asking about the account whose request this is.
    """

    account_id: str
    name: str
    csrf_token: str
    must_change_password: bool = False
    active_minutes: int = 30


@dataclass(frozen=True, slots=True)
class LoginResult:
    ok: bool
    reason: str = ""
    token: str | None = None
    csrf_token: str | None = None
    account: AccountRecord | None = None
    retry_after_s: float = 0.0


async def login(
    *,
    name: str,
    password: str,
    accounts: AccountRepository,
    passwords: PasswordHasher,
    sessions: SessionRepository,
    audit: AuditRepository,
    throttle: LoginThrottle,
    mirror: Any = None,
    now: datetime | None = None,
) -> LoginResult:
    """Authenticate a credential and mint a session.

    Order is deliberate: throttle, then the account lookup, then argon2. The
    lookup and the hash are both inside the same failure path so a name that
    does not exist costs what a wrong password costs.

    The name is lower-cased for lookup and for the audit entry, because names
    are login identifiers and `Alice` and `alice` must be one person.
    """

    moment = now or datetime.now(UTC)
    name = name.strip().lower()

    decision = throttle.check(name, now=moment)
    if not decision.allowed:
        _log.warning("login throttled", fields={"account": name})
        return LoginResult(False, THROTTLED, retry_after_s=decision.retry_after_s)

    account = await accounts.by_name(name)
    usable = account is not None and account.disabled_at is None and account.password_hash
    if not usable:
        # Spend the hash anyway. Returning early here is the account-enumeration
        # oracle this branch exists to avoid.
        await passwords.verify_dummy(password)
        throttle.record_failure(name, now=moment)
        await chain.append(
            audit,
            actor=None,
            action="identity.login_denied",
            payload={"account": name, "reason": INVALID},
            mirror=mirror,
        )
        return LoginResult(False, INVALID)

    assert account is not None and account.password_hash is not None
    try:
        matched = await passwords.verify(account.password_hash, password)
    except Exception as exc:
        _log.error("password verification failed", fields={"account": name, "error": str(exc)})
        raise AuthUnavailable(str(exc)) from exc

    if not matched:
        throttle.record_failure(name, now=moment)
        await chain.append(
            audit,
            actor=None,
            action="identity.login_denied",
            payload={"account": name, "reason": INVALID},
            mirror=mirror,
        )
        return LoginResult(False, INVALID)

    # A hash made under older parameters is upgraded on a successful login.
    # This is the only moment the plaintext is available to do it.
    if passwords.needs_rehash(account.password_hash):
        upgraded = await passwords.hash(password)
        await accounts.set_password(
            name=account.name,
            password_hash=upgraded,
            must_change_password=account.must_change_password,
        )

    throttle.record_success(name)

    token = tokens.new_secret()
    csrf_token = tokens.new_secret()
    await sessions.create(
        token_hash=tokens.hash_token(token),
        account_id=account.id,
        csrf_token=csrf_token,
        expires_at=tokens.expiry_from(moment),
    )

    await chain.append(
        audit,
        actor=account.name,
        action="identity.login",
        payload={"account": account.name, "method": "password"},
        mirror=mirror,
    )
    _log.info("login", fields={"account": account.name})

    return LoginResult(True, token=token, csrf_token=csrf_token, account=account)


async def resolve_session(
    token: str,
    *,
    sessions: SessionRepository,
    accounts: AccountRepository,
    now: datetime | None = None,
) -> Principal | None:
    """Turn a bearer token into a principal, or None.

    The account is re-read on every request, not just at login: disabling an
    account, or deleting it, invalidates a live session on the next request.
    That is the property D17 used to get from a name→uid lookup, now obtained
    from the row itself.
    """

    moment = now or datetime.now(UTC)
    record = await sessions.by_token_hash(tokens.hash_token(token))
    if record is None:
        return None

    if record.expires_at <= moment:
        await sessions.delete(tokens.hash_token(token))
        return None

    account = await accounts.by_id(record.account_id)
    if account is None or account.disabled_at is not None:
        # Fail closed and drop the session: a disabled account must not keep a
        # request alive on a token minted before the disable.
        if account is not None:
            await sessions.delete(tokens.hash_token(token))
        return None

    if tokens.should_touch(record.last_seen_at, now=moment):
        await sessions.touch(tokens.hash_token(token), expires_at=tokens.expiry_from(moment))

    return Principal(
        account_id=account.id,
        name=account.name,
        csrf_token=record.csrf_token,
        must_change_password=account.must_change_password,
        active_minutes=account.active_minutes,
    )


async def logout(
    token: str,
    *,
    sessions: SessionRepository,
    audit: AuditRepository,
    actor: str | None = None,
    mirror: Any = None,
) -> bool:
    """End one session."""

    removed = await sessions.delete(tokens.hash_token(token))
    if removed:
        await chain.append(
            audit, actor=actor, action="identity.logout", payload={}, mirror=mirror
        )
    return removed


async def revoke_all(
    account_id: str,
    *,
    sessions: SessionRepository,
    audit: AuditRepository,
    actor: str,
    mirror: Any = None,
) -> int:
    """End every session for an account. Returns how many were ended."""

    count = await sessions.delete_for_account(account_id)
    await chain.append(
        audit,
        actor=actor,
        action="identity.sessions_revoked",
        payload={"account_id": account_id, "count": count},
        mirror=mirror,
    )
    _log.warning("sessions revoked", fields={"account_id": account_id, "count": count})
    return count
