"""Opaque session tokens and their CSRF pairing.

A token is 32 random bytes handed to the client as a URL-safe string and stored
**only** as its BLAKE2b-256 hash. A database read therefore cannot be replayed
as a session: an attacker with the table has hashes, not credentials.

The CSRF token is a second, independent secret stored alongside the session. The
session token lives in an `HttpOnly` cookie the page cannot read; the CSRF token
is readable by the page and echoed in a header. An attacker who can make a
cross-site request carries the cookie but cannot read the second secret.

Comparison is constant-time wherever a caller-supplied value is involved.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

__all__ = [
    "SESSION_TTL",
    "TOUCH_INTERVAL",
    "expiry_from",
    "hash_token",
    "new_secret",
    "should_touch",
    "tokens_equal",
]

SESSION_TTL = timedelta(hours=12)

# Only extend the sliding window when the session has not been touched recently,
# so a busy session does not write on every request.
TOUCH_INTERVAL = timedelta(minutes=1)

_TOKEN_BYTES = 32


def new_secret() -> str:
    """A fresh URL-safe random secret (session token or CSRF token)."""

    return (
        base64.urlsafe_b64encode(secrets.token_bytes(_TOKEN_BYTES)).rstrip(b"=").decode("ascii")
    )


def hash_token(token: str) -> bytes:
    """BLAKE2b-256 of a token: the only form ever stored."""

    return hashlib.blake2b(token.encode("utf-8"), digest_size=32).digest()


def tokens_equal(left: str, right: str) -> bool:
    """Constant-time comparison for CSRF tokens."""

    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def expiry_from(now: datetime | None = None) -> datetime:
    """When a session minted or touched now should expire."""

    return (now or datetime.now(UTC)) + SESSION_TTL


def should_touch(last_seen_at: datetime, *, now: datetime | None = None) -> bool:
    """Whether the sliding window is stale enough to be worth a write."""

    return (now or datetime.now(UTC)) - last_seen_at >= TOUCH_INTERVAL
