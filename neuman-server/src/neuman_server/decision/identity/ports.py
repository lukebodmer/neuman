"""Ports the identity domain needs.

The identity domain owns two things now: an account is a row, and a password is
verified against a hash. It no longer observes a host directory, so there is no
`Authenticate` service that can be unavailable and no name→uid binding to
resolve. What is left is small enough to read in one place (D68).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

__all__ = [
    "AccountRecord",
    "AccountRepository",
    "PasswordHasher",
    "SessionRecord",
    "SessionRepository",
]


class PasswordHasher(Protocol):
    """Credential hashing and verification.

    `verify_dummy` exists for an unknown or disabled account: the login path
    must spend the same time on a name that does not exist as on one that does,
    or the response time enumerates accounts.
    """

    async def hash(self, password: str) -> str: ...

    async def verify(self, password_hash: str, password: str) -> bool: ...

    async def verify_dummy(self, password: str) -> None: ...

    def needs_rehash(self, password_hash: str) -> bool: ...


class AccountRecord(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def password_hash(self) -> str | None: ...

    @property
    def must_change_password(self) -> bool: ...

    @property
    def disabled_at(self) -> datetime | None: ...


class AccountRepository(Protocol):
    """The account rows the decision layer reads."""

    async def by_name(self, name: str) -> AccountRecord | None: ...

    async def by_id(self, account_id: str) -> AccountRecord | None: ...

    async def set_password(
        self, *, name: str, password_hash: str, must_change_password: bool
    ) -> AccountRecord | None: ...


class SessionRecord(Protocol):
    @property
    def account_id(self) -> str: ...

    @property
    def csrf_token(self) -> str: ...

    @property
    def expires_at(self) -> datetime: ...

    @property
    def last_seen_at(self) -> datetime: ...


class SessionRepository(Protocol):
    async def create(
        self,
        *,
        token_hash: bytes,
        account_id: str,
        csrf_token: str,
        expires_at: datetime,
    ) -> SessionRecord: ...

    async def by_token_hash(self, token_hash: bytes) -> SessionRecord | None: ...

    async def touch(self, token_hash: bytes, *, expires_at: datetime) -> None: ...

    async def delete(self, token_hash: bytes) -> bool: ...

    async def delete_for_account(self, account_id: str) -> int: ...

    async def purge_expired(self, *, now: datetime) -> int: ...

    async def list_for_account(self, account_id: str) -> Sequence[SessionRecord]: ...
