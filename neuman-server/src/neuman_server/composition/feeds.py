"""The frames one branch's sockets share.

A turn's frames used to go to exactly one socket — the one that sent the
message — so a transcript open in any other window, on the same computer or on
another, showed nothing until it was reopened. This is the piece that lets a
second socket *watch*: every frame a turn emits for a branch is published here,
and every socket on that branch receives it (D113).

Two properties make the catch-up exact rather than approximate.

**The projection is where a late reader starts.** A window that opens a session
reads `GET /api/sessions/{id}`, which draws every row the log holds so far. What
that read cannot show is the step in flight — the tokens of a reply that has not
finished, the call that is still running. So a socket is told `from_seq`, the
last row the projection already accounts for, draws the projection through that
row, and then applies the buffer of frames emitted after it. Nothing is drawn
twice, because the two halves are cut at a row the projection can see.

**A boundary frame clears the buffer.** `tool_proposal`, `tool_result`,
`compacted`, `done`, and the `user_message` that opens a turn are the frames
whose rows make everything before them readable from the projection. So each
clears the buffer *and* moves `from_seq` to its row, and what remains buffered is
exactly one step of live output — a bounded thing. A frame in the buffer is
therefore always something the projection does not hold. A `user_message` that
arrives mid-turn is a *steer* rather than a prompt, and it is not a boundary:
its row sits in the middle of a reply whose earlier tokens are not rows at all.

**A slow reader is dropped, never waited for.** `publish` runs inside the turn,
so it must not block: a queue that has fallen behind is sent a tombstone and its
socket closed with 4408, which the window answers by reconnecting and asking for
a fresh resume. The alternative — backpressure — would make one stalled browser
tab stall the model.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any

__all__ = ["BOUNDARY", "MAX_FRAMES", "QUEUE_FRAMES", "BranchFeeds"]

# The frames whose rows the projection reproduces, so buffering them would make
# a late reader draw the same thing twice. Each carries the `seq` of the row it
# accompanies; `from_seq` moves to it when the frame is published.
#
# `user_message` is the exception the feed decides rather than the table: the
# frame that *opens* a turn is a boundary (its row is the prompt, and everything
# before it is history), but a `user_message` that arrives mid-turn is a steer,
# and a steer is a row in the *middle* of a reply whose earlier tokens have no
# row at all. Clearing there would drop the first half of a bubble the projection
# cannot draw.
BOUNDARY = frozenset({"tool_proposal", "tool_result", "compacted", "done"})

# How much of the in-flight step is kept for a reader that joins mid-turn. One
# step's tokens, not one turn's: a turn that runs a hundred steps clears at every
# step boundary, so this is reached only by a single reply longer than any model
# in the catalog can produce. Past it the buffer is marked truncated and a late
# reader is told, rather than being handed a fragment as if it were the whole.
MAX_FRAMES = 20_000

# One socket's queue. Deep enough for the burst a reconnect produces (the frames
# of a step arriving at once), shallow enough that a browser that has stopped
# reading is noticed while the turn can still answer.
QUEUE_FRAMES = 1_024


@dataclass
class _Feed:
    """One branch's live stream: what to send a newcomer, and who is reading."""

    # The last log row the projection accounts for. A reader draws through this.
    from_seq: int
    frames: deque[dict[str, Any]] = field(default_factory=deque)
    queues: set[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=set)
    # Sockets on the branch, whether or not they asked for the handshake. The
    # feed lives while one is open even if none is watching yet, because the
    # step in flight has to be buffered before anyone asks for it — a window
    # that joins mid-turn gets the call it is waiting on, not just the history.
    readers: int = 0
    # The buffer hit its cap, so `frames` no longer starts at the step's first
    # token. Sent with the resume so the window can say so instead of implying
    # a whole reply it never saw.
    truncated: bool = False
    # Whether the branch is between turns. The first `user_message` after a
    # `done` opens a turn and is a boundary; one that arrives later is a steer and
    # is not. Tracked here because the frame shapes are identical and the
    # difference is where the tokens already buffered sit relative to its row.
    between_turns: bool = True


