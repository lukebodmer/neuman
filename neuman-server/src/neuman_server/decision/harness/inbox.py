"""The steering and approval inbox.

The loop is built around this from day one (docs/decisions.md D12), because retrofitting
concurrency into an agent loop is the expensive direction. v1 learned this the
hard way: approval was a synchronous callback that blocked a worker thread until
the client answered, and any message arriving mid-turn was dropped, so
hard-interrupt steering was simply absent.

Here, decisions *arrive as messages*:

* ``approval`` — a verdict on a specific pending tool call.
* ``steer`` — extra user input to fold into the conversation at the next safe
  point, without ending the turn.
* ``interrupt`` — stop now; cancel anything in flight.

Two properties the loop depends on:

**Fail closed.** An approval that never arrives times out as a *denial*, never
as an allow. A disconnected client must not become a grant.

**Interrupts are observable without awaiting.** :meth:`interrupted` is a plain
check the loop can make between chunks, so a long stream aborts promptly rather
than after the next await point resolves.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

from ...foundation.logging import get_logger

__all__ = ["Approval", "Inbox", "InboxMessage", "PendingApproval", "Steer"]

_log = get_logger("neuman.server.harness.inbox")

MessageKind = Literal["approval", "steer", "interrupt"]


@dataclass(frozen=True, slots=True)
class InboxMessage:
    kind: MessageKind
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Approval:
    tool_call_id: str
    approved: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class PendingApproval:
    """One call the turn is blocked on, and what the card for it needs.

    This is the card, carried rather than described: `tool` names what is being
    asked about, `arguments` is what the model asked for, and `preview` is the
    diff the tool produced when it was asked what the write would do. The
    preview cannot be recomputed by whoever draws the card — it is a second real
    dispatch to the human's machine, and a second one could describe a different
    write than the call the verdict applies to (D63, `_preview_write`).

    Held in memory with the wait, never in the log: a preview is an act that did
    not happen, and an entry for it would make the record claim two writes where
    one occurred.
    """

    tool_call_id: str
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    preview: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Steer:
    content: str


class Inbox:
    """Per-branch mailbox for decisions arriving while a turn runs."""

    __slots__ = ("_approvals", "_branch_id", "_interrupt", "_pending", "_prompts", "_steers")

    def __init__(self, branch_id: str) -> None:
        self._branch_id = branch_id
        self._steers: asyncio.Queue[Steer] = asyncio.Queue()
        # One future per awaited tool call, resolved by a matching approval.
        self._pending: dict[str, asyncio.Future[Approval]] = {}
        # What each pending call is asking, for the surface that draws its card
        # (D63). Same lifetime as `_pending`, because a card for a call nobody
        # is waiting on is a prompt nobody can answer.
        self._prompts: dict[str, PendingApproval] = {}
        # Approvals that arrived before the loop asked for them. A client that
        # pre-approves, or races the prompt, must not be ignored.
        self._approvals: dict[str, Approval] = {}
        self._interrupt = asyncio.Event()

    @property
    def branch_id(self) -> str:
        return self._branch_id

    # -- producer side (the transport) ---------------------------------------

    def post(self, message: InboxMessage) -> None:
        """Deliver a message. Never blocks; safe from a transport callback."""

        if message.kind == "interrupt":
            self._interrupt.set()
            # Unblock anything waiting on an approval: an interrupt denies every
            # pending decision rather than leaving the turn hanging.
            for call_id, future in list(self._pending.items()):
                if not future.done():
                    future.set_result(Approval(call_id, approved=False, reason="interrupted"))
            _log.info("interrupt received", fields={"branch": self._branch_id})

        elif message.kind == "approval":
            approval = Approval(
                tool_call_id=str(message.payload.get("tool_call_id", "")),
                approved=bool(message.payload.get("approved")),
                reason=str(message.payload.get("reason", "")),
            )
            future = self._pending.get(approval.tool_call_id)
            if future is not None and not future.done():
                future.set_result(approval)
            else:
                self._approvals[approval.tool_call_id] = approval

        elif message.kind == "steer":
            self._steers.put_nowait(Steer(content=str(message.payload.get("content", ""))))

    # -- consumer side (the loop) --------------------------------------------

    def interrupted(self) -> bool:
        """Whether an interrupt is pending. Cheap; check between chunks."""

        return self._interrupt.is_set()

    def clear_interrupt(self) -> None:
        self._interrupt.clear()

    def drain_steers(self) -> list[Steer]:
        """Every steering message waiting right now, in arrival order."""

        drained: list[Steer] = []
        while True:
            try:
                drained.append(self._steers.get_nowait())
            except asyncio.QueueEmpty:
                return drained

    async def await_approval(
        self,
        tool_call_id: str,
        *,
        timeout_s: float,
        tool: str = "",
        arguments: dict[str, Any] | None = None,
        preview: dict[str, Any] | None = None,
    ) -> Approval:
        """Wait for a verdict on one tool call. Times out as a denial.

        What the call is asking is recorded for :meth:`pending` while the wait
        lasts: a verdict is an informed act, so the window that answers one has
        to be shown the same card the asking window was (D63). The arguments and
        the preview are defaulted because an early verdict can arrive before the
        loop asks (see `_approvals`), and because a caller that only wants the
        verdict has nothing to say about the prompt.
        """

        early = self._approvals.pop(tool_call_id, None)
        if early is not None:
            return early

        if self._interrupt.is_set():
            return Approval(tool_call_id, approved=False, reason="interrupted")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Approval] = loop.create_future()
        self._pending[tool_call_id] = future
        self._prompts[tool_call_id] = PendingApproval(
            tool_call_id=tool_call_id,
            tool=tool,
            arguments=dict(arguments or {}),
            preview=preview,
        )
        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        except TimeoutError:
            _log.warning(
                "approval timed out; denying",
                fields={"branch": self._branch_id, "tool_call_id": tool_call_id},
            )
            return Approval(tool_call_id, approved=False, reason="timeout")
        finally:
            self._pending.pop(tool_call_id, None)
            self._prompts.pop(tool_call_id, None)

    def pending(self) -> list[PendingApproval]:
        """The calls currently awaiting a verdict, oldest first by id.

        Read by the admin overview, by the rail's live read and by the card a
        second window draws, which is what makes a blocked turn visible — and
        answerable — anywhere the account is signed in (D63). Empty for the
        moment between the prompt going out and the loop waiting.
        """

        return [self._prompts[call_id] for call_id in sorted(self._prompts)]
