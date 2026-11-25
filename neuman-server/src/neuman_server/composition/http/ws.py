"""WebSocket bridge: stream one branch's turn out, feed its inbox in.

This is the transport half of docs/decisions.md D12. The turn runs as an asyncio task; the
receive loop runs concurrently and posts into the branch's inbox. Neither blocks
the other, so a message arriving mid-turn is *delivered* rather than dropped —
which is precisely what v1 could not do, because its approval waited on a
`concurrent.futures.Future` inside a worker thread and anything else arriving
meanwhile was discarded.

A socket is opened for a **branch**, not a session: the branch is the transcript
the turn appends to, and two branches of one session may have turns running at
once — that is what a fork is for. The session owns the title and the rail row;
the branch owns the settings and the log.

The socket carries four inbound kinds — `message` (start a turn), `approval`,
`steer`, `interrupt` — and streams the harness's events out verbatim.

Authentication happens on the upgrade: the cookie is checked, and Origin is
checked explicitly because browsers do not apply CORS to WebSockets.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from ...adapters.persistence import (
    AccountStore,
    AgentSessionStore,
    AuditStore,
    BranchStore,
    EventStore,
    GrantStore,
    ImageStore,
    SessionStore,
    UsageStore,
)
from ...decision.harness import Actor, Harness, HarnessConfig
from ...decision.harness.inbox import InboxMessage
from ...decision.identity import resolve_session
from ...decision.policy.ops import preview_policy
from ...foundation import get_logger
from .deps import COOKIE_NAME, machine_facts

if TYPE_CHECKING:  # pragma: no cover
    from starlette.websockets import WebSocket

__all__ = ["ws_routes"]

_log = get_logger("neuman.server.http.ws")

_INBOUND = frozenset({"message", "approval", "steer", "interrupt"})


def ws_routes() -> list[Any]:
    from starlette.routing import WebSocketRoute

    return [WebSocketRoute("/ws/branches/{branch_id}", _branch_socket)]


async def _branch_socket(websocket: WebSocket) -> None:
    services = websocket.app.state.services
    branch_id = websocket.path_params["branch_id"]

    if not _origin_ok(websocket, services.settings):
        await websocket.close(code=4403)
        return

    token = websocket.cookies.get(COOKIE_NAME)
    if not token:
        await websocket.close(code=4401)
        return

    async with services.database.connection() as conn:
        principal = await resolve_session(
            token,
            sessions=SessionStore(conn),
            accounts=AccountStore(conn),
        )
        if principal is None:
            await websocket.close(code=4401)
            return
        if principal.must_change_password:
            # The password change is an HTTP act; a socket opened before it
            # would be a way around the block.
            await websocket.close(code=4403)
            return

        # `visible_to` rather than `owned_by`: a branch of a session the user
        # removed is not reconnectable, so a tab holding a stale id gets 4404
        # ("no such branch") instead of resuming a conversation the user took
        # off their list on another device. The *running* turn in a branch
        # removed mid-flight still resolves it with `owned_by` below — removing
        # ends the viewing, not the turn.
        branch = await BranchStore(conn).visible_to(branch_id, principal.account_id)
        if branch is None:
            await websocket.close(code=4404)
            return

    await websocket.accept()
    inbox = services.inbox_for(branch_id)
    turn: asyncio.Task[Any] | None = None

    try:
        while True:
            message = await websocket.receive_json()
            kind = message.get("type")
            if kind not in _INBOUND:
                await websocket.send_json({"type": "error", "code": "unknown_message"})
                continue

            if kind == "message":
                # One turn per *branch*, not per socket (D63). The socket that
                # started a turn owns its life — this handler cancels it in the
                # `finally` below — but a second connection on the same branch
                # must not start a second turn while the first is mid-dispatch.
                # Its request would project the first turn's assistant message
                # before that turn has written its `tool` replies, and every
                # provider refuses the half-answered history (N179). The
                # registry is `Services.turns`, so the check is the same one
                # the overview reports.
                if services.running_turn(branch_id) is not None:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "code": "turn_in_flight",
                            "message": (
                                "a turn is already running in this branch; "
                                "wait for it to finish"
                            ),
                        }
                    )
                    continue
                inbox.clear_interrupt()
                turn = asyncio.create_task(
                    _run_turn(websocket, services, principal, branch_id, message, inbox)
                )
                # The session id and the account id travel with the turn so the
                # rail can answer "what is this account running" without a query
                # per poll, and the inbox travels with it because that is the
                # one a blocked turn is actually waiting on (D63).
                services.begin_turn(
                    branch_id,
                    account=principal.name,
                    account_id=principal.account_id,
                    session_id=branch.session_id,
                    inbox=inbox,
                    task=turn,
                )
            else:
                inbox.post(InboxMessage(kind=kind, payload=message))

    except Exception as exc:  # noqa: BLE001 - a disconnect is the normal exit
        _log.debug("websocket closed", fields={"branch": branch_id, "reason": str(exc)})
    finally:
        if turn is not None and not turn.done():
            # A vanished client must not leave a turn burning budget. The
            # registry entry outlives the socket only until the cancellation
            # lands, so a reconnect in that window still sees a turn winding
            # down rather than starting a second one over it.
            inbox.post(InboxMessage(kind="interrupt"))
            turn.cancel()


# Long enough to be recognisable, short enough for a list row. A title is a
# label rather than a summary: the transcript is one click away, so the only job
# here is telling two sessions apart.
TITLE_MAX = 60


def _derive_title(message: str) -> str:
    """A session name from its opening message.

    Collapses whitespace so a pasted multi-line prompt does not become a title
    with newlines in it, and breaks on a word boundary when it can — a label cut
    mid-word reads as corrupted rather than truncated.
    """

    flat = " ".join(message.split())
    if len(flat) <= TITLE_MAX:
        return flat or "untitled"

    cut = flat[:TITLE_MAX]
    space = cut.rfind(" ")
    # Only honour a late space: an early one would leave a title of two words
    # when the limit allowed ten.
    if space > TITLE_MAX * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,.;:") + "…"


async def _run_turn(
    websocket: WebSocket,
    services: Any,
    principal: Any,
    branch_id: str,
    message: dict[str, Any],
    inbox: Any,
) -> None:
    """Run one turn in one branch, streaming its events to this socket."""

    content = message.get("content")
    model = message.get("model")
    if not isinstance(content, str) or not content or not isinstance(model, str) or not model:
        await websocket.send_json({"type": "error", "code": "invalid_request"})
        return

    # Optional: absent means the model's declared default. Shape-checked here
    # and *validated* in the harness, against the catalog entry — only the
    # resolved target knows which levels are real, and a surface that guessed
    # would be a second opinion about the catalog.
    reasoning = message.get("reasoning")
    if reasoning is not None and (not isinstance(reasoning, str) or not reasoning):
        await websocket.send_json({"type": "error", "code": "invalid_request"})
        return

    async with services.database.connection() as naming:
        branches = BranchStore(naming)
        # `owned_by`, not `visible_to`: a session removed while its branch is
        # running still has a title to derive and events to append. The branch
        # row is also where this turn's settings live.
        branch = await branches.owned_by(branch_id, principal.account_id)
        if branch is None:
            await websocket.send_json({"type": "error", "code": "not_found"})
            return
        store = AgentSessionStore(naming)
        # Name the session from the first message in any branch, if it has no
        # name yet. Derived rather than generated: asking the model for a title
        # costs a request per session and fails when the gateway is down.
        await store.title_if_unnamed(
            branch.session_id, principal.account_id, _derive_title(content)
        )
        account = await AccountStore(naming).by_id(principal.account_id)
        instructions = account.instructions if account is not None else ""
        # What the branch remembers for next time. Written before the turn so a
        # failure still leaves the choice the user made, and read by the next
        # open on any device. The frame remains the authority for *this* turn;
        # the row is the memory.
        await branches.set_model_reasoning(branch_id, model=model, reasoning=reasoning)
        branch = await branches.owned_by(branch_id, principal.account_id)
        if branch is None:  # the session was purged while this turn started
            await websocket.send_json({"type": "error", "code": "not_found"})
            return

    async def emit(event: dict[str, Any]) -> None:
        try:
            await websocket.send_json(event)
        except Exception:  # noqa: BLE001 - the client may have gone
            inbox.post(InboxMessage(kind="interrupt"))

    settings = services.settings

    async with services.database.connection() as conn:
        grants = GrantStore(conn)
        source = services.policy_source()

        # Where this turn runs. A branch names its machine (D112); a branch
        # written before machines existed names none, and adopts the one this
        # person is connected from now — once, pinned from then on. That is the
        # migration path as well as the rule: it is what the appliance did
        # before the noun existed, and it stops being ambiguous the first time
        # the conversation is used.
        machine = branch.machine
        if machine is None:
            machine = services.execution.newest_machine(principal.name)
            if machine is not None:
                await BranchStore(conn).set_machine(branch_id, machine)

        # One read for the home, the drops and what the operator says is
        # installed on that machine. Live where it can be, the last hello where
        # it cannot, so a sleeping laptop composes the same policy as an awake
        # one.
        facts = await machine_facts(conn, services, principal, machine)

        async def policy_for(name: str):
            # Re-composed on every call, so a revocation lands mid-turn.
            return await preview_policy(
                name, source=source, directory=services.directory, grants=grants
            )

        harness = Harness(
            gateway=services.gateway,
            execution=services.execution,
            registry=services.registry,
            audit_repository=AuditStore(conn),
            event_repository=EventStore(conn),
            usage=UsageStore(conn),
            images=ImageStore(conn),
            policy_for=policy_for,
            # The catalog lookup, over the runtime baseline (D35). Resolved per
            # turn, so a model an administrator adds or edits is used on the
            # next turn without a restart.
            resolve_model=services.model_catalog().resolve,
            config=HarnessConfig(
                tool_wait_ms=settings.tool_wait_ms,
                approval_timeout_s=settings.approval_timeout_s,
                max_tokens=settings.max_tokens,
                operator_prompt=services.operator_prompt,
                agent_name=services.agent_name,
                build_id=settings.build_id,
            ),
            context=services.context,
            server_native=_server_native_handlers(conn, branch_id),
            emit=emit,
            audit_mirror=settings.audit_mirror_path,
        )

        await harness.run_turn(
            branch_id=branch_id,
            session_id=branch.session_id,
            actor=Actor(
                account_id=principal.account_id,
                name=principal.name,
                machine=machine or "",
                machine_notes=facts.notes,
                home=facts.home,
            ),
            message=content,
            model=model,
            inbox=inbox,
            working_dir=branch.working_dir,
            attachments=list(branch.attachments),
            account_prompt=instructions,
            reasoning=reasoning,
            approval=branch.approval,
            tool_selection=branch.tool_selection,
        )


def _server_native_handlers(conn: Any, current_branch: str) -> dict[str, Any]:
    """Tools the core answers itself, never dispatching (docs/decisions.md D13).

    One tool, and it is the *user-facing* one: a person asking about their own
    sessions. An admin reading anyone's goes through `neuman-admin session`,
    which is the one admin surface — so there is no second implementation here
    and no authority check, because this handler has no wider scope to gate.

    `session_query` is branch-aware. A model asking about "this conversation"
    means the branch it is running in, which is why the handler is built with
    that branch: omitting `branch` answers for it. Naming another branch of the
    same account is allowed — the scope is the actor's own rows either way.
    """

    import json

    from ...decision.ops import sessions as session_ops

    async def session_query(actor: Actor, args: dict[str, Any], branch_id: str) -> str:
        branches = BranchStore(conn)
        action = args.get("action") or "list"

        if action in ("events", "event", "kinds"):
            wanted = str(args.get("branch") or current_branch)
            # Scoped by account, so someone else's branch is indistinguishable
            # from one that does not exist. A refusal that distinguished them
            # would be a probe for ids.
            branch = await branches.owned_by(wanted, actor.account_id)
            if branch is None:
                return json.dumps({"error": "branch not found"})

            store_events = EventStore(conn)

            if action == "kinds":
                return json.dumps(
                    session_ops.kinds(await store_events.summary(wanted), branch_id=wanted)
                )

            if action == "event":
                try:
                    seq = int(args.get("seq", -1))
                except (TypeError, ValueError):
                    return json.dumps({"error": "seq must be an integer"})
                return json.dumps(
                    session_ops.event(
                        await store_events.at_seq(wanted, seq),
                        branch_id=wanted,
                        seq=seq,
                    )
                )

            try:
                last = int(args["last"]) if args.get("last") is not None else None
                after = int(args.get("after", 0))
                limit = int(args.get("limit", 50))
            except (TypeError, ValueError):
                return json.dumps({"error": "last, after and limit must be integers"})

            kind = args.get("kind")
            return json.dumps(
                session_ops.events(
                    await store_events.window(
                        wanted,
                        kinds=session_ops.split_kinds(str(kind) if kind else None),
                        last=last,
                        after=after,
                        limit=limit,
                    ),
                    branch_id=wanted,
                )
            )

        # By creation. The rail's chosen order and its pins are the reader's
        # statements about their own list; a tool answering "what sessions do I
        # have" has no rail to read them from, and a stable order is worth more
        # there than one that follows a preference.
        rows = await AgentSessionStore(conn).list_for_account(actor.account_id, sort="created")
        out = []
        for row in rows:
            out.append(
                {
                    "id": row.id,
                    "title": row.title,
                    "created_at": row.created_at.isoformat(),
                    "branches": [
                        {
                            "id": item.id,
                            "created_at": item.created_at.isoformat(),
                            "forked_from": item.forked_from,
                            "first_message": item.first_message,
                        }
                        for item in await branches.list_for_session(row.id)
                    ],
                }
            )
        return json.dumps({"sessions": out})

    return {"session_query": session_query}


def _origin_ok(websocket: WebSocket, settings: Any) -> bool:
    """Check Origin on the upgrade; browsers do not apply CORS to WebSockets."""

    origin = websocket.headers.get("origin")
    if origin is None:
        # A non-browser client. The session cookie still governs.
        return True
    if origin in settings.allowed_origins:
        return True
    host = websocket.headers.get("host")
    return bool(host and origin.endswith(f"//{host}"))
