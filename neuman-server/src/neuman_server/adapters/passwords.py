"""Argon2id password hashing.

The one place a password is turned into a stored value and back. Argon2id is the
current recommendation and is deliberately slow: the hash is the only thing
between a stolen database and a credential set, so it buys memory hardness that
a plain SHA cannot.

Verification runs in a worker thread. Argon2 is CPU- and memory-bound, and doing
it on the event loop would stall every other request for the duration of one
login — a denial of service that looks like a slow server.
"""

from __future__ import annotations

import asyncio

from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

__all__ = ["Argon2Passwords"]


class Argon2Passwords:
    """Implements `decision.identity.ports.PasswordHasher`."""

    def __init__(self) -> None:
        # Library defaults are OWASP-ish (64 MiB, 3 passes, 4 lanes). Pinning
        # them here rather than accepting a library default means a dependency
        # upgrade cannot silently lower the cost of every new hash.
        self._hasher = _Argon2(
            time_cost=3,
            memory_cost=65536,
            parallelism=4,
            hash_len=32,
            salt_len=16,
        )
        # A real hash of a value nobody knows. Verifying against it for an
        # unknown account makes the response time the same as a wrong password
        # for a real one, which is the difference between a login and an
        # account-enumeration oracle.
        self._dummy = self._hasher.hash("neuman-no-such-account")

    async def hash(self, password: str) -> str:
        return await asyncio.to_thread(self._hasher.hash, password)

    async def verify(self, password_hash: str, password: str) -> bool:
        try:
            return await asyncio.to_thread(self._hasher.verify, password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            # A malformed or legacy hash is a failed credential, not a service
            # failure. The caller has already collapsed every denial to one
            # answer, so this must not become a 500 that distinguishes it.
            return False

    async def verify_dummy(self, password: str) -> None:
        await self.verify(self._dummy, password)

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError:
            return True
