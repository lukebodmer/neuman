"""Conversations and their branches.

A **session** is the row the rail shows: an owner, a title, a pin, a close, a
removal. A **branch** is one transcript inside it: an append-only event log, the
settings the next turn runs with, and where a fork lands. A session always has
at least one branch, and the root branch is created with the session.

Closed sessions are never deleted here — retention does that deliberately, on a
schedule, so that "the conversation is over" and "the record is gone" stay
separate decisions.

"The user is done looking at it" is a third decision, and `removed_at` is where
it lives. Removal hides a session and all its branches from its owner's listing
and nothing else: it does not close the conversation, so it does not start the
retention clock. A user tidying their list must not be able to schedule the
destruction of a transcript the audit chain refers to.

**Why branches instead of separate sessions.** A fork that appeared as a second
row would be a second title, a second pin and a second search hit for one
conversation; grouping it back together in the listing would make "a fork never
enters the list" a query rather than a fact. Here it is the schema: a fork adds
a `session_branches` row, and the rail reads `agent_sessions`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

__all__ = [
    "SESSION_SORTS",
    "AgentSessionRow",
    "AgentSessionStore",
    "BranchRow",
    "BranchStore",
    "PurgeCandidate",
]

# The orderings a listing may be asked for. `used` is by last activity, `created`
# by when the conversation began; both put pinned rows first. A closed set rather
# than a column name, because the value arrives from a query parameter and this
# is SQL — the store refuses anything else rather than interpolating it.
SESSION_SORTS = ("used", "created")

# "Last activity": the newest of when the session began, when it was closed, the
# newest branch's creation, and the last thing said in any of its branches.
# Written once because two queries need it and they must not disagree — the
# purge predicate (`purgeable`) asks whether a session is old enough to take, and
# the listing asks which sessions were used recently. Both alias the table `s`,
# so one string serves both.
#
# A fork's branch is created now, which lifts the session — that is the right
# reading: the user just acted on it.
_LAST_ACTIVITY = (
    "greatest(s.created_at, coalesce(s.closed_at, s.created_at), "
    "coalesce((SELECT max(b.created_at) FROM session_branches b "
    "WHERE b.session_id = s.id), s.created_at), "
    "coalesce(last.ts, s.created_at))"
)

# The newest event in a session, across its branches. By `ts` rather than `seq`
# because seq is per branch now and cannot order two branches against each other.
# The `(session_id, ts DESC)` index makes this one primary-key-range descent.
_LAST_EVENT = """
              LEFT JOIN LATERAL (
                   SELECT e.ts
                     FROM session_events e
                    WHERE e.session_id = s.id
                    ORDER BY e.ts DESC
                    LIMIT 1
              ) last ON true"""

# The branch a session opens on: the one with the greatest activity, where a
# branch's activity is its creation or its newest event. A fork is created now,
# so it opens after a fork; sending in another branch makes that one newer and
# it opens next time. A lateral join rather than a stored pointer: a pointer
# would be a second clock, and D61's rule is that the listing derives the time
# rather than recording one.
_OPEN_BRANCH = """
              LEFT JOIN LATERAL (
                   SELECT b.id,
                          greatest(b.created_at, coalesce(blast.ts, b.created_at))
                              AS activity
                     FROM session_branches b
                     LEFT JOIN LATERAL (
                          SELECT e.ts
                            FROM session_events e
                           WHERE e.branch_id = b.id
                           ORDER BY e.seq DESC
                           LIMIT 1
                     ) blast ON true
                    WHERE b.session_id = s.id
                    ORDER BY activity DESC, b.created_at DESC, b.id
                    LIMIT 1
              ) open_branch ON true"""

# How many branches a session has, for the rail's badge.
_BRANCH_COUNT = "(SELECT count(*) FROM session_branches b WHERE b.session_id = s.id)"


def _order_by(sort: str) -> str:
    """The ORDER BY for a listing, from the closed set above.

    **Pinned first, in both orders.** A pin is the reader's own statement that
    this row belongs at the top, so it outranks whichever date the list is
    sorted by — the alternative was to let a pinned row sink as it aged, which
    is not what pinning is for.

    The remaining keys differ by sort: `created` is the conversation's start,
    and `used` is `_LAST_ACTIVITY`. `created_at` breaks ties in the activity
    order because a session with no events has no activity of its own (the
    expression falls back to its creation), and two sessions started in the same
    second would otherwise come back in whatever order the plan chose — a list
    that reshuffles under a reader is indistinguishable from one that reordered
    for a reason. `s.id` is the last resort, for the same reason and because it
    is the one column that cannot tie.
    """

    if sort not in SESSION_SORTS:
        raise ValueError(f"unknown session sort: {sort!r}")
    if sort == "created":
        return "(s.pinned_at IS NOT NULL) DESC, s.created_at DESC, s.id"
    return f"(s.pinned_at IS NOT NULL) DESC, {_LAST_ACTIVITY} DESC, s.created_at DESC, s.id"


@dataclass(frozen=True, slots=True)
class AgentSessionRow:
    id: str
    account_id: str
    title: str | None
    created_at: datetime
    closed_at: datetime | None
    # Set when the owner took this session off their list. Distinct from
    # `closed_at` in both meaning and consequence: closing ends the conversation
    # and makes the row eligible for retention, removing only stops showing it.
    removed_at: datetime | None = None
    # Set when the owner pinned this session to the top of their list. A
    # statement about the list rather than the conversation, like `removed_at`:
    # pinning neither closes nor removes, and a pinned session still ages out of
    # retention.
    pinned_at: datetime | None = None
    # When the owner last had this session in front of them. It is the watermark
    # a reply is read against and the clock that keeps a session in the working
    # set, which is one fact: somebody has looked at it since the last thing was
    # said (D63).
    read_at: datetime | None = None
    # Derived, not a column: the listing and the purge query both compute it as
    # `_LAST_ACTIVITY`. None on a row read by a query that did not ask for it,
    # which is why it is last and defaulted rather than a field every `SELECT *`
    # has to carry.
    last_activity: datetime | None = None
    # Derived: how many branches the session has, and which one opening it shows.
    # Both are computed by the listing's laterals; the branch's own settings are
    # read when it is opened, not carried on every rail row.
    branch_count: int = 1
    open_branch_id: str | None = None
    # Derived, like the three above: whether the newest reply is newer than
    # `read_at`. Computed by the working-set query and nowhere else, because a
    # stored flag would be a second answer to a question the log and the
    # watermark already answer.
    unread: bool = False
    # The task run this session is an agent step of, or None for a
    # conversation. Such a session is the task's, never chat's (D99).
    task_run_id: str | None = None

    @classmethod
    def of(cls, row: dict[str, Any]) -> AgentSessionRow:
        return cls(
            id=row["id"],
            account_id=row["account_id"],
            title=row["title"],
            created_at=row["created_at"],
            closed_at=row["closed_at"],
            removed_at=row.get("removed_at"),
            pinned_at=row.get("pinned_at"),
            read_at=row.get("read_at"),
            last_activity=row.get("last_activity"),
            branch_count=int(row.get("branch_count") or 1),
            open_branch_id=row.get("open_branch_id"),
            unread=bool(row.get("unread")),
            task_run_id=row.get("task_run_id"),
        )


@dataclass(frozen=True, slots=True)
class BranchRow:
    id: str
    session_id: str
    forked_from: str | None
    forked_from_seq: int | None
    # How many leading events this branch inherited from `forked_from`. Zero on
    # a root branch. It is what labels a branch by its first new user message.
    inherited_event_count: int
    created_at: datetime
    # The machine this transcript runs on (D112). A branch is a transcript with
    # a place: `working_dir`, `attachments` and the drops an attachment may name
    # are all paths on *that* machine's filesystem, which is why a change of
    # machine clears them rather than reinterpreting them.
    #
    # Nullable, and only for rows written before machines existed: the core
    # cannot invent one for a branch it did not create, so such a branch adopts
    # the machine of its next turn once and is pinned from then on. Nothing
    # writes NULL after the migration that added this column.
    machine: str | None = None
    # Where the work is, if the user said. A hint the model is told about, never
    # a grant — the client runs as the user regardless.
    working_dir: str | None = None
    # Absolute paths the user attached. References, not copies: the agent reads
    # them with `fs_read`. A tuple because a row is frozen and a list would make
    # the row mutable through its own field.
    attachments: tuple[str, ...] = ()
    # Which granted tools the user wants in play, or None for all of them.
    #
    # None is not the empty tuple, and that distinction is the feature: no
    # selection means every tool policy grants, an empty selection means the
    # user asked for none. Storing "all" as a list would freeze today's registry
    # into the row, so a tool installed later would read as deselected.
    tool_selection: tuple[str, ...] | None = None
    # The branch's choices for the next turn, restored when the reader switches
    # back. None follows the account default (model/reasoning) or composed
    # policy (approval).
    model: str | None = None
    reasoning: str | None = None
    approval: str | None = None
    # The first message said *after* the inherited prefix. The branch switcher
    # labels a fork with it, so two branches read as the attempts they are
    # ("retry with legacy schema") rather than as "branch 2".
    first_message: str | None = None

    @classmethod
    def of(cls, row: dict[str, Any]) -> BranchRow:
        return cls(
            id=row["id"],
            session_id=row["session_id"],
            forked_from=row.get("forked_from"),
            forked_from_seq=(
                None if row.get("forked_from_seq") is None else int(row["forked_from_seq"])
            ),
            inherited_event_count=int(row.get("inherited_event_count") or 0),
            created_at=row["created_at"],
            machine=row.get("machine"),
            working_dir=row.get("working_dir"),
            # `or []` because an older row read through a pre-migration path has
            # NULL here, and a None would become `None` in the prompt.
            attachments=tuple(row.get("attachments") or []),
            # Not `or []`, unlike the line above: NULL is meaningful here and
            # has to survive as None. Collapsing it would turn "the user chose
            # nothing in particular" into "the user chose no tools" and
            # silently disarm every branch.
            tool_selection=(
                None if row.get("tool_selection") is None else tuple(row["tool_selection"])
            ),
            model=row.get("model"),
            reasoning=row.get("reasoning"),
            approval=row.get("approval"),
            first_message=row.get("first_message"),
        )


@dataclass(frozen=True, slots=True)
class PurgeCandidate:
    """A session eligible for purge, and when it was last touched.

    The timestamp is what the plan shows. Without it an operator sees a list of
    sessions and no way to tell how old each one is, so "older than 5d" stays an
    abstraction they have to trust.
    """

    session: AgentSessionRow
    last_activity: datetime


class AgentSessionStore:
    """Sessions: the rail's rows. The log and its settings live on branches."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def create(
        self,
        *,
        account_id: str,
        title: str | None = None,
        machine: str | None = None,
        task_run_id: str | None = None,
    ) -> tuple[AgentSessionRow, BranchRow]:
        """Create a session and its root branch, together.

        A session with no branch would be unopenable: the rail row exists but
        there is no transcript to read or turn to run. Both rows are one
        transaction so that half a session cannot survive a failure.

        `machine` is where the conversation's tools run (D112), recorded on the
        root branch because that is what a turn reads. It is the machine the
        window that created the session was on — a default, not a property: a
        fork may continue on another, and the window can be anywhere.

        `task_run_id` makes it a task's agent step rather than a conversation:
        every chat read below leaves it out (D99).
        """

        session_id = uuid.uuid4().hex
        branch_id = uuid.uuid4().hex
        async with self._conn.transaction():
            cursor = await self._conn.execute(
                """
                INSERT INTO agent_sessions (id, account_id, title, task_run_id)
                VALUES (%s, %s, %s, %s)
                RETURNING *
                """,
                (session_id, account_id, title, task_run_id),
            )
            session = AgentSessionRow.of(await cursor.fetchone())
            cursor = await self._conn.execute(
                """
                INSERT INTO session_branches (id, session_id, machine)
                VALUES (%s, %s, %s)
                RETURNING *
                """,
                (branch_id, session_id, machine),
            )
            branch = BranchRow.of(await cursor.fetchone())
        return replace(session, branch_count=1, open_branch_id=branch.id), branch

    async def get(self, session_id: str) -> AgentSessionRow | None:
        cursor = await self._conn.execute(
            "SELECT * FROM agent_sessions WHERE id = %s", (session_id,)
        )
        row = await cursor.fetchone()
        return AgentSessionRow.of(row) if row else None

    async def resolve_prefix(self, prefix: str) -> list[AgentSessionRow]:
        """Sessions whose id starts with `prefix`, for the admin read paths.

        `neuman-admin session list` prints a twelve-character id so a title can
        survive an eighty-column terminal, and its own comment said that was
        "enough to paste into `session events`" — which was not true. Only the
        full thirty-two-character id resolved, so the id the CLI showed was
        refused by the command it was printed for, with a bare `not_found` that
        reads as a missing session rather than a truncated id.

        Returning every match rather than the first is the point: a prefix that
        names two sessions must be an error the caller reports, never a silent
        choice between two people's transcripts. Admin-only — a user's paths
        resolve a session they already hold the whole id for, and widening those
        to prefixes would turn a typo into someone else's conversation.

        A non-hex prefix matches nothing without asking the database. Ids are
        `uuid4().hex`, so anything else cannot name one — and answering here
        keeps `LIKE`'s wildcards out of the query entirely rather than escaping
        them, which is one fewer thing to get right per dialect.
        """

        if not prefix or len(prefix) > 32 or not all(c in "0123456789abcdef" for c in prefix):
            return []

        cursor = await self._conn.execute(
            "SELECT * FROM agent_sessions WHERE id LIKE %s ORDER BY created_at DESC LIMIT 10",
            (prefix + "%",),
        )
        return [AgentSessionRow.of(row) for row in await cursor.fetchall()]

    async def owned_by(self, session_id: str, account_id: str) -> AgentSessionRow | None:
        """Fetch a session only if this account owns it.

        Every read path uses this rather than `get` plus a caller-side check:
        ownership is a scope on the query, so forgetting it is a syntax-level
        omission rather than a silently missing `if`.
        """

        cursor = await self._conn.execute(
            "SELECT * FROM agent_sessions WHERE id = %s AND account_id = %s",
            (session_id, account_id),
        )
        row = await cursor.fetchone()
        return AgentSessionRow.of(row) if row else None

    async def visible_to(self, session_id: str, account_id: str) -> AgentSessionRow | None:
        """Fetch a session only if this account owns it *and* still sees it.

        For the two places a user opens a session by id — reading its transcript
        and connecting a socket to it. Without the `removed_at` scope, a browser
        holding a stale id could reopen a session removed on another device, and
        the removal would look like it had failed.

        Separate from `owned_by` rather than a flag on it, because the paths that
        write to a session mid-turn must keep resolving a removed one: a user can
        remove a live conversation, and the turn already running in it still has
        a title to derive and events to append. Narrowing the shared ownership
        query would have made that a silent failure in the harness.

        A task's agent step is not visible here either: it is read through its
        run, by the task view, and a chat window holding its id opens nothing
        (D99).
        """

        cursor = await self._conn.execute(
            """
            SELECT * FROM agent_sessions
             WHERE id = %s AND account_id = %s AND removed_at IS NULL
               AND task_run_id IS NULL
            """,
            (session_id, account_id),
        )
        row = await cursor.fetchone()
        return AgentSessionRow.of(row) if row else None

    async def list_for_account(
        self, account_id: str, *, sort: str, limit: int = 50
    ) -> Sequence[AgentSessionRow]:
        """The sessions this account can still see, in the order asked for.

        Removed rows are excluded *here* rather than by the caller, so `limit`
        counts rows the user will actually be shown. Filtering after the limit
        is what made the old client-side version wrong: hiding ten sessions
        shortened the list by ten instead of revealing the ten behind the cap.

        **The order is decided here for the same reason the filter is.** A
        listing is capped at 50, so sorting in the browser would reorder the
        newest fifty by creation and call it "recently used" — a different set
        of rows from the fifty most recently used, and wrong in a way that only
        shows once an account has more than fifty sessions. `sort` is required
        rather than defaulted so every caller states which window it wants
        (D61).

        Each row carries the branch it opens on and how many it has. The open
        branch is the one with the newest activity, so a fork — created now —
        opens on the branch just made, and sending in another branch makes that
        one win next time. `last_activity` is computed for each row because the
        caller needs it to say *why* a row is where it is: the group heading and
        the date drawn on the row are the sort key, so the ordering and the date
        beside it are one fact rather than two.
        """

        cursor = await self._conn.execute(
            f"""
            SELECT s.*,
                   {_LAST_ACTIVITY} AS last_activity,
                   {_BRANCH_COUNT} AS branch_count,
                   open_branch.id AS open_branch_id
              FROM agent_sessions s
              {_LAST_EVENT}
              {_OPEN_BRANCH}
             WHERE s.account_id = %s AND s.removed_at IS NULL AND s.task_run_id IS NULL
             ORDER BY {_order_by(sort)}
             LIMIT %s
            """,
            (account_id, limit),
        )
        return [AgentSessionRow.of(row) for row in await cursor.fetchall()]

    async def active_for_account(
        self, account_id: str, *, cutoff: datetime
    ) -> Sequence[AgentSessionRow]:
        """The account's working set: what it has looked at or done since `cutoff`.

        **Membership is the account's, not a window's.** Two clocks say whether
        a session is in it — the last thing said or done in it (`_LAST_ACTIVITY`,
        the same expression retention and the listing use), and `read_at`, which
        opening a session moves. Either one inside the window keeps the row, so a
        session you opened and did not type into stays without pretending to be
        recent activity.

        **The order is the conversation, not the opening.** A row is drawn where
        its last activity puts it, which is why `read_at` is deliberately absent
        from `ORDER BY`: clicking a session must not lift it over the one you
        have been working in.

        `unread` is derived here rather than stored: a reply is unseen when a
        `response_final` is newer than the watermark. Per session, because the
        rail's row is the session — a conversation with any unread reply is
        unread.

        A turn running *right now* is not found by this query, because the
        registry is in memory; the caller adds those. A running turn is active
        whatever the window says, including a window of zero.
        """

        cursor = await self._conn.execute(
            f"""
            SELECT s.*,
                   {_LAST_ACTIVITY} AS last_activity,
                   {_BRANCH_COUNT} AS branch_count,
                   open_branch.id AS open_branch_id,
                   EXISTS (
                       SELECT 1
                         FROM session_events e
                        WHERE e.session_id = s.id
                          AND e.kind = 'response_final'
                          AND e.ts > coalesce(s.read_at, 'epoch'::timestamptz)
                   ) AS unread
              FROM agent_sessions s
              {_LAST_EVENT}
              {_OPEN_BRANCH}
             WHERE s.account_id = %s
               AND s.removed_at IS NULL
               AND s.task_run_id IS NULL
               AND ({_LAST_ACTIVITY} >= %s OR s.read_at >= %s)
             ORDER BY {_LAST_ACTIVITY} DESC, s.created_at DESC, s.id
            """,
            (account_id, cutoff, cutoff),
        )
        return [AgentSessionRow.of(row) for row in await cursor.fetchall()]

    async def search_for_account(
        self, account_id: str, query: str, *, sort: str, limit: int = 50
    ) -> Sequence[tuple[AgentSessionRow, str]]:
        """Sessions whose conversation matches ``query``, in the asked-for order.

        Full-text over the derived index (`session_search`), which holds what was
        *said* — the user's messages and the model's replies, across every branch
        — never reasoning or tool output. A title match counts too, because a
        session someone renamed deliberately should be findable by that name even
        if the word never appears in the conversation.

        Scoped and filtered exactly like `list_for_account`: ownership is a
        clause in the query rather than a check the caller might forget, and
        removed sessions stay removed. Ordered by the same keys as the unfiltered
        list rather than by rank — the match decides *membership*, and the order
        the reader chose decides where among the hits a match lands. A relevance
        ordering would scatter the date groups the rail is built from, which is
        the argument D52 already makes against one.

        Every word matches as a **prefix**: `ret` finds `retention`, which is
        what the search box promises (D52) and what `plainto_tsquery` alone does
        not do — it parses free text into whole lexemes, and the `simple`
        dictionary has no stemming, so a partial word matched nothing.

        Returns each row with a headline: the fragment that matched, with the
        terms marked. Without it a filtered list is a set of titles with no
        indication of why any of them is there. The headline is the session's,
        not a branch's; opening the hit opens the session's most recently active
        branch, and the branch switcher shows the rest.
        """

        # `plainto_tsquery` stays the parser because it is the one that treats
        # the input as text rather than tsquery syntax — `&`, `|` and `:` are
        # words, not operators. Its output is already quoted and normalized, so
        # suffixing each quoted lexeme with `:*` is a rewrite of the parser's
        # own output and cannot introduce syntax; `to_tsquery` then reads it
        # back with every term a prefix.
        #
        # Built once in `q` because `ts_headline` must mark the same query `@@`
        # matched: a headline given a different query highlights nothing and
        # returns a blank snippet for a row that did match.
        cursor = await self._conn.execute(
            f"""
            WITH q AS (
                SELECT to_tsquery(
                           'simple',
                           regexp_replace(
                               plainto_tsquery('simple', %s)::text,
                               '''([^'']+)''',
                               '''\\1'':*',
                               'g'
                           )
                       ) AS tsq
            )
            SELECT s.*,
                   {_LAST_ACTIVITY} AS last_activity,
                   {_BRANCH_COUNT} AS branch_count,
                   open_branch.id AS open_branch_id,
                   coalesce(
                       ts_headline(
                           'simple', f.body, q.tsq,
                           'StartSel=<<,StopSel=>>,MaxWords=18,MinWords=5,'
                           'ShortWord=2,MaxFragments=1'
                       ),
                       ''
                   ) AS snippet
              FROM agent_sessions s
              LEFT JOIN session_search f ON f.session_id = s.id
              {_LAST_EVENT}
              {_OPEN_BRANCH}
              CROSS JOIN q
             WHERE s.account_id = %s
               AND s.removed_at IS NULL
               AND s.task_run_id IS NULL
               AND (
                     f.document @@ q.tsq
                     OR s.title ILIKE %s
                   )
             ORDER BY {_order_by(sort)}
             LIMIT %s
            """,
            (query, account_id, f"%{query}%", limit),
        )
        return [
            (AgentSessionRow.of(row), row.get("snippet") or "")
            for row in await cursor.fetchall()
        ]

    async def reindex_search(self) -> int:
        """Rebuild the derived search index from every branch's log.

        Never touches `session_events`: the index is derived data, so rebuilding
        it must not be able to damage the record it indexes — the same guarantee
        `AuditStore.reindex` makes about the chain.

        One row per **session**, because the rail shows sessions and the question
        is "which conversation was that in". The text is every branch's, ordered
        by session then event so a rebuild is deterministic. The kind list and
        the text extraction are duplicated here in SQL rather than driven from
        `searchable_text`, which is a real seam: a rebuild has to happen in one
        statement over every session, and pulling every event into Python to
        re-derive it would make a repair proportional to the whole history in
        memory. `test_tools`-style coverage pins the two to the same answer.
        """

        async with self._conn.transaction():
            await self._conn.execute("TRUNCATE session_search")
            cursor = await self._conn.execute(
                """
                INSERT INTO session_search (session_id, document, body)
                SELECT session_id,
                       to_tsvector('simple', string_agg(text, ' ' ORDER BY branch_id, seq)),
                       string_agg(text, ' ' ORDER BY branch_id, seq)
                  FROM (
                        SELECT e.session_id, e.branch_id, e.seq,
                               CASE e.kind
                                 WHEN 'user_message'
                                   THEN coalesce(e.payload ->> 'content', '')
                                 ELSE coalesce(e.payload -> 'message' ->> 'content', '')
                               END AS text
                          FROM session_events e
                         WHERE e.kind IN ('user_message', 'response_final')
                       ) said
                 WHERE text <> ''
                 GROUP BY session_id
                """
            )
        return cursor.rowcount

    async def list_all(self, *, limit: int = 50) -> Sequence[AgentSessionRow]:
        """Every account's sessions, newest first. For an admin only.

        Unscoped, which is the point: a business reads its own record, and the
        caller is what decides whether that is permitted — `require_admin` in
        the handler, not a `WHERE` clause here. Removed rows are still excluded,
        because "removed" is the owner's statement that they are done with a
        session rather than a privacy boundary, and a list that resurrected them
        would surprise the person who tidied up.
        """

        cursor = await self._conn.execute(
            f"""
            SELECT s.*,
                   {_BRANCH_COUNT} AS branch_count,
                   open_branch.id AS open_branch_id
              FROM agent_sessions s
              {_OPEN_BRANCH}
             WHERE s.removed_at IS NULL
             ORDER BY s.created_at DESC
             LIMIT %s
            """,
            (limit,),
        )
        return [AgentSessionRow.of(row) for row in await cursor.fetchall()]

    async def counts_by_account(self, account_ids: Sequence[str]) -> dict[str, int]:
        """How many visible sessions each account has.

        One query for the roster's page, and the same scope `list_for_account`
        uses: removed rows are the owner's statement that they are done with a
        session, so a roster that counted them would disagree with the list the
        owner sees.
        """

        ids = list(account_ids)
        if not ids:
            return {}
        cursor = await self._conn.execute(
            """
            SELECT account_id, count(*) AS n
              FROM agent_sessions
             WHERE account_id = ANY(%s) AND removed_at IS NULL AND task_run_id IS NULL
             GROUP BY account_id
            """,
            (ids,),
        )
        return {row["account_id"]: int(row["n"]) for row in await cursor.fetchall()}

    async def set_title(self, session_id: str, account_id: str, title: str) -> bool:
        """Name a session, replacing whatever it had."""

        cursor = await self._conn.execute(
            "UPDATE agent_sessions SET title = %s WHERE id = %s AND account_id = %s",
            (title, session_id, account_id),
        )
        return cursor.rowcount > 0

    async def title_if_unnamed(self, session_id: str, account_id: str, title: str) -> bool:
        """Name a session only if it has no name yet.

        The derived title must never overwrite a deliberate one. A plain UPDATE
        would, and the bug would be invisible until someone renamed a session and
        watched the rename vanish on their next message — so the condition is in
        the statement rather than in a read-then-write the caller could get
        wrong.

        Returns whether this call set it, which is also how the caller avoids
        doing the work twice in a session.
        """

        cursor = await self._conn.execute(
            """
            UPDATE agent_sessions
               SET title = %s
             WHERE id = %s AND account_id = %s AND title IS NULL
            """,
            (title, session_id, account_id),
        )
        return cursor.rowcount > 0

    async def mark_read(self, session_id: str, account_id: str) -> bool:
        """Record that its owner has had this session in front of them.

        Moved by opening it, from any window: the watermark is the account's, so
        a reply read on one device stops being unread on the next poll
        everywhere (D63). It is also the clock that keeps a session in the
        working set, which is the same fact — somebody looked. Returns whether a
        row moved, so an unknown session and someone else's are both a 404 rather
        than a silent success.
        """

        cursor = await self._conn.execute(
            "UPDATE agent_sessions SET read_at = now() WHERE id = %s AND account_id = %s",
            (session_id, account_id),
        )
        return cursor.rowcount > 0

    async def set_pinned(self, session_id: str, account_id: str, pinned: bool) -> bool:
        """Pin a session to the top of its owner's list, or let it go.

        A statement about the *list*, like `mark_removed` and unlike `close`: it
        reorders a row and ends nothing. Server-side rather than in a browser,
        because a pin that holds on the laptop and not the phone is the bug
        `removed_at` was moved out of `localStorage` to fix, and because the
        ordering it feeds is applied here, before the row cap (D61).

        The predicate on each statement is what makes a repeated click a no-op
        rather than a second event: pinning an already-pinned session keeps the
        moment it was pinned, and unpinning one that is not pinned writes
        nothing. Returns whether this call changed the row.
        """

        if pinned:
            statement = (
                "UPDATE agent_sessions SET pinned_at = now()"
                " WHERE id = %s AND account_id = %s AND pinned_at IS NULL"
            )
        else:
            statement = (
                "UPDATE agent_sessions SET pinned_at = NULL"
                " WHERE id = %s AND account_id = %s AND pinned_at IS NOT NULL"
            )
        cursor = await self._conn.execute(statement, (session_id, account_id))
        return cursor.rowcount > 0

    async def purgeable(
        self, cutoff: datetime, *, account_id: str | None = None
    ) -> Sequence[PurgeCandidate]:
        """Sessions with no activity since `cutoff`, oldest activity first.

        **"Older than" means idle for, not closed before.** The clock is the
        session's last activity: its creation, its newest branch's creation, the
        newest event in any branch, and `closed_at` if it was ever closed,
        whichever is latest. A session still open but untouched for the window is
        exactly what an operator means by "old" — and the earlier
        `closed_at`-only predicate matched nothing on a real appliance, because
        the client has no close action to set it (N149). Closing still counts,
        and resets the window to the moment of closing.

        One predicate for the manual purge and the retention job
        (`composition/jobs/retention.py`), so "what would be purged now" and
        "what the job purges" cannot disagree — and the expression itself is
        `_LAST_ACTIVITY`, shared with the listing that sorts by the same clock,
        so "old" and "recently used" are also one definition.
        """

        sql = f"""
            SELECT s.*,
                   {_LAST_ACTIVITY} AS last_activity,
                   {_BRANCH_COUNT} AS branch_count
              FROM agent_sessions s
              {_LAST_EVENT}
             WHERE {_LAST_ACTIVITY} < %s
        """
        params: list[Any] = [cutoff]
        if account_id is not None:
            sql += " AND s.account_id = %s"
            params.append(account_id)
        sql += " ORDER BY last_activity"
        cursor = await self._conn.execute(sql, tuple(params))
        return [
            PurgeCandidate(session=AgentSessionRow.of(row), last_activity=row["last_activity"])
            for row in await cursor.fetchall()
        ]

    async def purge_ids(self, session_ids: Sequence[str]) -> int:
        """Delete these sessions by id; branches, events, search and refs cascade.

        By id rather than by a re-evaluated predicate, so the rows an operator
        approved are the rows that go. Re-running the cutoff at delete time
        could sweep in a session that closed in between, which is a deletion
        nobody saw. A session already gone (the hourly job, another admin) is a
        no-op, not an error.
        """

        ids = list(session_ids)
        if not ids:
            return 0
        cursor = await self._conn.execute(
            "DELETE FROM agent_sessions WHERE id = ANY(%s)", (ids,)
        )
        return cursor.rowcount

    async def close(self, session_id: str, account_id: str) -> bool:
        cursor = await self._conn.execute(
            """
            UPDATE agent_sessions SET closed_at = now()
             WHERE id = %s AND account_id = %s AND closed_at IS NULL
            """,
            (session_id, account_id),
        )
        return cursor.rowcount > 0

    async def mark_removed(self, session_id: str, account_id: str) -> bool:
        """Take a session off its owner's list, for good.

        Not a delete and not a close. The row, its branches' event logs and the
        audit entries naming them all survive — only `list_for_account` stops
        returning it. An operator purges it later, or never.

        `removed_at IS NULL` guards the timestamp rather than the fact: removing
        a removed session is already true, and letting the second call move the
        timestamp would mean a repeated click rewriting when the user decided.
        Returns whether this call was the one that did it.
        """

        cursor = await self._conn.execute(
            """
            UPDATE agent_sessions SET removed_at = now()
             WHERE id = %s AND account_id = %s AND removed_at IS NULL
            """,
            (session_id, account_id),
        )
        return cursor.rowcount > 0


