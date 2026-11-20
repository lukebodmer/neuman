"""Where a branch may be forked from.

A fork inherits a **prefix of a transcript**, and not every event is a place a
history can begin. The rule is the one compaction already obeys: a history may
not start between an assistant's `tool_calls` and the replies it is owed, and a
branch may not be cut in the middle of a turn. So the boundary is a completed
turn and nothing else.

That is a smaller set than "any message", and it is deliberately chosen over
the alternative of validating an arbitrary cut after the fact. `turn_complete`
is written on every exit path of `_run_turn`, after the last tool result and the
final reply, so a boundary that names one is legal by construction — the N116
class cannot be reached. A turn left open by a crash is not a boundary, which
is what makes a fork of a crashed branch stop at the last thing that finished.

`side` is how the UI says which half of a turn it means:

* `before` — the last completed turn at or before `at_seq`. Used on the user
  message that opened a turn: fork *before* this attempt and make a new one.
* `after` — the first completed turn at or after `at_seq`. Used on the reply:
  fork *after* this turn and continue from it.
* omitted — the last completed turn. A clone of everything that finished.

`at_seq` is a seq in the source branch. The display projection carries one on
every message for exactly this purpose; a live turn learns it from the socket's
`user_message` and `done` frames.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["Boundary", "BoundaryError", "resolve"]


class BoundaryError(ValueError):
    """A requested boundary that cannot be forked from, with a stable code."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Boundary:
    """A resolved prefix: the inclusive seq to inherit, and what was asked for."""

    through: int
    at_seq: int | None
    side: str | None


def _turn_ends(events: Sequence[object]) -> list[int]:
    ends: list[int] = []
    for event in events:
        if event.kind != "harness_event":
            continue
        payload = event.payload or {}
        if payload.get("type") == "turn_complete":
            ends.append(int(event.seq))
    return sorted(ends)


def resolve(
    events: Sequence[object],
    *,
    at_seq: int | None = None,
    side: str | None = None,
) -> Boundary:
    """Resolve a request to the inclusive seq a fork inherits.

    `events` is the source branch's projection read — the same rows the new
    branch will copy, so the boundary is resolved against the log being cut
    rather than a second opinion about it.
    """

    if side not in (None, "before", "after"):
        raise BoundaryError("invalid_boundary", "side must be 'before' or 'after'")

    ends = _turn_ends(events)
    last_seq = max((int(event.seq) for event in events), default=0)

    if at_seq is not None:
        if not isinstance(at_seq, int) or isinstance(at_seq, bool):
            raise BoundaryError("invalid_boundary", "at_seq must be an integer")
        if at_seq < 1 or at_seq > last_seq:
            raise BoundaryError("invalid_boundary", "at_seq is not in this branch")

    if at_seq is None:
        if not ends:
            raise BoundaryError("nothing_to_fork", "this branch has no completed turn")
        return Boundary(through=ends[-1], at_seq=None, side=None)

    if side == "before":
        candidates = [end for end in ends if end <= at_seq]
        through = candidates[-1] if candidates else 0
    elif side == "after":
        candidates = [end for end in ends if end >= at_seq]
        if not candidates:
            raise BoundaryError("turn_open", "that turn has not finished")
        through = candidates[0]
    else:
        # No side and an explicit seq: treat it as a boundary if it is one.
        if at_seq not in ends:
            raise BoundaryError("invalid_boundary", "that event is not a turn boundary")
        through = at_seq

    if through <= 0:
        raise BoundaryError("nothing_to_fork", "there is no completed turn before it")
    return Boundary(through=through, at_seq=at_seq, side=side)
