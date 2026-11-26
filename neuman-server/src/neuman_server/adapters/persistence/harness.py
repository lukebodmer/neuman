"""Harness repositories: the session event log, token accounting, the catalog.

Implements `decision.harness.ports.EventRepository` and `UsageRepository`, plus
the model-name resolution the harness injects.

Two things worth reading closely:

**Sequence assignment.** ``seq`` is per branch and must have no gaps and no
duplicates, because projection replays in that order. It is assigned inside the
insert from a subquery over the same branch, under a row lock on the branch, so
two concurrent appends cannot choose the same number.

**Reservations are rows, not a counter.** A call reserves its `max_tokens`
before dispatch and settles against reported usage afterwards, so an account at
199k of a 200k cap cannot overshoot by a whole completion. v1 kept an aggregate
`reserved` column and zeroed it at startup, which is correct only while calls
are single-flight; D19 targets hundreds of concurrent sessions, so they are not.
Individual rows make settle/release idempotent by id and let a reservation
orphaned by a crash be swept by age instead of by a restart.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from ...decision.harness.ports import Reservation
from ...decision.ops.sessions import BranchSummary, EventWindow

__all__ = ["SEARCHABLE_KINDS", "EventRow", "EventStore", "UsageStore", "searchable_text"]

# The event kinds that carry *conversation*, and so the only ones the session
# search index (`session_search`) reads.
#
# What a person said and what the model answered. Deliberately not `reasoning`,
# which is the model thinking aloud, and not `tool_result`, which is a file's
# contents or a directory listing — indexing those would mean typing a filename
# matched every session that had ever listed the directory containing it, and
# the sessions that actually discussed it would be buried. `tool_proposal` and
# `tool_decision` are governance records rather than speech.
SEARCHABLE_KINDS = ("user_message", "response_final")


def searchable_text(kind: str, payload: dict[str, Any]) -> str:
    """The conversational text in one event, or "" if it carries none.

    Shared by the inline append and the rebuild so the two cannot disagree about
    what a session's document contains — the drift that would make a reindex
    silently change which sessions match.

    An assistant turn's `content` only: `reasoning` sits beside it in the same
    payload for a reader (D48), and `tool_calls` are the model asking for work
    rather than saying something.
    """

    if kind == "user_message":
        text = payload.get("content")
        return text if isinstance(text, str) else ""
    if kind == "response_final":
        message = payload.get("message") or {}
        text = message.get("content") if isinstance(message, dict) else None
        return text if isinstance(text, str) else ""
    return ""


@dataclass(frozen=True, slots=True)
class EventRow:
    seq: int
    ts: datetime
    kind: str
    message_id: str | None
    payload: dict[str, Any]

    @classmethod
    def of(cls, row: dict[str, Any]) -> EventRow:
        return cls(
            seq=int(row["seq"]),
            ts=row["ts"],
            kind=row["kind"],
            message_id=row["message_id"],
            payload=row["payload"] or {},
        )


class EventStore:
    """The append-only log of one branch."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def append(
        self,
        *,
        branch_id: str,
        kind: str,
        payload: dict[str, Any],
        message_id: str | None = None,
        redactions: list[dict[str, Any]] | None = None,
    ) -> EventRow:
        from psycopg.types.json import Jsonb

        async with self._conn.transaction():
            # Serialize appends for this *branch*: the next seq must be chosen
            # under a lock or two concurrent writers pick the same one. Two
            # branches of one session append independently, which is what lets a
            # fork work while the branch it came from is still running.
            locked = await self._conn.execute(
                "SELECT session_id FROM session_branches WHERE id = %s FOR UPDATE",
                (branch_id,),
            )
            found = await locked.fetchone()
            if found is None:
                raise ValueError(f"no branch {branch_id!r}")
            session_id = found["session_id"]
            cursor = await self._conn.execute(
                """
                INSERT INTO session_events
                    (session_id, branch_id, seq, kind, message_id, payload, redactions)
                SELECT %s,
                       %s,
                       coalesce(max(seq), 0) + 1,
                       %s, %s, %s, %s
                  FROM session_events
                 WHERE branch_id = %s
                RETURNING *
                """,
                (
                    session_id,
                    branch_id,
                    kind,
                    message_id,
                    Jsonb(payload),
                    Jsonb(redactions) if redactions else None,
                    branch_id,
                ),
            )
            row = await cursor.fetchone()

            # The search index, maintained inline and inside the same
            # transaction — the shape `audit_search` uses (D8). One row per
            # *session*, so a hit is a conversation rather than a branch: the
            # rail shows sessions, and a branch is a way of reading one.
            #
            # Appended to the session's existing document rather than rebuilt
            # from the log: a conversation's history does not change, so
            # re-reading every event on every message would make the index cost
            # grow with the session's length. Two branches appending at once
            # contend on this one row for the duration of the upsert; the writes
            # are short, and moving the index to branches would put the grouping
            # back in the rail query.
            text = searchable_text(kind, payload)
            if text:
                await self._conn.execute(
                    """
                    INSERT INTO session_search (session_id, document, body)
                    VALUES (%s, to_tsvector('simple', %s), %s)
                    ON CONFLICT (session_id) DO UPDATE
                       SET document = session_search.document
                                   || to_tsvector('simple', EXCLUDED.body),
                           body = session_search.body || ' ' || EXCLUDED.body
                    """,
                    (session_id, text, text),
                )
        return EventRow.of(row)

    async def read(
        self, branch_id: str, *, kinds: Sequence[str] | None = None
    ) -> Sequence[EventRow]:
        if kinds is None:
            cursor = await self._conn.execute(
                "SELECT * FROM session_events WHERE branch_id = %s ORDER BY seq",
                (branch_id,),
            )
        else:
            # A projection reads only the kinds it can use, through the
            # `(branch_id, kind)` index. `response_chunk` is one row per streamed
            # delta and `request` is the whole history once per step; fetching
            # them to throw them away is most of the cost of opening a long
            # transcript.
            cursor = await self._conn.execute(
                """
                SELECT * FROM session_events
                 WHERE branch_id = %s AND kind = ANY(%s)
                 ORDER BY seq
                """,
                (branch_id, list(kinds)),
            )
        return [EventRow.of(row) for row in await cursor.fetchall()]

    async def head(self, branch_id: str) -> int:
        """The branch's newest seq, or 0 for a branch with no events yet.

        Read when a socket attaches to a branch, so the live stream and the
        projection are cut at the same row (D113). One indexed aggregate over
        `(branch_id, seq)`, not a read of the log.
        """

        cursor = await self._conn.execute(
            "SELECT coalesce(max(seq), 0) AS newest FROM session_events WHERE branch_id = %s",
            (branch_id,),
        )
        row = await cursor.fetchone()
        return int(row["newest"]) if row else 0

    async def window(
        self,
        branch_id: str,
        *,
        kinds: Sequence[str] | None = None,
        last: int | None = None,
        after: int = 0,
        limit: int = 50,
    ) -> EventWindow:
        """The rows a reader asked for, and the two numbers a page reports.

        Resolved in SQL rather than by slicing a full read. `read()` above pulls
        every row of a branch — 12,300 of them, 13 MB of payload, on a shape a
        real one reaches — and a windowed read that fetched all of it to show
        fifty spent ~410ms per press doing it (measured: 480ms against 69ms).
        The count and `max(seq)` are one indexed aggregate over the same
        predicate, so the totals a page prints cost a scan of the index rather
        than a transfer of the log. (N160)
        """

        where = "branch_id = %s"
        args: list[Any] = [branch_id]
        if kinds:
            where += " AND kind = ANY(%s)"
            args.append(list(kinds))

        tally = await self._conn.execute(
            f"SELECT count(*) AS n, max(seq) AS newest FROM session_events WHERE {where}",
            tuple(args),
        )
        counted = await tally.fetchone()
        # By name, like every other aggregate here: the pool's row factory is
        # `dict_row`, so a row is a mapping and unpacking it positionally yields
        # its *keys* — `total` became the string "count" and the comparison below
        # raised on `int < str` (N161).
        total = int(counted["n"]) if counted else 0
        last_seq = counted["newest"] if counted else None

        if last is not None:
            # The tail, which is where a failed turn says why. Read descending
            # so the limit cuts to the newest, then put back in seq order —
            # projection replays in order and a reversed page would read wrong.
            cursor = await self._conn.execute(
                f"SELECT * FROM session_events WHERE {where} ORDER BY seq DESC LIMIT %s",
                (*args, max(1, last)),
            )
            rows = list(reversed(await cursor.fetchall()))
        else:
            cursor = await self._conn.execute(
                f"SELECT * FROM session_events WHERE {where}"
                " AND seq > %s ORDER BY seq LIMIT %s",
                (*args, after, min(max(1, limit), 200)),
            )
            rows = await cursor.fetchall()

        return EventWindow(
            rows=[EventRow.of(row) for row in rows],
            total=total or 0,
            last_seq=last_seq,
        )

    async def summary(self, branch_id: str) -> BranchSummary:
        """What a branch holds, without reading a payload.

        The orientation `session_query` answers first: a reader who knows a
        transcript is 12,890 streamed chunks and three tool results can ask a
        question that fits the budget, and one who is shown a truncated page
        cannot.
        """

        cursor = await self._conn.execute(
            """
            SELECT kind, count(*) AS n FROM session_events
             WHERE branch_id = %s GROUP BY kind
            """,
            (branch_id,),
        )
        counts = {row["kind"]: int(row["n"]) for row in await cursor.fetchall()}

        cursor = await self._conn.execute(
            """
            SELECT min(ts) AS first_ts, max(ts) AS last_ts FROM session_events
             WHERE branch_id = %s
            """,
            (branch_id,),
        )
        span = await cursor.fetchone()

        return BranchSummary(
            total=sum(counts.values()),
            first_ts=span["first_ts"] if span else None,
            last_ts=span["last_ts"] if span else None,
            counts=counts,
        )

    async def at_seq(self, branch_id: str, seq: int) -> EventRow | None:
        """One event, by its position in the branch."""

        cursor = await self._conn.execute(
            "SELECT * FROM session_events WHERE branch_id = %s AND seq = %s",
            (branch_id, seq),
        )
        row = await cursor.fetchone()
        return EventRow.of(row) if row else None

    async def last_of_kind(self, branch_id: str, kind: str) -> EventRow | None:
        cursor = await self._conn.execute(
            """
            SELECT * FROM session_events
             WHERE branch_id = %s AND kind = %s
             ORDER BY seq DESC LIMIT 1
            """,
            (branch_id, kind),
        )
        row = await cursor.fetchone()
        return EventRow.of(row) if row else None

    async def copy_prefix(
        self,
        *,
        session_id: str,
        source_branch_id: str,
        target_branch_id: str,
        through_seq: int,
        at_seq: int | None,
        side: str | None,
        machine: str,
        from_machine: str,
    ) -> int:
        """Copy one branch's projected prefix into another; return the row count.

        The filter is the point: a fork inherits **conversation, not history**.
        User turns, final replies, tool results, plus the turn boundaries and
        compaction checkpoints a later turn or fork needs. Chunks, request
        snapshots, tool proposals and decisions, build ids and turn failures stay
        in the source: the child cannot claim an approval happened in it, and a
        copy of a 13 MB transcript does not become 26 MB.

        The marker is seq 1 and records the source, the boundary and how many
        rows were copied. It also records the machine this branch runs on and
        the one the copied prefix ran on, because a fork may continue a
        conversation on another computer (D112): the inherited events name paths
        on the old one, and the marker is where a later reader — a person, or a
        model reading its own transcript — learns where the break is.
        The copied rows follow, renumbered from 2, so a
        branch's log starts at 1 and is contiguous. `ts`, `message_id` and
        `redactions` are kept verbatim — the transcript is when it was said, not
        when it was copied.
        """

        from psycopg.types.json import Jsonb

        async with self._conn.transaction():
            counted = await self._conn.execute(
                """
                SELECT count(*) AS n
                  FROM session_events
                 WHERE branch_id = %s
                   AND seq <= %s
                   AND (
                         kind IN ('user_message', 'response_final', 'tool_result')
                         OR (
                              kind = 'harness_event'
                              AND payload ->> 'type' IN ('turn_complete', 'compaction')
                         )
                   )
                """,
                (source_branch_id, through_seq),
            )
            row = await counted.fetchone()
            copied = int(row["n"]) if row else 0
            await self._conn.execute(
                """
                INSERT INTO session_events
                    (session_id, branch_id, seq, kind, payload)
                VALUES (%s, %s, 1, 'harness_event', %s)
                """,
                (
                    session_id,
                    target_branch_id,
                    Jsonb(
                        {
                            "type": "fork",
                            "from_branch": source_branch_id,
                            "from_seq": through_seq,
                            "at_seq": at_seq,
                            "side": side,
                            "events": copied,
                            "machine": machine,
                            "from_machine": from_machine,
                        }
                    ),
                ),
            )
            await self._conn.execute(
                """
                INSERT INTO session_events
                    (session_id, branch_id, seq, ts, kind, message_id, payload, redactions)
                SELECT %s,
                       %s,
                       row_number() OVER (ORDER BY seq) + 1,
                       ts, kind, message_id,
                       -- The usage was spent in the source branch. Copying it
                       -- would make the session's token total count the same
                       -- call once per fork.
                       CASE WHEN kind = 'response_final'
                            THEN payload - 'usage'
                            ELSE payload
                       END,
                       redactions
                  FROM session_events
                 WHERE branch_id = %s
                   AND seq <= %s
                   AND (
                         kind IN ('user_message', 'response_final', 'tool_result')
                         OR (
                              kind = 'harness_event'
                              AND payload ->> 'type' IN ('turn_complete', 'compaction')
                         )
                   )
                """,
                (session_id, target_branch_id, source_branch_id, through_seq),
            )
        return copied

    async def count_for_sessions(self, session_ids: Sequence[str]) -> int:
        """How many event rows these sessions hold.

        Read before a purge so the size of what is about to go can be reported;
        afterwards the rows are gone and the question has no answer.
        """

        ids = list(session_ids)
        if not ids:
            return 0
        cursor = await self._conn.execute(
            "SELECT count(*) AS n FROM session_events WHERE session_id = ANY(%s)",
            (ids,),
        )
        row = await cursor.fetchone()
        return int(row["n"]) if row else 0

    async def tokens_for_sessions(self, session_ids: Sequence[str]) -> dict[str, int]:
        """Provider-reported tokens per session, summed over its model calls.

        Read from the `response_final` rows rather than the daily ledger: the
        ledger is per account, and "what did this conversation use" is a
        question about one session.

        The regex rather than a bare cast is not decoration. `usage` is the
        provider's JSON, stored as it arrived, so a field that is absent, null
        or a string would make the cast raise and take the whole list with it —
        after the tokens are spent, which is the shape `pricing.py` defends
        against on the money side. A call with no usable total is left out
        rather than counted as zero: missing is not free.
        """

        ids = list(session_ids)
        if not ids:
            return {}
        cursor = await self._conn.execute(
            """
            SELECT session_id,
                   sum((payload->'usage'->>'total_tokens')::bigint) AS tokens
              FROM session_events
             WHERE session_id = ANY(%s)
               AND kind = 'response_final'
               AND payload->'usage'->>'total_tokens' ~ '^[0-9]+$'
             GROUP BY session_id
            """,
            (ids,),
        )
        return {row["session_id"]: int(row["tokens"]) for row in await cursor.fetchall()}


