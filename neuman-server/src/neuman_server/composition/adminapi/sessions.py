"""Session reads for admins: any account's conversation, and the log beneath it.

**The admin surface is the CLI, and this is what it reads.** An admin asking
about someone's session goes through `neuman-admin session`, which reaches these
routes; a *user* asking about their own goes through the `session_query` tool.
One operation underneath (`decision/ops/sessions.py`) serves both, so paging,
kind filtering and payload clipping exist once. The alternative — a tool with an
admin branch *and* a CLI without one — is the N48 shape: two descriptions of one
behaviour, drifting apart.

Why an admin reads these at all: a business has to be able to read its own
record. To understand what is happening, to answer for it when something goes
wrong, and to point Neuman at its own history and ask where it is unhelpful. The
restriction that used to prevent it protected nothing — whoever holds
`neuman-admin` holds sudo, and `psql` reads the same two tables regardless — so
it only made the legitimate case awkward while deterring nobody.

**Crossing into another account is appended to the chain.** `session.read` and
`session.list` name the reader, the session and its owner, so "who read my
session" has an answer. Reading your own records nothing: every turn would
otherwise append one, which is noise rather than governance. On an appliance
whose claim is a verifiable record, reading the record is an action that belongs
in it.

Authority is the `/admin` prefix guard's, not these handlers' — protection is a
consequence of the route existing rather than of a handler remembering (N27,
N32).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...adapters.persistence import (
    AccountStore,
    AgentSessionStore,
    AuditStore,
    BranchStore,
    EventStore,
    ImageStore,
)
from ...decision.audit import chain
from ...decision.ops import sessions as session_ops
from .deps import admin_actor, connection, services_of

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request
    from starlette.responses import Response

__all__ = ["admin_session_routes"]


def admin_session_routes() -> list[Any]:
    from starlette.routing import Route

    return [
        Route("/admin/sessions", _list, methods=["GET"]),
        Route("/admin/sessions/{session_id}", _read, methods=["GET"]),
        # Two routes rather than one, because confirming a redirect is two
        # acts: the plan says what would go, and the apply deletes the exact
        # ids the operator approved. A single route with a `confirm` flag could
        # not bind the approval to a set, and the set can move under an hourly
        # retention pass.
        Route("/admin/sessions/purge/plan", _purge_plan, methods=["POST"]),
        Route("/admin/sessions/purge/apply", _purge_apply, methods=["POST"]),
    ]


async def _body(request: Request) -> dict[str, Any]:
    """The request's JSON object, or {} when it is absent or not an object."""

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a body that will not parse is no body
        return {}
    return body if isinstance(body, dict) else {}


async def _record(request: Request, conn: Any, action: str, detail: dict[str, Any]) -> None:
    """Append an admin's read to the chain."""

    await chain.append(
        AuditStore(conn),
        actor=admin_actor(request),
        action=action,
        payload=detail,
        mirror=services_of(request).settings.audit_mirror_path,
    )


async def _list(request: Request) -> Response:
    """Every account's sessions, or one account's with `?account=`."""

    from starlette.responses import JSONResponse

    account = request.query_params.get("account")

    async with connection(request) as conn:
        store = AgentSessionStore(conn)
        accounts = AccountStore(conn)
        if account:
            # By *name*, because that is what an admin types; the store is keyed
            # by account id, so this resolves first and answers an empty list
            # for a name nobody has used rather than pretending to fail.
            row = await accounts.by_name(account)
            # By creation, like `list_all`: an operator's reading of what exists,
            # where a pin and "recently used" describe the owner's own list
            # rather than this one.
            rows = [] if row is None else await store.list_for_account(row.id, sort="created")
        else:
            rows = await store.list_all()

        await _record(request, conn, "session.list", {"account": account or "all"})
        # What each conversation spent, from its own log. One query for the
        # page rather than one per row: the list can be every session on the
        # appliance, and a per-session read would be N round trips to draw one
        # table (the reason the roster reads once too).
        tokens = await EventStore(conn).tokens_for_sessions([row.id for row in rows])
        # The name, for the reason a filter takes one: the id is the store's key
        # and an operator reads and types the name.
        names = await accounts.names_for([row.account_id for row in rows])

    return JSONResponse(
        {
            "sessions": [
                {
                    "id": row.id,
                    "account": names[row.account_id],
                    "title": row.title,
                    "created_at": row.created_at.isoformat(),
                    "branches": row.branch_count,
                    "open_branch": row.open_branch_id,
                    "tokens": tokens.get(row.id, 0),
                }
                for row in rows
            ]
        }
    )


