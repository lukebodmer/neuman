"""A user's own sessions, their branches, and the fork that makes one.

Every query is scoped by the session's `account_id` at the repository, not by a
check in this file — `owned_by` either returns the row or does not exist for this
caller. A scope that lives in the query cannot be forgotten by a new route.

A **session** is the rail row; a **branch** is one transcript inside it. The
routes split along that line: `/api/sessions` names what the list shows and what
carries a title, a pin and a removal; `/api/branches` names a transcript and the
settings its next turn runs with. A fork is a branch of the session it came
from, so it never appears as a second row.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ...adapters.persistence import (
    SESSION_SORTS,
    AgentSessionStore,
    AuditStore,
    BranchStore,
    EventStore,
    GrantStore,
    ImageStore,
)
from ...decision.harness import PROJECTION_KINDS, project
from ...decision.ops import fork as fork_op
from ...decision.policy import gate
from ...decision.policy.fragments import AUTO_ACCEPT_MODES, auto_accept_rank
from ...decision.policy.ops import preview_policy
from ...decision.policy.paths import attachable, within_reach
from .deps import (
    Unauthorized,
    acting_drops,
    acting_home,
    connection,
    require_csrf,
    require_principal,
    services_of,
)

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request
    from starlette.responses import Response

__all__ = ["session_routes"]

# One cap for every route that accepts a title. The derived title caps itself at
# 60 for a list row; a hand-chosen one gets this much room because someone typing
# it has a reason. Two constants for one concept would mean a title one route
# accepts and another truncates.
_MAX_TITLE = 200

# Attachments are paths the model is told about in every system prompt, so the
# cost of a long list is paid on every turn of the conversation. A dozen files is
# a working set; a hundred is someone using the field as a file manager.
_MAX_ATTACHMENTS = 12

# The longest filter string accepted. A type-ahead sends a request per
# keystroke, and a query longer than this is not a filter anyone typed into a
# sidebar — it is a paste, and `plainto_tsquery` would spend the time tokenising
# it to return nothing.
_MAX_QUERY = 128


def session_routes() -> list[Any]:
    from starlette.routing import Route

    return [
        # The account's working set, which is what the rail's Active section
        # draws (D63). Its own path rather than a view of `/api/sessions/{id}`:
        # it answers about what is happening rather than about what is stored,
        # and `/api/sessions/active` would be read as a session id by the route
        # below it.
        Route("/api/active", _active, methods=["GET"]),
        Route("/api/sessions", _list_sessions, methods=["GET"]),
        Route("/api/sessions", _create_session, methods=["POST"]),
        Route("/api/sessions/{session_id}", _get_session, methods=["GET"]),
        Route("/api/sessions/{session_id}", _patch_session, methods=["PATCH"]),
        Route("/api/sessions/{session_id}", _close_session, methods=["DELETE"]),
        Route("/api/sessions/{session_id}/remove", _remove_session, methods=["POST"]),
        # Opening a session is reading it, in every window (D63).
        Route("/api/sessions/{session_id}/read", _mark_read, methods=["POST"]),
        # Every path the account's conversations attach, which is what the
        # client sweeps its dropped files against (D102).
        Route("/api/attachments", _attachments, methods=["GET"]),
        Route("/api/branches/{branch_id}", _patch_branch, methods=["PATCH"]),
        # What a blocked branch is asking, so a window that did not start the
        # turn can draw the same card and answer it (D63). Its own read rather
        # than a field on the session, because it is the only thing here that is
        # not derived from the log and changes while a turn runs.
        Route("/api/branches/{branch_id}/pending", _pending, methods=["GET"]),
        Route("/api/branches/{branch_id}/fork", _fork_branch, methods=["POST"]),
        Route(
            "/api/branches/{branch_id}/tools/{tool_call_id}/image",
            _tool_image,
            methods=["GET"],
        ),
    ]


def _session_json(row: Any, *, snippet: str = "") -> dict[str, Any]:
    body = {
        "id": row.id,
        "title": row.title,
        "created_at": row.created_at.isoformat(),
        "closed_at": row.closed_at.isoformat() if row.closed_at else None,
        # When the row was last touched, which is what the `used` ordering sorts
        # by and what the client puts on the row and in its date heading. The
        # client does not compute it: the order and the date drawn beside a row
        # must be one fact, and the server is the only side that knows it.
        "last_activity": (row.last_activity or row.created_at).isoformat(),
        # Null when not pinned. The client draws a pinned section from this
        # rather than from its own list of ids.
        "pinned_at": row.pinned_at.isoformat() if row.pinned_at else None,
        # How many transcripts this conversation has, and which one opening the
        # row shows. The branch's settings are read with the branch, not here.
        "branch_count": row.branch_count,
        "open_branch_id": row.open_branch_id,
    }
    # Only when searching, and only when the match was in the conversation: a
    # title-only hit has no fragment to show and an empty string would render as
    # a blank second line.
    if snippet:
        body["snippet"] = snippet
    return body


def _pending_json(item: Any) -> dict[str, Any]:
    """One waiting call, shaped for the card the client already knows how to draw.

    The names are the frame's (`tool`, `arguments`, `preview`) rather than the
    inbox's, because the client has exactly one renderer for an approval card and
    it was written against `tool_approval_required`. A second shape would be a
    second description of the same card.
    """

    return {
        "tool_call_id": item.tool_call_id,
        "tool": item.tool,
        "arguments": item.arguments,
        # Null when the write could not be resolved. The card still asks: an
        # approval must be answerable when its preview is not (N97).
        "preview": item.preview,
    }


def _branch_json(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "created_at": row.created_at.isoformat(),
        # Provenance: the branch this one inherited from, and the inclusive seq
        # it took. Null on the root branch.
        "forked_from": row.forked_from,
        "forked_from_seq": row.forked_from_seq,
        "inherited_event_count": row.inherited_event_count,
        "working_dir": row.working_dir,
        "attachments": list(row.attachments),
        # `None` rather than a list when the user has not narrowed, so the client
        # can tell Select all from a selection that happens to name everything —
        # the first keeps tracking the registry, the second would not.
        "tool_selection": None if row.tool_selection is None else list(row.tool_selection),
        "model": row.model,
        "reasoning": row.reasoning,
        "approval": row.approval,
        # The first message said after the inherited prefix: the branch switcher
        # labels a fork with it. Null until the branch has said something.
        "first_message": row.first_message,
    }


async def _list_sessions(request: Request) -> Response:
    """This account's sessions, optionally filtered by what was said in them.

    One row per conversation, never one per branch. `?q=` searches across every
    branch and matches a deliberately-chosen title too. `?sort=` chooses the date
    the list is ordered by; the order is decided here because the listing is
    capped, exactly as D61 says.
    """

    from starlette.responses import JSONResponse

    query = (request.query_params.get("q") or "").strip()[:_MAX_QUERY]
    sort = request.query_params.get("sort") or "used"
    if sort not in SESSION_SORTS:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        store = AgentSessionStore(conn)
        if query:
            matches = await store.search_for_account(principal.account_id, query, sort=sort)
        else:
            matches = [
                (row, "")
                for row in await store.list_for_account(principal.account_id, sort=sort)
            ]

    return JSONResponse(
        {"sessions": [_session_json(row, snippet=snippet) for row, snippet in matches]}
    )


async def _attachments(request: Request) -> Response:
    """Every path any of the account's conversations still attaches.

    The client deletes a file dropped on its window once nothing attaches it
    (D102), and only the core holds every branch's list. Paths, not just the
    dropped ones: the core does not know which directory a client keeps them
    in once it has disconnected, and the client filters its own.
    """

    from starlette.responses import JSONResponse

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)
        paths = await BranchStore(conn).attached_paths(principal.account_id)
    return JSONResponse({"attachments": paths})


async def _active(request: Request) -> Response:
    """The account's working set: what it is doing, and what it was just in.

    One answer for every window and every device, because every fact in it is
    the account's: a turn running in a branch (`Services.turns`), a session
    opened within the account's window (`read_at`), the last thing said in it
    (`last_activity`), and whether a reply is newer than the last look
    (derived from the log against `read_at`). The client draws this list in this
    order and adds `open` to the one it has on screen; everything else is
    `idle`, `running`, `needs you` or `finished` — the same in every window
    (D63).

    In this order: newest activity first. `read_at` is not part of it, so
    clicking an old session puts it in the working set without lifting it over
    the one being worked in.

    A turn running *now* is added from the in-memory registry rather than found
    by the query, so it is present whatever the account's window says —
    including a window of zero, where the query matches nothing.

    What this deliberately does not say: which window holds a transcript, or
    what the turn has done step by step. Those arrive as frames on the socket
    that owns the turn (`GET /api/sessions/{id}` gives any window the log so
    far). A row here is a pointer, not a transcript.

    A GET, so it records nothing: reading your own work is not an act the chain
    has to answer for. The cross-account reads in the admin API are the ones
    that are audited.
    """

    from datetime import UTC, datetime, timedelta

    from starlette.responses import JSONResponse

    services = services_of(request)

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        store = AgentSessionStore(conn)
        cutoff = datetime.now(UTC) - timedelta(minutes=principal.active_minutes)
        rows = list(await store.active_for_account(principal.account_id, cutoff=cutoff))

        # One entry per session, and the most urgent branch wins when a fork is
        # running beside its source: the row can only say one thing.
        turns = services.live_turns(principal.account_id)
        live: dict[str, dict[str, Any]] = {}
        for turn in turns:
            current = live.get(turn["session_id"])
            if current is None or turn["state"] == "approval":
                live[turn["session_id"]] = turn

        # A session whose turn outlived the window is still active, and its row
        # is what carries the title; scoped like every other read, because a
        # session removed elsewhere must not come back as a row here.
        known = {row.id for row in rows}
        for session_id in live:
            if session_id in known:
                continue
            row = await store.visible_to(session_id, principal.account_id)
            if row is not None:
                rows.append(row)
                known.add(row.id)

        dated = sorted(
            ((row.last_activity or row.created_at, row) for row in rows),
            key=lambda item: (item[0], item[1].id),
            reverse=True,
        )

        sessions = []
        for when, row in dated:
            turn = live.get(row.id)
            sessions.append(
                {
                    "id": row.id,
                    "title": row.title,
                    # Where to open it: the branch the work is in, else the one
                    # the session would open on anyway.
                    "branch_id": turn["branch_id"] if turn else row.open_branch_id,
                    "state": (
                        turn["state"] if turn else ("finished" if row.unread else "idle")
                    ),
                    "tool": turn["tool"] if turn else None,
                    "last_activity": when.isoformat(),
                }
            )

    return JSONResponse({"sessions": sessions})


async def _mark_read(request: Request) -> Response:
    """Say that this session has been read — by the account, everywhere.

    Email's rule, and the reason the watermark is a column rather than a flag in
    a browser: opening a session is the act, and it is the account's, so the
    next poll clears `finished` in every window and on every device (D63).

    A route rather than a field on `PATCH`, because there is no second value to
    state — nobody marks a session unread — which is the same distinction that
    makes removal a route and a pin a field.
    """

    from starlette.responses import JSONResponse

    session_id = request.path_params["session_id"]

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
            require_csrf(request, principal)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        if not await AgentSessionStore(conn).mark_read(session_id, principal.account_id):
            return JSONResponse({"error": "not_found"}, status_code=404)

    return JSONResponse({"ok": True})


async def _create_session(request: Request) -> Response:
    from starlette.responses import JSONResponse

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
            require_csrf(request, principal)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        try:
            body = await request.json()
        except ValueError:
            body = {}

        title = body.get("title") if isinstance(body, dict) else None
        if title is not None and (not isinstance(title, str) or len(title) > _MAX_TITLE):
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        session, branch = await AgentSessionStore(conn).create(
            account_id=principal.account_id, title=title
        )

    # The session is the resource the caller created; its root branch rides
    # along because the socket it opens next is a branch, and a second GET to
    # learn that id would be a round trip for something already known.
    return JSONResponse(
        {**_session_json(session), "branch": _branch_json(branch)}, status_code=201
    )


async def _get_session(request: Request) -> Response:
    from starlette.responses import JSONResponse

    session_id = request.path_params["session_id"]

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        # `visible_to`, not `owned_by`: a session the user has removed is gone
        # from their view everywhere, including a tab that still holds its id.
        store = AgentSessionStore(conn)
        row = await store.visible_to(session_id, principal.account_id)
        if row is None:
            return JSONResponse({"error": "not_found"}, status_code=404)

        branches = BranchStore(conn)
        all_branches = await branches.list_for_session(session_id)
        wanted = request.query_params.get("branch")
        if wanted:
            branch = next((b for b in all_branches if b.id == wanted), None)
        else:
            # The branch with the newest activity: a fork just made, or the one
            # a message was last sent in. Re-read from the list so the branch
            # carries its `first_message`, which is what names it in the switcher.
            opened = await branches.open_for_session(session_id)
            branch = (
                next((b for b in all_branches if b.id == opened.id), opened)
                if opened is not None
                else None
            )
        if branch is None:
            return JSONResponse({"error": "not_found"}, status_code=404)

        events = await EventStore(conn).read(branch.id, kinds=PROJECTION_KINDS)
        row = replace(
            row,
            branch_count=len(all_branches),
            open_branch_id=branch.id,
        )

    return JSONResponse(
        {
            **_session_json(row),
            "branches": [_branch_json(item) for item in all_branches],
            "branch": _branch_json(branch),
            # The projection, not the raw log: this is what the model saw, which
            # is what a transcript should show. The browser also wants the tool
            # name, exit status and a write's diff, which `for_display` adds.
            "messages": project(events, for_display=True),
        }
    )


async def _pending(request: Request) -> Response:
    """The calls this branch's turn is blocked on, and what their cards say.

    The one read in the session surface that is not derived from the log: a
    pending approval exists only while a turn is waiting for it, and its preview
    is an act that did not happen and is deliberately not recorded. It is served
    from the turn's own inbox so a window that did not start the turn can draw
    the same card and answer it — the verdict is the account's, and the inbox is
    per branch, so any socket on the branch can already deliver one (D63, D12).

    Scoped like every other branch read: a branch of a session removed from this
    account's list is not reachable here either.
    """

    from starlette.responses import JSONResponse

    services = services_of(request)
    branch_id = request.path_params["branch_id"]

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        if await BranchStore(conn).visible_to(branch_id, principal.account_id) is None:
            return JSONResponse({"error": "not_found"}, status_code=404)

    return JSONResponse(
        {"pending": [_pending_json(item) for item in services.pending_approvals(branch_id)]}
    )


async def _tool_image(request: Request) -> Response:
    """The picture an image tool produced, as bytes.

    A separate route rather than a field on the transcript, for the reason the
    WebSocket frame omits it: a screenshot is megabytes of base64, and neither
    every frame nor every reopen should pay for one. The client renders an
    `<img>` pointing here only for a card that says it has an image.

    Scoped like every other read: `visible_to` means a branch of a session
    removed from the user's list is not reachable here either. The image itself
    is rooted by the session, so a fork may show a picture its source produced.
    """

    from starlette.responses import JSONResponse, Response

    branch_id = request.path_params["branch_id"]
    call_id = request.path_params["tool_call_id"]

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        branch = await BranchStore(conn).visible_to(branch_id, principal.account_id)
        if branch is None:
            return JSONResponse({"error": "not_found"}, status_code=404)

        events = await EventStore(conn).read(branch_id, kinds=("tool_result",))
        image = _image_for(events, call_id)
        stored = (
            await ImageStore(conn).get(image["sha256"])
            if image and image.get("sha256")
            else None
        )

    if stored is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    return Response(
        content=stored.data,
        media_type=stored.media_type,
        # Private, so a shared cache cannot hold one user's picture; a short life
        # bounds how long a removed session's image stays fetchable from an
        # intermediary, without re-reading the log for every render.
        headers={"cache-control": "private, max-age=3600"},
    )


def _image_for(events: Any, call_id: str) -> dict[str, Any] | None:
    """The `image` half of the tool result with this call id, if any."""

    for event in events:
        if event.kind != "tool_result":
            continue
        if event.payload.get("tool_call_id") != call_id:
            continue
        image = event.payload.get("image")
        if isinstance(image, dict) and image.get("sha256"):
            return image
    return None


async def _patch_session(request: Request) -> Response:
    """Change a session's title or pin.

    `PATCH` rather than `PUT`: these change named fields and leave the rest,
    including a transcript that no route should be able to replace. A field
    absent from the body is untouched, which is what lets the client send one
    without knowing the other.

    The working directory, attachments, tool selection, model, reasoning and
    approval are branch settings now, on `PATCH /api/branches/{id}`. They belong
    to the conversation the branch is having, not to the row that names it.
    """

    from starlette.responses import JSONResponse

    session_id = request.path_params["session_id"]

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
            require_csrf(request, principal)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        if not isinstance(body, dict) or not any(f in body for f in ("title", "pinned")):
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        store = AgentSessionStore(conn)
        if await store.owned_by(session_id, principal.account_id) is None:
            return JSONResponse({"error": "not_found"}, status_code=404)

        if "title" in body:
            title = body["title"]
            if not isinstance(title, str):
                return JSONResponse({"error": "invalid_request"}, status_code=400)

            # Collapsed the same way the derived title is, so a pasted newline
            # does not become a list row with a newline in it. The client cannot
            # be trusted to have done it, and this is the untrusted surface (D1).
            title = " ".join(title.split())[:_MAX_TITLE]
            if not title:
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            await store.set_title(session_id, principal.account_id, title)

        # Pinning states a value rather than performing a one-way act, which is
        # why it is a field here and removal is a route of its own: there is a
        # second value (false), so `PATCH {pinned: false}` says something a route
        # could not.
        if "pinned" in body:
            pinned = body["pinned"]
            if not isinstance(pinned, bool):
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            await store.set_pinned(session_id, principal.account_id, pinned)

        row = await store.owned_by(session_id, principal.account_id)

    return JSONResponse(
        {
            "id": row.id,
            "title": row.title,
            # The field this route can change and the client cannot infer: a pin
            # set from one tab has to read as set in the other's next repaint.
            "pinned_at": row.pinned_at.isoformat() if row.pinned_at else None,
        }
    )


async def _patch_branch(request: Request) -> Response:
    """Change a branch's settings, or the choices its next turn runs with.

    **The working directory is a hint, not a grant.** It must be inside what
    policy already grants for reading, checked against freshly composed policy.
    The attachments and tool selection get the same treatment, for the same
    reason: this route is where a staged value becomes stored state, and a stale
    grant must not be preserved by a PATCH that merely re-sends an older value.

    The model and reasoning are validated against the live catalog, not against
    what the client believed: a model that has been revoked or uninstalled is
    refused by name, and a level that model does not declare is refused with the
    levels it does. Approval is a choice within the operator's ceiling, so a
    request above it is refused here as well as clamped at the point of consent.
    """

    from starlette.responses import JSONResponse

    services = services_of(request)
    branch_id = request.path_params["branch_id"]

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
            require_csrf(request, principal)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        named = (
            "working_dir",
            "attachments",
            "tool_selection",
            "model",
            "reasoning",
            "approval",
        )
        if not isinstance(body, dict) or not any(field in body for field in named):
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        branches = BranchStore(conn)
        branch = await branches.visible_to(branch_id, principal.account_id)
        if branch is None:
            return JSONResponse({"error": "not_found"}, status_code=404)

        policy = await preview_policy(
            principal.name,
            source=services.policy_source(),
            directory=services.directory,
            grants=GrantStore(conn),
        )

        if "working_dir" in body:
            working_dir = body["working_dir"]
            if working_dir is not None:
                if not isinstance(working_dir, str):
                    return JSONResponse({"error": "invalid_request"}, status_code=400)

                if not within_reach(
                    working_dir,
                    policy,
                    home=acting_home(services, principal),
                    data_dirs=services.settings.home_data_dirs,
                ):
                    # Named rather than a bare 400: the user picked a real
                    # directory and the answer is that policy does not reach it,
                    # which is a different problem from a malformed request.
                    return JSONResponse(
                        {
                            "error": "outside_policy",
                            "detail": (
                                f"{working_dir} is not inside any path this account "
                                "may read. An administrator grants reach in a role, "
                                "on the Roles page."
                            ),
                        },
                        status_code=403,
                    )
            await branches.set_working_dir(branch_id, working_dir)

        if "attachments" in body:
            attachments = body["attachments"]
            # The whole list, every time. The client sends what it just rendered,
            # so there is no add/remove ordering to get wrong — and clearing is
            # `[]` rather than a third verb.
            if not isinstance(attachments, list) or len(attachments) > _MAX_ATTACHMENTS:
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            if not all(isinstance(entry, str) and entry for entry in attachments):
                return JSONResponse({"error": "invalid_request"}, status_code=400)

            # Every path, not just the new one. The client could have sent a list
            # containing something it never showed, and re-checking all of them
            # means a narrowed policy cannot be preserved by a PATCH that merely
            # re-sends an older list.
            for entry in attachments:
                if not attachable(
                    entry,
                    policy,
                    home=acting_home(services, principal),
                    drops=acting_drops(services, principal),
                    data_dirs=services.settings.home_data_dirs,
                ):
                    return JSONResponse(
                        {
                            "error": "outside_policy",
                            "detail": (
                                f"{entry} is not inside any path this account may "
                                "read, nor a file dropped on this client's window. An "
                                "administrator grants reach in a role, on the Roles page."
                            ),
                        },
                        status_code=403,
                    )

            # De-duplicated, order preserved: attaching the same file twice is a
            # double-click, not an instruction to mention it twice in the prompt.
            seen: dict[str, None] = {}
            for entry in attachments:
                seen.setdefault(entry, None)
            await branches.set_attachments(branch_id, list(seen))

        if "tool_selection" in body:
            selection = body["tool_selection"]
            # `null` is Select all, and it is a different statement from `[]`.
            # Null restores "every tool policy grants", which keeps working as
            # the registry changes; an empty list is a user who switched
            # everything off, which is a conversation with no tools and a
            # legitimate thing to ask for.
            if selection is not None:
                if not isinstance(selection, list):
                    return JSONResponse({"error": "invalid_request"}, status_code=400)
                if not all(isinstance(entry, str) and entry for entry in selection):
                    return JSONResponse({"error": "invalid_request"}, status_code=400)

                # Intersected against the live gate rather than refused for
                # naming something ungranted: a tool outside the grant is a
                # *stale checkbox*, and the honest response to "use these eleven"
                # when ten remain is to store the ten. Dropping it is safe
                # because this is a narrowing either way — the advertisement
                # intersects with `grantable` and the gate re-checks every
                # proposal (D6).
                allowed = set(gate.grantable(policy, services.registry))
                seen_tools: dict[str, None] = {}
                for entry in selection:
                    if entry in allowed:
                        seen_tools.setdefault(entry, None)
                selection = list(seen_tools)

            await branches.set_tool_selection(branch_id, selection)

        if "model" in body or "reasoning" in body:
            model = body.get("model", branch.model)
            reasoning = body.get("reasoning", branch.reasoning)
            if model is not None:
                if not isinstance(model, str) or not model:
                    return JSONResponse({"error": "invalid_request"}, status_code=400)
                if model not in policy.models:
                    return JSONResponse({"error": "model_not_granted"}, status_code=403)
                entry = services.model_catalog().describe([model]).get(model)
                if entry is None:
                    return JSONResponse({"error": "model_unavailable"}, status_code=400)
                levels = (entry.get("reasoning") or {}).get("levels") or []
                if reasoning is not None:
                    if not isinstance(reasoning, str) or reasoning not in levels:
                        return JSONResponse(
                            {"error": "invalid_reasoning", "allowed": list(levels)},
                            status_code=400,
                        )
            elif reasoning is not None:
                return JSONResponse({"error": "invalid_reasoning"}, status_code=400)
            await branches.set_model_reasoning(branch_id, model=model, reasoning=reasoning)

        if "approval" in body:
            approval = body["approval"]
            if approval is not None:
                if approval not in AUTO_ACCEPT_MODES:
                    return JSONResponse(
                        {"error": "invalid_request", "allowed": list(AUTO_ACCEPT_MODES)},
                        status_code=400,
                    )
                if auto_accept_rank(approval) > auto_accept_rank(policy.auto_accept_max):
                    # Refused by name, because the fix is an operator raising
                    # the ceiling — a different problem from a malformed
                    # request, and one the user can act on by asking.
                    return JSONResponse(
                        {
                            "error": "above_ceiling",
                            "detail": (
                                f"this account's approval ceiling is {policy.auto_accept_max!r}"
                            ),
                        },
                        status_code=403,
                    )
            await branches.set_approval(branch_id, approval)

        fresh = await branches.visible_to(branch_id, principal.account_id)
        # Re-read through the list so the branch's `first_message` — the label
        # the switcher shows — comes back with the settings it just changed.
        listed = await branches.list_for_session(fresh.session_id)
        fresh = next((item for item in listed if item.id == branch_id), fresh)
        effective = policy.clamped_auto_accept(fresh.approval)

    body = _branch_json(fresh)
    body["effective_approval"] = effective
    return JSONResponse(body)


async def _fork_branch(request: Request) -> Response:
    """Fork this branch into a sibling of the same session.

    The boundary is a completed turn; `at_seq` and `side` say which one. The
    source's settings are re-checked against live policy before they are copied:
    a fork may not resurrect a directory or a tool the account has since lost.
    The branch itself is created, copied and audited by `ops.fork`, and the
    session row is untouched — no new list entry, no new title or pin.
    """

    from starlette.responses import JSONResponse

    services = services_of(request)
    branch_id = request.path_params["branch_id"]

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
            require_csrf(request, principal)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        try:
            body = await request.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        at_seq = body.get("at_seq")
        side = body.get("side")
        if at_seq is not None and (isinstance(at_seq, bool) or not isinstance(at_seq, int)):
            return JSONResponse({"error": "invalid_boundary"}, status_code=400)

        branches = BranchStore(conn)
        source = await branches.visible_to(branch_id, principal.account_id)
        if source is None:
            return JSONResponse({"error": "not_found"}, status_code=404)

        policy = await preview_policy(
            principal.name,
            source=services.policy_source(),
            directory=services.directory,
            grants=GrantStore(conn),
        )
        home = acting_home(services, principal)

        warnings: list[str] = []
        working_dir = source.working_dir
        if working_dir and not within_reach(
            working_dir, policy, home=home, data_dirs=services.settings.home_data_dirs
        ):
            warnings.append(
                f"{working_dir} is outside what policy now reaches; "
                "the fork starts without a working directory."
            )
            working_dir = None

        attachments = []
        drops = acting_drops(services, principal)
        for entry in source.attachments:
            if attachable(
                entry,
                policy,
                home=home,
                drops=drops,
                data_dirs=services.settings.home_data_dirs,
            ):
                attachments.append(entry)
            else:
                warnings.append(f"{entry} is outside what policy now reaches and was dropped.")
        if len(attachments) != len(source.attachments):
            warnings.append(
                f"{len(source.attachments) - len(attachments)} attachment(s) dropped."
            )

        tool_selection = source.tool_selection
        if tool_selection is not None:
            allowed = set(gate.grantable(policy, services.registry))
            kept = [name for name in tool_selection if name in allowed]
            if kept != list(tool_selection):
                warnings.append("A revoked tool was dropped from the fork's selection.")
            tool_selection = kept

        try:
            result = await fork_op.fork(
                actor=principal.name,
                session_id=source.session_id,
                source=source,
                at_seq=at_seq,
                side=side,
                working_dir=working_dir,
                attachments=tuple(attachments),
                tool_selection=tool_selection,
                events=EventStore(conn),
                branches=branches,
                audit=AuditStore(conn),
                mirror=services.settings.audit_mirror_path,
            )
        except fork_op.BoundaryError as exc:
            status = 409 if exc.code == "turn_open" else 400
            return JSONResponse({"error": exc.code, "detail": exc.detail}, status_code=status)

    return JSONResponse(
        {
            "branch": _branch_json(result["branch"]),
            "through_seq": result["through_seq"],
            "warnings": warnings,
        },
        status_code=201,
    )


async def _close_session(request: Request) -> Response:
    from starlette.responses import JSONResponse

    services = services_of(request)
    session_id = request.path_params["session_id"]

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
            require_csrf(request, principal)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        store = AgentSessionStore(conn)
        closed = await store.close(session_id, principal.account_id)
        branch_ids = [b.id for b in await BranchStore(conn).list_for_session(session_id)]

    if closed:
        services.drop_inboxes(branch_ids)
    return JSONResponse({"ok": closed}, status_code=200 if closed else 404)


async def _remove_session(request: Request) -> Response:
    """Take a session and every branch off this user's list, on every device.

    Its own route rather than a field on `PATCH`, because it is not an editable
    field. PATCH states a new value for something the client may state again
    differently — a title, a pin. Removal has no second value: there is no
    un-remove, by design.

    Deliberately *not* `DELETE`, which this surface already uses for closing.
    Closing ends the conversation and starts the retention clock; removing ends
    nothing. A session can be removed while one of its branches is still live,
    and the rows, their transcripts and the audit entries naming them all
    survive for the operator.
    """

    from starlette.responses import JSONResponse

    services = services_of(request)
    session_id = request.path_params["session_id"]

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
            require_csrf(request, principal)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        store = AgentSessionStore(conn)
        # Ownership first, so removing someone else's session is a 404 rather
        # than the 200 a bare rowcount of 0 would be indistinguishable from.
        if await store.owned_by(session_id, principal.account_id) is None:
            return JSONResponse({"error": "not_found"}, status_code=404)

        await store.mark_removed(session_id, principal.account_id)
        branch_ids = [b.id for b in await BranchStore(conn).list_for_session(session_id)]

    # Not conditional on `mark_removed`: it returns False for a session that was
    # already removed, and a second click must still read as success — the user
    # asked for a state the server is in.
    services.drop_inboxes(branch_ids)
    return JSONResponse({"ok": True})