class UsageStore:
    """Daily token accounting with reservation (docs/decisions.md D20)."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def reserve(
        self, *, account_id: str, tokens_daily: int | None, requested: int
    ) -> Reservation | None:
        """Reserve up to ``requested`` tokens, or None when the budget is spent.

        With no daily cap the reservation is still recorded, so usage reporting
        works and an unlimited account is not a blind spot.
        """

        day = datetime.now(UTC).date()

        async with self._conn.transaction():
            cursor = await self._conn.execute(
                """
                INSERT INTO usage_daily (account_id, day)
                VALUES (%s, %s)
                ON CONFLICT (account_id, day) DO UPDATE SET updated_at = now()
                RETURNING settled, reserved
                """,
                (account_id, day),
            )
            row = await cursor.fetchone()

            if tokens_daily is None:
                tokens = requested
            else:
                remaining = tokens_daily - int(row["settled"]) - int(row["reserved"])
                if remaining <= 0:
                    return None
                tokens = min(requested, remaining)

            reservation_id = uuid.uuid4().hex
            await self._conn.execute(
                """
                INSERT INTO usage_reservations (id, account_id, day, tokens)
                VALUES (%s, %s, %s, %s)
                """,
                (reservation_id, account_id, day, tokens),
            )
            await self._conn.execute(
                """
                UPDATE usage_daily
                   SET reserved = reserved + %s, updated_at = now()
                 WHERE account_id = %s AND day = %s
                """,
                (tokens, account_id, day),
            )

        return Reservation(id=reservation_id, account_id=account_id, tokens=tokens)

    async def settle(self, reservation: Reservation, used: int) -> None:
        """Convert a reservation into settled usage."""

        await self._resolve(reservation, settled=max(used, 0))

    async def release(self, reservation: Reservation) -> None:
        """Drop a reservation whose call failed or was cancelled."""

        await self._resolve(reservation, settled=0)

    async def _resolve(self, reservation: Reservation, *, settled: int) -> None:
        async with self._conn.transaction():
            cursor = await self._conn.execute(
                "DELETE FROM usage_reservations WHERE id = %s RETURNING tokens, day",
                (reservation.id,),
            )
            row = await cursor.fetchone()
            if row is None:
                # Already settled or swept. Idempotent by construction — this is
                # what the per-reservation row buys over an aggregate counter.
                return

            await self._conn.execute(
                """
                UPDATE usage_daily
                   SET reserved = greatest(reserved - %s, 0),
                       settled = settled + %s,
                       updated_at = now()
                 WHERE account_id = %s AND day = %s
                """,
                (int(row["tokens"]), settled, reservation.account_id, row["day"]),
            )

    async def sweep_stale(self, *, older_than_s: float) -> int:
        """Release reservations orphaned by a crash. Returns how many."""

        async with self._conn.transaction():
            cursor = await self._conn.execute(
                """
                DELETE FROM usage_reservations
                 WHERE created_at < now() - make_interval(secs => %s)
                RETURNING account_id, day, tokens
                """,
                (older_than_s,),
            )
            rows = await cursor.fetchall()
            for row in rows:
                await self._conn.execute(
                    """
                    UPDATE usage_daily
                       SET reserved = greatest(reserved - %s, 0), updated_at = now()
                     WHERE account_id = %s AND day = %s
                    """,
                    (int(row["tokens"]), row["account_id"], row["day"]),
                )
        return len(rows)

    async def usage_for(self, account_id: str) -> dict[str, int]:
        """Today's settled and reserved totals, for display."""

        cursor = await self._conn.execute(
            "SELECT settled, reserved FROM usage_daily WHERE account_id = %s AND day = %s",
            (account_id, datetime.now(UTC).date()),
        )
        row = await cursor.fetchone()
        if row is None:
            return {"settled": 0, "reserved": 0}
        return {"settled": int(row["settled"]), "reserved": int(row["reserved"])}

    async def all_on(self, day: date) -> list[tuple[str, int, int]]:
        """Every account's settled and reserved tokens for ``day``, heaviest first.

        An account with no row for the day is present at zero rather than
        omitted: the roster is the answer to "who is spending", and someone who
        spent nothing is a fact about them. Ordered by consumption so the first
        row is the account an operator opened this to find.
        """

        cursor = await self._conn.execute(
            """
            SELECT a.name,
                   coalesce(u.settled, 0) AS settled,
                   coalesce(u.reserved, 0) AS reserved
              FROM accounts a
              LEFT JOIN usage_daily u
                     ON u.account_id = a.id AND u.day = %s
             ORDER BY settled DESC, a.name
            """,
            (day,),
        )
        return [
            (row["name"], int(row["settled"]), int(row["reserved"]))
            for row in await cursor.fetchall()
        ]

    async def last_active(self) -> dict[str, datetime]:
        """When each account last moved a usage row, over every recorded day.

        Not from the trend window: an account whose last call was a month ago
        still made it, and a roster that says "never" because the fortnight it
        drew is empty states something false. One row per account, so this is a
        single aggregate rather than a scan of the ledger.
        """

        cursor = await self._conn.execute(
            """
            SELECT a.name, max(u.updated_at) AS updated_at
              FROM usage_daily u
              JOIN accounts a ON a.id = u.account_id
             GROUP BY a.name
            """
        )
        return {row["name"]: row["updated_at"] for row in await cursor.fetchall()}

    async def today_for(
        self, account_ids: Sequence[str], day: date
    ) -> dict[str, tuple[int, int]]:
        """Settled and reserved tokens for these accounts on ``day``.

        One query for the roster's page: the list draws a Today column for every
        row, and a query per account would make a page of fifty cost fifty
        round trips to render one number each.
        """

        ids = list(account_ids)
        if not ids:
            return {}
        cursor = await self._conn.execute(
            """
            SELECT account_id, settled, reserved
              FROM usage_daily
             WHERE account_id = ANY(%s) AND day = %s
            """,
            (ids, day),
        )
        return {
            row["account_id"]: (int(row["settled"]), int(row["reserved"]))
            for row in await cursor.fetchall()
        }

    async def history(self, since: date) -> list[tuple[str, date, int, int]]:
        """Every stored day at or after ``since``, as settled and reserved.

        Only rows that exist are returned, and an account that spent nothing on
        a day has no row — so the caller zero-fills the gap rather than
        treating it as missing data. When a row was last touched is not here:
        `last_active` answers that over the whole ledger, because a window is
        the one range that cannot.
        """

        cursor = await self._conn.execute(
            """
            SELECT a.name, u.day, u.settled, u.reserved
              FROM usage_daily u
              JOIN accounts a ON a.id = u.account_id
             WHERE u.day >= %s
             ORDER BY a.name, u.day
            """,
            (since,),
        )
        return [
            (row["name"], row["day"], int(row["settled"]), int(row["reserved"]))
            for row in await cursor.fetchall()
        ]

    async def by_model_since(self, moment: datetime) -> list[dict[str, Any]]:
        """Tokens and spend per model since `moment`, from the responses themselves.

        From `response_final` rather than the daily ledger, which knows the
        account and not the model. Spend is what was recorded with each
        response, so a price changed today does not rewrite this morning;
        responses recorded before the model and cost were stored are counted
        under no model, with no cost.

        No index serves the time filter, deliberately: this is read when an
        operator opens the overview, not per turn, and an index for it would be a
        migration of its own (D103). Measured on a populated table before relying
        on it.
        """

        cursor = await self._conn.execute(
            """
            SELECT payload->>'model' AS model,
                   sum((payload->'usage'->>'total_tokens')::bigint) AS tokens,
                   sum((payload->>'cost_usd')::numeric) AS usd,
                   sum(CASE WHEN payload->>'cost_usd' IS NULL
                            THEN (payload->'usage'->>'total_tokens')::bigint ELSE 0 END)
                       AS unpriced,
                   bool_or(coalesce((payload->>'cost_estimated')::boolean, false))
                       AS estimated
              FROM session_events
             WHERE kind = 'response_final'
               AND ts >= %s
               AND payload->'usage'->>'total_tokens' ~ '^[0-9]+$'
             GROUP BY 1
             ORDER BY 2 DESC
            """,
            (moment,),
        )
        return [
            {
                "model": row["model"],
                "tokens": int(row["tokens"] or 0),
                "usd": float(row["usd"]) if row["usd"] is not None else None,
                "unpriced_tokens": int(row["unpriced"] or 0),
                "estimated": bool(row["estimated"]),
            }
            for row in await cursor.fetchall()
        ]
