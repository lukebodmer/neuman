"""Identity repositories: accounts, groups, and login sessions.

Implements `decision.identity.ports.AccountRepository` and `SessionRepository`.
The only place identity SQL is written.

**Accounts are owned here.** The host account stopped being the executor when
the tools moved to the client (D55), and the product's deployments have no
directory to observe (D16). A row is created by an operator, carries an argon2
hash, and is disabled rather than deleted so the audit chain keeps a name that
still means the same person. There is no uid and no name→uid conflict: the row
*is* the identity (D68).

**Groups are policy keys.** `neuman-admin` is the built-in operator group; every
other group is whatever an operator creates so a grant can name it. Membership
is checked live, which is what makes a removal land on the next policy read.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

__all__ = ["AccountRow", "AccountStore", "SessionRow", "SessionStore", "search_patterns"]


def search_patterns(query: str) -> tuple[str, str, str]:
    """The `LIKE` patterns for a roster search: starts with, contains, scattered.

    Whitespace is dropped, because a name has none and a scattered match has no
    use for a word boundary. A name can contain `_`, and a group name is the same
    shape, so the metacharacters are escaped: a search for `neuman_admin` should
    not also match `neuman-admin`. Backslash is `LIKE`'s default escape.
    """

    letters = "".join(query.lower().split())
    escaped = [
        letter.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        for letter in letters
    ]
    whole = "".join(escaped)
    return f"{whole}%", f"%{whole}%", f"%{'%'.join(escaped)}%"


@dataclass(frozen=True, slots=True)
class AccountRow:
    id: str
    name: str
    password_hash: str | None
    must_change_password: bool = False
    disabled_at: datetime | None = None
    # When this account was created and last seen. Optional because most
    # callers want the identity rather than its history, and `of` fills them
    # from the row when the query selected them.
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    # How this person wants the client to look. On the identity row because the
    # client reads them together at startup, and a palette that arrives a frame
    # after the page does is a visible flash of the wrong theme (D53).
    palette: str = "dark"
    collapse_reasoning: bool = False
    # How long this window keeps a session open after it was last used, in
    # minutes; 0 is "keep nothing".
    active_minutes: int = 30
    # The person's own standing instructions, added to every turn (D65). Empty is
    # the ordinary case and means they have written none — not a missing read.
    instructions: str = ""
    # What a new branch starts with. A name the model catalog knows and a level
    # that model declares; both validated against the live catalog when set. A
    # default that is later revoked or uninstalled is not repaired — the client
    # shows "no model" and says why, rather than quietly choosing another.
    default_model: str | None = None
    default_reasoning: str | None = None

    @classmethod
    def of(cls, row: dict[str, Any]) -> AccountRow:
        return cls(
            id=row["id"],
            name=row["name"],
            password_hash=row.get("password_hash"),
            must_change_password=row.get("must_change_password", False),
            disabled_at=row.get("disabled_at"),
            first_seen=row.get("first_seen"),
            last_seen=row.get("last_seen"),
            palette=row.get("palette", "dark"),
            collapse_reasoning=row.get("collapse_reasoning", False),
            active_minutes=row.get("active_minutes", 30),
            instructions=row.get("instructions", ""),
            default_model=row.get("default_model"),
            default_reasoning=row.get("default_reasoning"),
        )

    @property
    def disabled(self) -> bool:
        return self.disabled_at is not None


@dataclass(frozen=True, slots=True)
class SessionRow:
    account_id: str
    csrf_token: str
    expires_at: datetime
    last_seen_at: datetime

    @classmethod
    def of(cls, row: dict[str, Any]) -> SessionRow:
        return cls(
            account_id=row["account_id"],
            csrf_token=row["csrf_token"],
            expires_at=row["expires_at"],
            last_seen_at=row["last_seen_at"],
        )


class AccountStore:
    """Neuman's accounts, their groups, and their preferences."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    # -- reads ---------------------------------------------------------------

    async def by_name(self, name: str) -> AccountRow | None:
        cursor = await self._conn.execute(
            "SELECT * FROM accounts WHERE lower(name) = lower(%s)", (name,)
        )
        row = await cursor.fetchone()
        return AccountRow.of(row) if row else None

    async def by_id(self, account_id: str) -> AccountRow | None:
        cursor = await self._conn.execute("SELECT * FROM accounts WHERE id = %s", (account_id,))
        row = await cursor.fetchone()
        return AccountRow.of(row) if row else None

    async def names_for(self, account_ids: Sequence[str]) -> dict[str, str]:
        """Each id's account name, in one query for a table's whole page."""

        cursor = await self._conn.execute(
            "SELECT id, name FROM accounts WHERE id = ANY(%s)", (list(account_ids),)
        )
        return {row["id"]: row["name"] for row in await cursor.fetchall()}

    async def list_all(self) -> Sequence[AccountRow]:
        """Every account, oldest first.

        Disabled accounts are included: an operator managing the appliance
        needs to see who was removed and when, and the audit chain keeps the
        name anyway.
        """

        cursor = await self._conn.execute("SELECT * FROM accounts ORDER BY first_seen")
        return [AccountRow.of(row) for row in await cursor.fetchall()]

    async def page(
        self, *, query: str, limit: int, offset: int
    ) -> tuple[int, Sequence[AccountRow]]:
        """One page of accounts, optionally filtered by name or group.

        Paged rather than whole: an appliance can carry hundreds of accounts,
        and a search that filtered a page already fetched would fail to find
        the person the operator named. Group names are searched too, so typing
        a group lists its members — the same reason the table shows groups.

        The match is fuzzy (D84): the query's letters in order, gaps allowed. A
        name that starts with the query comes first, then one that contains it,
        then a scattered match, then a group's member, because the looser a
        match is the less likely it is the person meant.

        Ordered by name within that, and by name alone with no query, rather
        than `first_seen`: this is a roster an operator scans for a person, not
        the order accounts arrived in.
        """

        where = ""
        order = "a.name"
        params: list[Any] = []
        ranks: list[Any] = []
        if query:
            prefix, contains, scattered = search_patterns(query)
            where = """
             WHERE lower(a.name) LIKE %s
                OR EXISTS (
                     SELECT 1
                       FROM group_members m
                       JOIN groups g ON g.id = m.group_id
                      WHERE m.account_id = a.id AND lower(g.name) LIKE %s
                   )
            """
            order = """
                CASE WHEN lower(a.name) LIKE %s THEN 0
                     WHEN lower(a.name) LIKE %s THEN 1
                     WHEN lower(a.name) LIKE %s THEN 2
                     ELSE 3
                END, a.name
            """
            params = [scattered, scattered]
            ranks = [prefix, contains, scattered]

        cursor = await self._conn.execute(
            f"SELECT count(*) AS n FROM accounts a {where}", tuple(params)
        )
        row = await cursor.fetchone()
        total = int(row["n"]) if row else 0

        cursor = await self._conn.execute(
            f"SELECT a.* FROM accounts a {where} ORDER BY {order} LIMIT %s OFFSET %s",
            (*params, *ranks, limit, offset),
        )
        return total, [AccountRow.of(row) for row in await cursor.fetchall()]

    async def groups_of(self, name: str) -> frozenset[str]:
        """Every group this account belongs to, or empty for a disabled one.

        Disabled is checked here rather than only at the session layer: policy
        composition asks this on every step, so disabling an account takes its
        grants away even while a turn is running.
        """

        cursor = await self._conn.execute(
            """
            SELECT g.name
              FROM groups g
              JOIN group_members m ON m.group_id = g.id
              JOIN accounts a ON a.id = m.account_id
             WHERE lower(a.name) = lower(%s)
               AND a.disabled_at IS NULL
             ORDER BY g.name
            """,
            (name,),
        )
        return frozenset(row["name"] for row in await cursor.fetchall())

    async def groups_for(self, account_ids: Sequence[str]) -> dict[str, frozenset[str]]:
        """Every group each account belongs to, in one query.

        For display, so membership is reported even for a disabled account: an
        operator deciding whether to re-enable someone needs to see what they
        were in. `groups_of` stays the enforcement answer, and it still drops
        a disabled account's groups so policy cannot grant anything through a
        membership that has been switched off.
        """

        ids = list(account_ids)
        if not ids:
            return {}
        cursor = await self._conn.execute(
            """
            SELECT m.account_id, g.name
              FROM group_members m
              JOIN groups g ON g.id = m.group_id
             WHERE m.account_id = ANY(%s)
             ORDER BY g.name
            """,
            (ids,),
        )
        grouped: dict[str, set[str]] = {}
        for row in await cursor.fetchall():
            grouped.setdefault(row["account_id"], set()).add(row["name"])
        return {account_id: frozenset(names) for account_id, names in grouped.items()}

    async def has_admin(self) -> bool:
        cursor = await self._conn.execute(
            """
            SELECT 1
              FROM groups g
              JOIN group_members m ON m.group_id = g.id
              JOIN accounts a ON a.id = m.account_id
             WHERE g.name = 'neuman-admin'
               AND a.disabled_at IS NULL
             LIMIT 1
            """
        )
        return await cursor.fetchone() is not None

    # -- writes --------------------------------------------------------------

    async def create(
        self,
        *,
        name: str,
        password_hash: str,
        must_change_password: bool = True,
    ) -> AccountRow | None:
        """Create an account. None when the name is already taken."""

        cursor = await self._conn.execute(
            """
            INSERT INTO accounts (id, name, password_hash, must_change_password)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING *
            """,
            (uuid.uuid4().hex, name, password_hash, must_change_password),
        )
        row = await cursor.fetchone()
        return AccountRow.of(row) if row else None

    async def set_password(
        self,
        *,
        name: str,
        password_hash: str,
        must_change_password: bool,
    ) -> AccountRow | None:
        cursor = await self._conn.execute(
            """
            UPDATE accounts
               SET password_hash = %s, must_change_password = %s,
                   disabled_at = NULL
             WHERE lower(name) = lower(%s)
            RETURNING *
            """,
            (password_hash, must_change_password, name),
        )
        row = await cursor.fetchone()
        return AccountRow.of(row) if row else None

    async def set_disabled(self, *, name: str, disabled: bool) -> AccountRow | None:
        cursor = await self._conn.execute(
            """
            UPDATE accounts
               SET disabled_at = CASE WHEN %s THEN now() ELSE NULL END
             WHERE lower(name) = lower(%s)
            RETURNING *
            """,
            (disabled, name),
        )
        row = await cursor.fetchone()
        return AccountRow.of(row) if row else None

    async def set_must_change_password(self, *, name: str, required: bool) -> AccountRow | None:
        cursor = await self._conn.execute(
            """
            UPDATE accounts SET must_change_password = %s
             WHERE lower(name) = lower(%s)
            RETURNING *
            """,
            (required, name),
        )
        row = await cursor.fetchone()
        return AccountRow.of(row) if row else None

    # -- groups --------------------------------------------------------------

    async def ensure_group(self, name: str) -> str:
        cursor = await self._conn.execute(
            """
            INSERT INTO groups (id, name) VALUES (%s, %s)
            ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            (uuid.uuid4().hex, name),
        )
        row = await cursor.fetchone()
        assert row is not None
        return row["id"]

    async def list_groups(self) -> Sequence[str]:
        cursor = await self._conn.execute("SELECT name FROM groups ORDER BY name")
        return [row["name"] for row in await cursor.fetchall()]

    async def group_exists(self, name: str) -> bool:
        cursor = await self._conn.execute("SELECT 1 FROM groups WHERE name = %s", (name,))
        return await cursor.fetchone() is not None

    async def delete_group(self, name: str) -> bool:
        """Delete a group; its memberships go with it, by cascade.

        Its grants do not: `policy_grants` names a group rather than referencing
        it, so the caller revokes them first (D85).
        """

        cursor = await self._conn.execute("DELETE FROM groups WHERE name = %s", (name,))
        return cursor.rowcount > 0

    async def add_member(self, *, group: str, name: str) -> bool:
        """Add an account to a group, creating the group if it does not exist.

        Returns False when the account does not exist. A name that is not an
        account is not a member: there is no host directory to author ahead of.
        """

        group_id = await self.ensure_group(group)
        cursor = await self._conn.execute(
            """
            INSERT INTO group_members (group_id, account_id)
            SELECT %s, id FROM accounts WHERE lower(name) = lower(%s)
            ON CONFLICT DO NOTHING
            RETURNING account_id
            """,
            (group_id, name),
        )
        return await cursor.fetchone() is not None

    async def remove_member(self, *, group: str, name: str) -> bool:
        cursor = await self._conn.execute(
            """
            DELETE FROM group_members m
             USING groups g, accounts a
             WHERE m.group_id = g.id AND m.account_id = a.id
               AND g.name = %s AND lower(a.name) = lower(%s)
            """,
            (group, name),
        )
        return cursor.rowcount > 0

    async def members(self, group: str) -> Sequence[AccountRow]:
        cursor = await self._conn.execute(
            """
            SELECT a.*
              FROM accounts a
              JOIN group_members m ON m.account_id = a.id
              JOIN groups g ON g.id = m.group_id
             WHERE g.name = %s
             ORDER BY a.name
            """,
            (group,),
        )
        return [AccountRow.of(row) for row in await cursor.fetchall()]

    # -- preferences ---------------------------------------------------------

    # The palettes the stylesheet defines. Here as well as in the CHECK
    # constraint because a 400 naming the valid set is a better answer than a
    # constraint violation surfacing as a 500, and because this is the list the
    # route validates against before it ever reaches SQL. The constraint is the
    # backstop, not the gate — adding a theme means touching the stylesheet, the
    # client's list and picker, this tuple, and a new migration; a test asserts
    # the sets agree.
    PALETTES = (
        "dark",
        "light",
        "nord",
        "everforest-dark",
        "everforest-light",
        "gruvbox-dark",
        "gruvbox-light",
    )

    # The longest window the setting offers, in minutes. Here as well as in the
    # column's CHECK for the reason `PALETTES` is: the route reports the bound
    # when it refuses, which is a better answer than a constraint violation
    # surfacing as a 500, and the CHECK is the backstop rather than the gate.
    ACTIVE_MINUTES_MAX = 1440

    async def set_interface(
        self,
        *,
        name: str,
        palette: str | None = None,
        collapse_reasoning: bool | None = None,
        active_minutes: int | None = None,
    ) -> AccountRow | None:
        """Update one account's client preferences, leaving unnamed ones alone.

        A partial update rather than a whole-record write: the client PATCHes one
        control at a time, and a full replace would make the palette picker and
        the reasoning checkbox each capable of reverting the other if two tabs
        were open. `None` means "not mentioned", which is why neither field can
        be set to NULL — the columns are `NOT NULL` and the absence of a value
        here is already carrying a different meaning.

        Returns None if the account does not exist.
        """

        if palette is not None and palette not in self.PALETTES:
            raise ValueError(f"unknown palette: {palette!r}")
        if active_minutes is not None and not 0 <= active_minutes <= self.ACTIVE_MINUTES_MAX:
            raise ValueError(f"active minutes outside 0..{self.ACTIVE_MINUTES_MAX}")

        sets: list[str] = []
        args: list[Any] = []
        if palette is not None:
            sets.append("palette = %s")
            args.append(palette)
        if collapse_reasoning is not None:
            sets.append("collapse_reasoning = %s")
            args.append(collapse_reasoning)
        if active_minutes is not None:
            sets.append("active_minutes = %s")
            args.append(active_minutes)
        if not sets:
            # Nothing named: report the current state rather than writing. A
            # no-op PATCH is a legitimate request and not worth a round trip to
            # an UPDATE that would set nothing.
            return await self.by_name(name)

        args.append(name)
        cursor = await self._conn.execute(
            # The interpolation is a join of literals chosen above, never input:
            # the only strings that can reach `sets` are the assignments in this
            # method. The values stay parameterised.
            f"UPDATE accounts SET {', '.join(sets)} WHERE lower(name) = lower(%s) RETURNING *",
            tuple(args),
        )
        row = await cursor.fetchone()
        return AccountRow.of(row) if row else None

    # The ceiling on `instructions`, in characters. Held here rather than in the
    # route for the reason `PALETTES` is: the column's CHECK is the backstop and
    # this is the value the route reports when it refuses, so the two must be
    # one number. The client's `maxlength` is held to it by a test.
    MAX_INSTRUCTIONS = 2000

    async def set_defaults(
        self, *, name: str, model: str | None, reasoning: str | None
    ) -> AccountRow | None:
        """Set the model and reasoning a new branch starts with.

        `None` clears both. The pair is written in one statement because they
        are one choice: a level that belongs to a different model is not a
        setting, it is a stale fragment. Whether the name and level are real is
        the route's check against the live catalog; this layer stores the
        decision.
        """

        cursor = await self._conn.execute(
            "UPDATE accounts SET default_model = %s, default_reasoning = %s"
            " WHERE lower(name) = lower(%s) RETURNING *",
            (model, reasoning, name),
        )
        row = await cursor.fetchone()
        return AccountRow.of(row) if row else None

    async def set_instructions(self, *, name: str, instructions: str) -> AccountRow | None:
        """Replace one account's standing instructions for the model."""

        if len(instructions) > self.MAX_INSTRUCTIONS:
            raise ValueError(f"instructions exceed {self.MAX_INSTRUCTIONS} characters")

        cursor = await self._conn.execute(
            "UPDATE accounts SET instructions = %s WHERE lower(name) = lower(%s) RETURNING *",
            (instructions, name),
        )
        row = await cursor.fetchone()
        return AccountRow.of(row) if row else None


class SessionStore:
    """Login sessions. Tokens are stored only as hashes."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def create(
        self,
        *,
        token_hash: bytes,
        account_id: str,
        csrf_token: str,
        expires_at: datetime,
    ) -> SessionRow:
        # A login is the account being seen, so `last_seen` moves with it — in
        # the same statement, so the two cannot disagree.
        cursor = await self._conn.execute(
            """
            WITH seen AS (
                UPDATE accounts SET last_seen = now() WHERE id = %s
            )
            INSERT INTO auth_sessions (token_hash, account_id, csrf_token, expires_at)
            VALUES (%s, %s, %s, %s)
            RETURNING *
            """,
            (account_id, token_hash, account_id, csrf_token, expires_at),
        )
        row = await cursor.fetchone()
        return SessionRow.of(row)

    async def by_token_hash(self, token_hash: bytes) -> SessionRow | None:
        cursor = await self._conn.execute(
            "SELECT * FROM auth_sessions WHERE token_hash = %s", (token_hash,)
        )
        row = await cursor.fetchone()
        return SessionRow.of(row) if row else None

    async def touch(self, token_hash: bytes, *, expires_at: datetime) -> None:
        """Extend a session, and record that its account was seen.

        `accounts.last_seen` used to be set when the row was made and never
        again, so the roster's LAST SEEN was the creation date. It moves here,
        with the session's own `last_seen_at`, at the same throttled cadence.
        """

        await self._conn.execute(
            """
            WITH touched AS (
                UPDATE auth_sessions
                   SET last_seen_at = now(), expires_at = %s
                 WHERE token_hash = %s
             RETURNING account_id
            )
            UPDATE accounts SET last_seen = now()
             WHERE id IN (SELECT account_id FROM touched)
            """,
            (expires_at, token_hash),
        )

    async def delete(self, token_hash: bytes) -> bool:
        cursor = await self._conn.execute(
            "DELETE FROM auth_sessions WHERE token_hash = %s", (token_hash,)
        )
        return cursor.rowcount > 0

    async def delete_for_account(self, account_id: str) -> int:
        cursor = await self._conn.execute(
            "DELETE FROM auth_sessions WHERE account_id = %s", (account_id,)
        )
        return cursor.rowcount

    async def purge_expired(self, *, now: datetime) -> int:
        cursor = await self._conn.execute(
            "DELETE FROM auth_sessions WHERE expires_at <= %s", (now,)
        )
        return cursor.rowcount

    async def list_for_account(self, account_id: str) -> Sequence[SessionRow]:
        cursor = await self._conn.execute(
            "SELECT * FROM auth_sessions WHERE account_id = %s ORDER BY created_at DESC",
            (account_id,),
        )
        return [SessionRow.of(row) for row in await cursor.fetchall()]
