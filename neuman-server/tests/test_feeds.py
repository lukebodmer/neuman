"""The per-branch frame feed: the buffer a watching socket catches up from.

These are the properties the socket tests cannot show cheaply: what a boundary
frame does to the buffer, what happens when nobody is attached, what a reader
that stops reading costs, and where a late reader is cut. They are the whole
reason a second window can draw a live step without drawing it twice (D113).
"""

from __future__ import annotations

import asyncio

from neuman_server.composition.feeds import MAX_FRAMES, QUEUE_FRAMES, BranchFeeds


def token(text: str) -> dict:
    return {"type": "token", "text": text}


def drain(queue: asyncio.Queue) -> list:
    """Everything the queue holds now, without waiting."""

    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


def test_an_idle_branch_resumes_at_its_head():
    """With nothing buffered, the projection is the whole truth."""

    feeds = BranchFeeds()
    feeds.attach("b", head=42)

    assert feeds.resume_seq("b", head=99) == 42
    _queue, from_seq, frames, complete = feeds.join("b", head=99)
    assert (from_seq, frames, complete) == (42, [], True)


def test_a_boundary_frame_moves_the_cut_and_clears_what_came_before_it():
    """A row the projection reproduces must not also arrive as a frame.

    `user_message` is the clearest case: the row is written before the frame is
    emitted, so a reader that draws the projection through that row already has
    it. Buffering the frame as well is how a late reader would draw the prompt
    twice.
    """

    feeds = BranchFeeds()
    feeds.attach("b", head=10)
    feeds.publish("b", token("first"))
    feeds.publish("b", {"type": "user_message", "content": "hi", "seq": 11})
    feeds.publish("b", token("second"))

    _queue, from_seq, frames, complete = feeds.join("b", head=0)
    assert from_seq == 11
    assert frames == [token("second")]
    assert complete is True


def test_a_branch_with_no_socket_buffers_nothing():
    """A turn cannot run without its owner's socket, so there is nobody to catch up.

    Buffering here would be memory for a stream no one can read, and the next
    reader seeds `from_seq` from the log instead.
    """

    feeds = BranchFeeds()
    feeds.publish("b", token("lost"))

    _queue, from_seq, frames, complete = feeds.join("b", head=7)
    assert (from_seq, frames, complete) == (7, [], True)


def test_the_last_socket_leaving_forgets_the_branch():
    feeds = BranchFeeds()
    # `join` attaches too: a watching socket is a socket on the branch.
    queue, _from_seq, _frames, _complete = feeds.join("b", head=3)
    feeds.publish("b", token("x"))

    feeds.leave("b", queue)
    feeds.detach("b")

    # No feed, so the next reader is cut at the log's head rather than at a
    # buffer that no longer exists.
    assert feeds.resume_seq("b", head=12) == 12


def test_a_steer_does_not_cut_the_reply_it_arrived_in():
    """A mid-turn `user_message` is a steer, and its row is not a boundary.

    Clearing there would drop the tokens of the reply the person is steering —
    they have no row yet, so the projection cannot draw them — and a reader
    joining after the steer would start the bubble mid-sentence. The steer frame
    itself stays buffered and is drawn in order, between the tokens either side.
    """

    feeds = BranchFeeds()
    feeds.attach("b", head=0)
    feeds.publish("b", {"type": "user_message", "content": "one", "seq": 1})
    feeds.publish("b", token("half"))
    feeds.publish("b", {"type": "user_message", "content": "also", "seq": 2})
    feeds.publish("b", token("done"))

    _queue, from_seq, frames, complete = feeds.join("b", head=0)
    assert from_seq == 1
    assert frames == [
        token("half"),
        {"type": "user_message", "content": "also", "seq": 2},
        token("done"),
    ]
    assert complete is True


def test_a_reader_that_falls_behind_is_dropped_rather_than_waited_for():
    """`publish` runs inside the turn, so a stalled browser tab cannot stall it.

    The tombstone is what wakes the sender; it closes the socket with 4408 so
    the window comes back with a fresh resume instead of showing a frozen
    transcript.
    """

    feeds = BranchFeeds()
    feeds.attach("b", head=0)
    queue, _from_seq, _frames, _complete = feeds.join("b", head=0)

    for n in range(QUEUE_FRAMES):
        feeds.publish("b", token(str(n)))
    assert queue.full()

    feeds.publish("b", token("late"))

    held = drain(queue)
    assert len(held) == QUEUE_FRAMES
    assert held[-1] is None


def test_a_buffer_that_overflows_keeps_the_tail_and_says_so():
    """A reply longer than the cap is joined mid-sentence, not silently cut.

    Keeping the oldest frames instead would give a late reader the start of a
    reply and none of the live edge, which is the half they can already read
    from the projection.
    """

    feeds = BranchFeeds()
    feeds.attach("b", head=0)
    for n in range(MAX_FRAMES + 5):
        feeds.publish("b", token(str(n)))

    _queue, _from_seq, frames, complete = feeds.join("b", head=0)
    assert complete is False
    assert len(frames) == MAX_FRAMES
    assert frames[-1] == token(str(MAX_FRAMES + 4))
    assert frames[0] != token("0")


def test_dropping_a_branch_tells_its_readers():
    """A removed session's viewers are disconnected, not left on a dead stream."""

    feeds = BranchFeeds()
    feeds.attach("b", head=0)
    first, *_ = feeds.join("b", head=0)
    second, *_ = feeds.join("b", head=0)

    feeds.drop("b")

    assert drain(first) == [None]
    assert drain(second) == [None]
    assert feeds.resume_seq("b", head=5) == 5


def test_every_reader_gets_every_frame():
    feeds = BranchFeeds()
    feeds.attach("b", head=0)
    first, *_ = feeds.join("b", head=0)
    second, *_ = feeds.join("b", head=0)

    feeds.publish("b", token("x"))

    assert drain(first) == [token("x")]
    assert drain(second) == [token("x")]
