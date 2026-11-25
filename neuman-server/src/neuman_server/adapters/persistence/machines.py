"""Machines: the named places an account's dispatches run.

The row is what a socket used to be. Before it, a client's home, drops, tools
and release lived on the connection, so they vanished when the socket did and a
`$HOME` grant expanded against whichever client happened to be connected. A
branch could be reopened on a different computer and be silently pointed at a
second filesystem, with nothing in the prompt or the record saying so.

Keeping the last hello means three things that were impossible become ordinary:

* `$HOME` and `$HOME/<path>` expand the same way whether the machine is online
  or not, so composing policy twice gives the same answer.
* An offline machine is an *answer* — "dgx is not connected" — instead of a
  hole the window has to explain as "your permissions were taken away".
* The record can name the machine a dispatch ran on, which `SPEC.md` §5 has
  promised since before there was anything to name.

A machine is scoped to an account (`(account_id, label)`), because reach differs
per person: Alice's `dgx` and Bob's `dgx` are one physical box and two machines.
That is also why the label needs no attestation: the connection is already bound
to an account by the login that produced it, so a name can only ever point at
one of that account's own places. What the record therefore holds is a claim,
like every other client-reported fact, and `docs/security-model.md` says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

__all__ = ["CLIENT", "MACHINE_LABEL", "MachineRow", "MachineStore"]

# The label's shape, from `common.schema.json#/$defs/machineLabel`. Repeated in
# code because a websocket frame and a JSON body are not schema-validated at
# runtime: the schema is what two halves agree on, and this is the check that
# runs. One copy, imported by both routes, so the two surfaces cannot disagree
# about what a machine may be called.
MACHINE_LABEL = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

# The only kind this release speaks. `ssh` is reserved in the schema and the
# CHECK on the table, so adding it is a `kind` with an implementation rather
# than a migration and a contract change.
CLIENT = "client"


@dataclass(frozen=True, slots=True)
class MachineRow:
    """One machine, as the database remembers it.

    `home` and `drops` are the last hello's answer and are nullable, which is
    not a defect: a row an operator wrote before the machine ever connected has
    never introduced itself, and a `$HOME` grant then names nothing — the same
    answer as a machine that is offline, and never the wrong one.
    """

    account_id: str
    label: str
    kind: str
    notes: str
    created_at: datetime
    last_seen_at: datetime | None
    home: str | None
    drops: str | None
    tools: tuple[str, ...]
    release: str | None
    rpc: int | None

    @classmethod
    def of(cls, row: dict[str, Any]) -> MachineRow:
        return cls(
            account_id=row["account_id"],
            label=row["label"],
            kind=row["kind"],
            notes=row["notes"],
            created_at=row["created_at"],
            last_seen_at=row.get("last_seen_at"),
            home=row.get("home"),
            drops=row.get("drops"),
            tools=tuple(row.get("tools") or ()),
            release=row.get("release"),
            rpc=row.get("rpc"),
        )


class MachineStore:
    """Machines, per account. One statement per operation."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def upsert(
        self,
        account_id: str,
        label: str,
        *,
        kind: str = CLIENT,
        home: str,
        drops: str,
        tools: tuple[str, ...] | list[str],
        release: str | None,
        rpc: int,
    ) -> MachineRow:
        """Record a hello, creating the machine the first time it introduces itself.

        Every fact here is reported by the client and none of it grants
        anything: `home` decides which directory a `$HOME` entry names, and a
        dishonest one cannot widen reach because `shell` and `python` are
        unchecked and a client wanting more would not need to lie (N145).
        """

        from psycopg.types.json import Jsonb

        cursor = await self._conn.execute(
            """
            INSERT INTO machines AS m
                (account_id, label, kind, last_seen_at, home, drops, tools, release, rpc)
            VALUES (%s, %s, %s, now(), %s, %s, %s, %s, %s)
            ON CONFLICT (account_id, label) DO UPDATE
               SET kind = EXCLUDED.kind,
                   last_seen_at = now(),
                   home = EXCLUDED.home,
                   drops = EXCLUDED.drops,
                   tools = EXCLUDED.tools,
                   release = EXCLUDED.release,
                   rpc = EXCLUDED.rpc
            RETURNING *
            """,
            (
                account_id,
                label,
                kind,
                home,
                drops,
                Jsonb(sorted(tools)),
                release,
                rpc,
            ),
        )
        return MachineRow.of(await cursor.fetchone())

    async def get(self, account_id: str, label: str) -> MachineRow | None:
        cursor = await self._conn.execute(
            "SELECT * FROM machines WHERE account_id = %s AND label = %s",
            (account_id, label),
        )
        row = await cursor.fetchone()
        return MachineRow.of(row) if row else None

    async def list_for(self, account_id: str) -> list[MachineRow]:
        cursor = await self._conn.execute(
            "SELECT * FROM machines WHERE account_id = %s ORDER BY label",
            (account_id,),
        )
        return [MachineRow.of(row) for row in await cursor.fetchall()]

    async def list_all(self) -> list[MachineRow]:
        """Every machine on the appliance, for the administration page.

        Ordered by label first because the page's question is usually "who is
        on the dgx", which is a question about a place rather than a person.
        """

        cursor = await self._conn.execute("SELECT * FROM machines ORDER BY label, account_id")
        return [MachineRow.of(row) for row in await cursor.fetchall()]

    async def set_notes(self, account_id: str, label: str, notes: str) -> bool:
        """What is installed here, in an operator's words.

        Admin-written and told to the model, because `hello` advertises Neuman
        *tools* and says nothing about the programs on `PATH`. Without it a
        model discovers a missing registration toolkit one refused turn at a
        time, on every conversation.
        """

        cursor = await self._conn.execute(
            "UPDATE machines SET notes = %s WHERE account_id = %s AND label = %s",
            (notes, account_id, label),
        )
        return cursor.rowcount > 0