async def _read(request: Request) -> Response:
    """One session: its kinds, a window of events, or one event whole.

    Three readings on one route rather than three routes, because they are three
    views of the same thing and the parameters decide which — `?view=kinds`,
    `?view=events`, `?view=event&seq=N`. A route per view would mean three places
    to keep the scope check and the audit append.
    """

    from starlette.responses import JSONResponse

    session_id = request.path_params["session_id"]
    params = request.query_params
    view = params.get("view", "events")

    def integer(name: str, default: int | None = None) -> int | None:
        raw = params.get(name)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except ValueError:
            raise ValueError(name) from None

    try:
        seq = integer("seq")
        last = integer("last")
        after = integer("after", 0) or 0
        limit = integer("limit", 50) or 50
    except ValueError as exc:
        return JSONResponse(
            {"error": "invalid_request", "detail": f"{exc} must be an integer"},
            status_code=400,
        )

    async with connection(request) as conn:
        store = AgentSessionStore(conn)
        row = await store.get(session_id)

        # An exact id is the normal case and stays one query. A prefix is the
        # case the CLI creates for itself: `session list` prints twelve
        # characters, and before this every one of them was refused by the
        # commands it was printed for.
        if row is None and session_id:
            matches = await store.resolve_prefix(session_id)
            if len(matches) > 1:
                return JSONResponse(
                    {
                        "error": "ambiguous_session",
                        "detail": f"{session_id!r} names {len(matches)} sessions",
                        "candidates": [match.id for match in matches],
                    },
                    status_code=400,
                )
            row = matches[0] if matches else None
            if row is not None:
                # Everything below — the audit detail and the event read — must
                # use the id that actually exists, not the fragment typed for it.
                session_id = row.id

        if row is None:
            return JSONResponse({"error": "not_found"}, status_code=404)

        # The log is a branch now. An admin who knows only the session reads the
        # branch with the newest activity, which is the one a user would open;
        # `?branch=` names another, and `view=branches` lists them all so the CLI
        # can offer the ids without a second route.
        branches = BranchStore(conn)
        all_branches = await branches.list_for_session(session_id)
        if view == "branches":
            body = {
                "session_id": session_id,
                "branches": [
                    {
                        "id": item.id,
                        "created_at": item.created_at.isoformat(),
                        "forked_from": item.forked_from,
                        "forked_from_seq": item.forked_from_seq,
                        "inherited_event_count": item.inherited_event_count,
                        "first_message": item.first_message,
                    }
                    for item in all_branches
                ],
            }
            await _record(
                request,
                conn,
                "session.read",
                {"session_id": session_id, "owner": row.account_id, "view": view},
            )
            return JSONResponse(body)

        wanted = params.get("branch")
        if wanted:
            branch = await branches.get(wanted)
            if branch is None or branch.session_id != session_id:
                return JSONResponse({"error": "not_found"}, status_code=404)
        else:
            branch = await branches.open_for_session(session_id)
        if branch is None:
            return JSONResponse({"error": "not_found"}, status_code=404)

        if view == "event" and seq is None:
            return JSONResponse(
                {"error": "invalid_request", "detail": "view=event needs seq"},
                status_code=400,
            )

        # Only the view's own window is read. Fetching the whole log to cut a
        # page out of it made a press on `read` wait on every row the branch had
        # ever written (see `EventStore.window`).
        store_events = EventStore(conn)
        if view == "kinds":
            body = session_ops.kinds(await store_events.summary(branch.id), branch_id=branch.id)
        elif view == "event":
            body = session_ops.event(
                await store_events.at_seq(branch.id, seq), branch_id=branch.id, seq=seq
            )
        else:
            body = session_ops.events(
                await store_events.window(
                    branch.id,
                    kinds=session_ops.split_kinds(params.get("kind")),
                    last=last,
                    after=after,
                    limit=limit,
                ),
                branch_id=branch.id,
            )
        body["session_id"] = session_id
        body["branches"] = [
            {"id": item.id, "created_at": item.created_at.isoformat()} for item in all_branches
        ]

        await _record(
            request,
            conn,
            "session.read",
            {
                "session_id": session_id,
                "branch_id": branch.id,
                "owner": row.account_id,
                "view": view,
            },
        )

    return JSONResponse(body)