class BranchStore:
    """Branches: one transcript each, with the settings its next turn runs on."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def transaction(self):
        """The transaction a fork owns: the row, its prefix and the audit entry.

        Exposed here because the three writes share this connection and must be
        one act. A fork that failed between the row and the copy would leave an
        empty branch in the rail, which is worse than either outcome.
        """

        return self._conn.transaction()

    async def get(self, branch_id: str) -> BranchRow | None:
        cursor = await self._conn.execute(
            "SELECT * FROM session_branches WHERE id = %s", (branch_id,)
        )
        row = await cursor.fetchone()
        return BranchRow.of(row) if row else None

    async def resolve_prefix(self, prefix: str) -> list[BranchRow]:
        """Branches whose id starts with `prefix`, for the admin read paths.

        Branch ids are what `session events` reads; a session id names the row
        above them. Hex-only for the reason the session resolver is.
        """

        if not prefix or len(prefix) > 32 or not all(c in "0123456789abcdef" for c in prefix):
            return []
        cursor = await self._conn.execute(
            "SELECT * FROM session_branches WHERE id LIKE %s ORDER BY created_at LIMIT 10",
            (prefix + "%",),
        )
        return [BranchRow.of(row) for row in await cursor.fetchall()]

    async def owned_by(self, branch_id: str, account_id: str) -> BranchRow | None:
        """Fetch a branch only if its session belongs to this account.

        The session is the owner; the branch has no account of its own. One join
        rather than a denormalised column, so a branch cannot name an account its
        session does not have.
        """

        cursor = await self._conn.execute(
            """
            SELECT b.*
              FROM session_branches b
              JOIN agent_sessions s ON s.id = b.session_id
             WHERE b.id = %s AND s.account_id = %s
            """,
            (branch_id, account_id),
        )
        row = await cursor.fetchone()
        return BranchRow.of(row) if row else None

    async def visible_to(self, branch_id: str, account_id: str) -> BranchRow | None:
        """Ownership *and* the session still on this account's list.

        The paths a user opens by branch id — a transcript, a socket, an image —
        use this. The paths that write mid-turn use `owned_by`: a session removed
        while its branch is running must still resolve, or the turn loses the row
        it is writing to. A task's agent step is the task view's, not chat's
        (D99).
        """

        cursor = await self._conn.execute(
            """
            SELECT b.*
              FROM session_branches b
              JOIN agent_sessions s ON s.id = b.session_id
             WHERE b.id = %s AND s.account_id = %s AND s.removed_at IS NULL
               AND s.task_run_id IS NULL
            """,
            (branch_id, account_id),
        )
        row = await cursor.fetchone()
        return BranchRow.of(row) if row else None

    async def list_for_session(self, session_id: str) -> Sequence[BranchRow]:
        """Every branch of a session, oldest first.

        The switcher's order: `main` (the root, created with the session) is
        first, and each fork follows in the order it was made. A branch's
        activity is the listing's job, not this one's — the caller has the session
        row for that.
        """

        cursor = await self._conn.execute(
            """
            SELECT b.*, first.message AS first_message
              FROM session_branches b
              LEFT JOIN LATERAL (
                   SELECT left(e.payload ->> 'content', 80) AS message
                     FROM session_events e
                    WHERE e.branch_id = b.id
                      AND e.kind = 'user_message'
                      AND e.seq > b.inherited_event_count
                    ORDER BY e.seq
                    LIMIT 1
              ) first ON true
             WHERE b.session_id = %s
             ORDER BY b.created_at, b.id
            """,
            (session_id,),
        )
        return [BranchRow.of(row) for row in await cursor.fetchall()]

    async def open_for_session(self, session_id: str) -> BranchRow | None:
        """The branch a session opens on: the one with the newest activity.

        The same rule the listing uses. Kept here as one statement so opening a
        session by id and drawing its row cannot disagree about which branch is
        the current one.
        """

        cursor = await self._conn.execute(
            """
            SELECT b.*,
                   greatest(b.created_at, coalesce(blast.ts, b.created_at)) AS activity
              FROM session_branches b
              LEFT JOIN LATERAL (
                   SELECT e.ts FROM session_events e
                    WHERE e.branch_id = b.id
                    ORDER BY e.seq DESC LIMIT 1
              ) blast ON true
             WHERE b.session_id = %s
             ORDER BY activity DESC, b.created_at DESC, b.id
             LIMIT 1
            """,
            (session_id,),
        )
        row = await cursor.fetchone()
        return BranchRow.of(row) if row else None

    async def create(
        self,
        *,
        session_id: str,
        forked_from: str | None = None,
        forked_from_seq: int | None = None,
        inherited_event_count: int = 0,
        machine: str | None = None,
        working_dir: str | None = None,
        attachments: Sequence[str] = (),
        tool_selection: Sequence[str] | None = None,
        model: str | None = None,
        reasoning: str | None = None,
        approval: str | None = None,
    ) -> BranchRow:
        """Insert one branch row. The caller owns the transaction and the copy."""

        from psycopg.types.json import Jsonb

        cursor = await self._conn.execute(
            """
            INSERT INTO session_branches
                (id, session_id, forked_from, forked_from_seq,
                 inherited_event_count, machine, working_dir, attachments, tool_selection,
                 model, reasoning, approval)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                uuid.uuid4().hex,
                session_id,
                forked_from,
                forked_from_seq,
                inherited_event_count,
                machine,
                working_dir,
                Jsonb(list(attachments)),
                None if tool_selection is None else Jsonb(list(tool_selection)),
                model,
                reasoning,
                approval,
            ),
        )
        return BranchRow.of(await cursor.fetchone())

    async def set_machine(self, branch_id: str, machine: str) -> bool:
        """Pin a branch to a machine.

        Used in exactly two places: adopting a machine for a branch written
        before machines existed (once), and moving a branch the user asked to
        move. The caller clears `working_dir` and `attachments` in the same
        transaction, because a path on one machine is not a path on another and
        keeping it would be the silent-wrong-files failure this column exists to
        end.
        """

        cursor = await self._conn.execute(
            "UPDATE session_branches SET machine = %s WHERE id = %s",
            (machine, branch_id),
        )
        return cursor.rowcount > 0

    async def set_working_dir(self, branch_id: str, working_dir: str | None) -> bool:
        """Point a branch at a directory, or clear it with None.

        No validation here: whether the path is *within policy* is a decision,
        and decisions do not live in the repository layer (D7). The caller checks
        it against composed reach before getting this far.
        """

        cursor = await self._conn.execute(
            "UPDATE session_branches SET working_dir = %s WHERE id = %s",
            (working_dir, branch_id),
        )
        return cursor.rowcount > 0

    async def set_attachments(self, branch_id: str, paths: Sequence[str]) -> bool:
        """Replace the attachment list.

        Whole-list replacement rather than add/remove verbs: the client holds the
        list it just rendered, so a PATCH that states the new list cannot get out
        of step with the UI the way two mutating endpoints can. It also makes
        reordering and clearing fall out for free.

        No validation here — whether a path is *within policy* is a decision, and
        decisions do not live in the repository layer (D7).
        """

        from psycopg.types.json import Jsonb

        cursor = await self._conn.execute(
            "UPDATE session_branches SET attachments = %s WHERE id = %s",
            (Jsonb(list(paths)), branch_id),
        )
        return cursor.rowcount > 0

    async def attached_paths(self, account_id: str) -> list[str]:
        """Every path any of the account's conversations still attaches.

        The client deletes a dropped file once nothing names it (D102), so this
        must be the whole set: every branch, including a fork nobody is looking
        at and a task's step. A removed session is gone from every list and
        cannot come back, so what it attached no longer counts.
        """

        cursor = await self._conn.execute(
            """
            SELECT DISTINCT path
              FROM session_branches b
              JOIN agent_sessions s ON s.id = b.session_id,
                   jsonb_array_elements_text(b.attachments) AS path
             WHERE s.account_id = %s AND s.removed_at IS NULL
             ORDER BY path
            """,
            (account_id,),
        )
        return [row["path"] for row in await cursor.fetchall()]

    async def set_tool_selection(self, branch_id: str, tools: Sequence[str] | None) -> bool:
        """Narrow which tools this branch's turns may use, or clear with None.

        Whole-list replacement, like `set_attachments` and for the same reason:
        the client sends the selection it just rendered, so there is no
        add/remove ordering to get out of step with the UI.

        `None` is not `[]`. None restores "every tool policy grants" and is what
        the Select all control writes; an empty list is a user who deselected
        everything, which is a legitimate thing to ask for and must not be
        quietly read as the default.

        No validation here: whether a name is *granted* is a decision, and
        decisions do not live in the repository layer (D7). The caller intersects
        against composed policy, and the capability gate re-checks every proposal
        regardless.
        """

        from psycopg.types.json import Jsonb

        cursor = await self._conn.execute(
            "UPDATE session_branches SET tool_selection = %s WHERE id = %s",
            (None if tools is None else Jsonb(list(tools)), branch_id),
        )
        return cursor.rowcount > 0

    async def set_inherited(self, branch_id: str, inherited: int) -> bool:
        """Record how much of the log is inherited; written once, at fork time."""

        cursor = await self._conn.execute(
            "UPDATE session_branches SET inherited_event_count = %s WHERE id = %s",
            (inherited, branch_id),
        )
        return cursor.rowcount > 0

    async def set_model_reasoning(
        self, branch_id: str, *, model: str | None, reasoning: str | None
    ) -> bool:
        """Record what the next turn of this branch will use.

        Written when the reader picks a control and again when a turn starts, so
        a branch opened from another device restores the last thing it actually
        ran with rather than what this browser remembered.
        """

        cursor = await self._conn.execute(
            "UPDATE session_branches SET model = %s, reasoning = %s WHERE id = %s",
            (model, reasoning, branch_id),
        )
        return cursor.rowcount > 0

    async def set_approval(self, branch_id: str, approval: str | None) -> bool:
        """Choose this branch's approval mode, or None to follow policy."""

        cursor = await self._conn.execute(
            "UPDATE session_branches SET approval = %s WHERE id = %s",
            (approval, branch_id),
        )
        return cursor.rowcount > 0
