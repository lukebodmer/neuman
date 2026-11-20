"""Fork one branch into another inside the same session.

**One implementation, two surfaces** (D9). The HTTP route calls this with the
branch it resolved for the signed-in user; another surface may call the same
function with a branch it has already resolved. Scope is the caller's business, as
it is in `ops/sessions.py`: this function takes rows that are already resolved.

Two things are deliberate:

* **The child inherits conversation, not history.** `EventStore.copy_prefix`
  copies the projection and the turn boundaries, not the requests, chunks,
  proposals or decisions. The source keeps the governance record; the child
  cannot claim an approval happened in it.
* **The fork is audited in the same transaction as the copy.** A fork that
  happened without an entry would be an unrecorded act on a governed record.
"""

from __future__ import annotations

from typing import Any

from ..audit import chain
from ..harness import boundary
from ..harness.boundary import BoundaryError
from ..harness.projection import PROJECTION_KINDS

__all__ = ["BoundaryError", "fork"]


async def fork(
    *,
    actor: str,
    session_id: str,
    source: Any,
    at_seq: int | None,
    side: str | None,
    working_dir: str | None,
    attachments: tuple[str, ...],
    tool_selection: tuple[str, ...] | None,
    events: Any,
    branches: Any,
    audit: Any,
    mirror: str | None,
) -> dict[str, Any]:
    """Create a sibling branch holding the source's prefix through a boundary.

    `working_dir`, `attachments` and `tool_selection` are the values the caller
    already checked against live policy. They are passed in rather than copied
    here because reach is a decision and decisions do not live in this module
    (D7); the branch's model, reasoning and approval are the source's own
    choices and are copied as they are.
    """

    # A branch whose turn is running has events after its last `turn_complete`
    # (the opening message, chunks, proposals). Resolving against the same rows
    # the copy will read is what makes a fork during a turn stop at the last
    # thing that finished rather than at a half-written tool run.
    rows = await events.read(source.id, kinds=PROJECTION_KINDS)
    resolved = boundary.resolve(rows, at_seq=at_seq, side=side)

    new = await branches.create(
        session_id=session_id,
        forked_from=source.id,
        forked_from_seq=resolved.through,
        inherited_event_count=0,
        working_dir=working_dir,
        attachments=attachments,
        tool_selection=tool_selection,
        model=source.model,
        reasoning=source.reasoning,
        approval=source.approval,
    )

    copied = await events.copy_prefix(
        session_id=session_id,
        source_branch_id=source.id,
        target_branch_id=new.id,
        through_seq=resolved.through,
        at_seq=resolved.at_seq,
        side=resolved.side,
    )

    # The marker is seq 1 and the copied rows follow, so the prefix a reader must
    # skip to find this branch's own first message is one more than the copy.
    await branches.set_inherited(new.id, 1 + copied)

    await chain.append(
        audit,
        actor=actor,
        action="session.fork",
        payload={
            "session": session_id,
            "from_branch": source.id,
            "to_branch": new.id,
            "from_seq": resolved.through,
            "at_seq": resolved.at_seq,
            "side": resolved.side,
            "events": copied,
        },
        mirror=mirror,
    )

    fresh = await branches.get(new.id)
    return {
        "branch": fresh,
        "through_seq": resolved.through,
        "copied": copied,
    }