async def _purge_plan(request: Request) -> Response:
    """The sessions a purge older than `older_than_seconds` would take.

    "Older than" is *idle* for the duration — no activity in the window — not
    "closed before" (`AgentSessionStore.purgeable`). An open session nobody has
    touched for the window is what an operator is asking to remove, and the
    closed-only predicate this replaces matched nothing on a real appliance
    because the client never closes a session (N149).

    A read, so it records nothing: it lists sessions the way `session.list`
    does, and the destructive act — the one that belongs in the chain — is the
    apply below.
    """

    from datetime import UTC, datetime, timedelta

    from starlette.responses import JSONResponse

    body = await _body(request)
    try:
        seconds = int(body.get("older_than_seconds", 0))
    except (TypeError, ValueError):
        seconds = 0
    if seconds <= 0:
        return JSONResponse(
            {
                "error": "invalid_request",
                "detail": "older_than_seconds must be a positive integer",
            },
            status_code=400,
        )
    account_name = body.get("account")
    cutoff = datetime.now(UTC) - timedelta(seconds=seconds)

    async with connection(request) as conn:
        account_id: str | None = None
        if account_name:
            row = await AccountStore(conn).by_name(account_name)
            if row is None:
                return JSONResponse({"sessions": [], "count": 0, "events": 0})
            account_id = row.id
        candidates = await AgentSessionStore(conn).purgeable(cutoff, account_id=account_id)
        events = await EventStore(conn).count_for_sessions(
            [candidate.session.id for candidate in candidates]
        )
        # What an operator approves by reading, so the person rather than a key.
        names = await AccountStore(conn).names_for(
            [candidate.session.account_id for candidate in candidates]
        )

    return JSONResponse(
        {
            "sessions": [
                {
                    "id": candidate.session.id,
                    "account": names[candidate.session.account_id],
                    "title": candidate.session.title,
                    "created_at": candidate.session.created_at.isoformat(),
                    "last_activity": candidate.last_activity.isoformat(),
                }
                for candidate in candidates
            ],
            "count": len(candidates),
            "events": events,
        }
    )


async def _purge_apply(request: Request) -> Response:
    """Delete the sessions the operator approved, and collect their images.

    Two steps in one transaction: removing the roots (`agent_sessions`, whose
    events, search rows and image references cascade) and then the mark-and-
    sweep of the images those roots were the only thing rooting. Doing the
    delete without the sweep is how the orphaned-image bug comes back, so they
    are one act, and the whole thing is one chain entry — a transcript was
    destroyed, by whom, and which.
    """

    from starlette.responses import JSONResponse

    body = await _body(request)
    ids = body.get("session_ids")
    if not isinstance(ids, list) or not all(isinstance(item, str) and item for item in ids):
        return JSONResponse(
            {
                "error": "invalid_request",
                "detail": "session_ids must be a list of session ids",
            },
            status_code=400,
        )
    if not ids:
        return JSONResponse(
            {"error": "invalid_request", "detail": "session_ids must not be empty"},
            status_code=400,
        )

    async with connection(request) as conn:
        async with conn.transaction():
            # Counted before the delete, because after it there is nothing to
            # count and the report would be a zero that means "we forgot".
            events = await EventStore(conn).count_for_sessions(ids)
            removed = await AgentSessionStore(conn).purge_ids(ids)
            swept = await ImageStore(conn).sweep()
            await _record(
                request,
                conn,
                "session.purge",
                {
                    "count": removed,
                    "ids": ids,
                    "images": swept.images,
                    "image_bytes": swept.bytes,
                },
            )

    return JSONResponse(
        {
            "purged": removed,
            "events": events,
            "images": swept.images,
            "bytes": swept.bytes,
        }
    )