class BranchFeeds:
    """Every live branch's stream, keyed by branch.

    Held on `Services` beside the turn registry, and with the same lifetime rule:
    in memory, one process, no persistence. A feed exists only while a socket is
    attached to the branch, because a turn cannot outlive its owner's socket
    (D63) — so nothing here is ever the only copy of anything.
    """

    def __init__(self) -> None:
        self._live: dict[str, _Feed] = {}

    def resume_seq(self, branch_id: str, head: int) -> int:
        """The row a reader of this branch should draw the projection through.

        `head` is the branch's newest row, used when no socket is attached and
        therefore nothing is buffered: the projection is the whole truth and a
        reader draws all of it.
        """

        feed = self._live.get(branch_id)
        return feed.from_seq if feed is not None else head

    def attach(self, branch_id: str, head: int) -> None:
        """Register a socket on the branch, creating the feed at `head`.

        Called for every socket, not only a watching one: the buffer has to be
        filling from the moment a turn starts, or the first window to attach
        after a call was proposed would be handed a history with no card in it.
        `head` seeds a feed that does not exist yet — an idle branch — so its
        first reader is told "you are already at the head".
        """

        feed = self._live.get(branch_id)
        if feed is None:
            feed = _Feed(from_seq=head)
            self._live[branch_id] = feed
        feed.readers += 1

    def detach(self, branch_id: str) -> None:
        """One socket left; forget the branch when the last one has."""

        feed = self._live.get(branch_id)
        if feed is None:
            return
        feed.readers = max(0, feed.readers - 1)
        if feed.readers == 0 and not feed.queues:
            # A buffer with no reader is memory for a turn nobody can be running
            # (the owner's socket is what keeps one alive), so it goes with the
            # last reader rather than waiting for the next turn to clear it.
            self._live.pop(branch_id, None)

    def join(
        self, branch_id: str, head: int
    ) -> tuple[asyncio.Queue[dict[str, Any] | None], int, list[dict[str, Any]], bool]:
        """Attach one *watching* socket: its queue, and what it must draw first."""

        self.attach(branch_id, head)
        feed = self._live[branch_id]
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=QUEUE_FRAMES)
        feed.queues.add(queue)
        return queue, feed.from_seq, list(feed.frames), not feed.truncated

    def leave(self, branch_id: str, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        """Detach one watching socket's queue."""

        feed = self._live.get(branch_id)
        if feed is not None:
            feed.queues.discard(queue)

    def publish(self, branch_id: str, frame: dict[str, Any]) -> None:
        """Fan one emitted frame out to every socket on the branch.

        Deliberately synchronous: this runs inside the turn's emit path, and an
        `await` here would put a reader's socket between the model and its next
        token. A queue that cannot take the frame is dropped instead.
        """

        feed = self._live.get(branch_id)
        if feed is None:
            # No socket on the branch. A frame emitted with nobody attached is
            # not buffered: there would be no reader to hand it to, and a turn
            # cannot be running on a branch whose owner has gone (D63).
            return

        # Boundaries are cleared *including* themselves: the projection holds
        # their rows, so a reader that draws through `from_seq` has them, and one
        # that applies the buffer would draw them again.
        kind = frame.get("type")
        boundary = kind in BOUNDARY
        if kind == "user_message":
            # A prompt that opens a turn, not a steer: see `BOUNDARY`.
            boundary = feed.between_turns
            feed.between_turns = False
        elif kind == "done":
            feed.between_turns = True

        if boundary:
            seq = frame.get("seq")
            if isinstance(seq, int):
                feed.from_seq = seq
            feed.frames.clear()
            feed.truncated = False
        else:
            feed.frames.append(frame)
            if len(feed.frames) > MAX_FRAMES:
                # Keep the tail — the live edge is what a reader needs to join —
                # and say the head is missing. Trimming the *oldest* means the
                # bubble a late reader starts will begin mid-sentence, which is
                # the honest description of a reply too long to have kept.
                feed.frames.popleft()
                feed.truncated = True

        # Snapshot: a queue that overflowed is removed below, and mutating the
        # set while iterating would skip the reader after it.
        for queue in list(feed.queues):
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                # The tombstone makes the sender close the socket with 4408
                # rather than block here or wait forever on an empty queue.
                _evict(queue)

    def drop(self, branch_id: str) -> None:
        """Forget a branch, when its session is removed or closed.

        The viewers are told rather than left on a stream that has stopped: a
        tombstone closes each socket with 4408, and the next upgrade refuses the
        removed branch with 4404. Staying silent would leave a transcript on
        screen that no longer exists anywhere.
        """

        feed = self._live.pop(branch_id, None)
        if feed is None:
            return
        for queue in list(feed.queues):
            _evict(queue)
        feed.queues.clear()


def _evict(queue: asyncio.Queue[dict[str, Any] | None]) -> None:
    """Make room for a tombstone in a queue that is full."""

    try:
        queue.get_nowait()
    except asyncio.QueueEmpty:  # pragma: no cover - full and empty cannot both hold
        pass
    try:
        queue.put_nowait(None)
    except asyncio.QueueFull:  # pragma: no cover - one slot was just freed
        pass
