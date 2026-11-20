"""Reading a branch's log: orientation, windows, and one event whole.

**One implementation, two surfaces** (D9). `session_query` serves a user asking
about their own sessions and the admin API serves an admin asking about anyone's;
the shape of the answer — what a window says about itself, how a payload is
clipped, which event a seq names — is identical either way, so it lives here and
both call it.

**Scope is the caller's business, not this module's.** Every function takes a
branch row that the caller has already resolved: `session_query` resolves with
`owned_by`, the admin route with `get`. That is what lets one operation serve
both without a flag, and it keeps the authority decision where it belongs — in
identity, checked inside the operation that needs it (D9), never inferred here
from arguments.

The shape is driven by one constraint: a branch is routinely tens of thousands
of rows — 12,893 in the one that prompted this, mostly `response_chunk` — against
a 256 KiB output budget. So:

* `kinds` orients without reading any payload, which is what makes a first call
  useful rather than truncated;
* `events` reads a window, filtered by kind *before* paging so a caller walking
  `tool_result` rows is not paging through ten thousand chunks to find them;
* `event` returns one row whole, never clipped, because "show me exactly this"
  is the end of a diagnosis and a fragment would not answer it.

**The window is resolved by the store, not by slicing.** These functions used to
take every row of a session and cut the window out of the list, which meant the
route read and decoded the whole log — 12,300 rows and 13 MB measured on a real
session shape — to display fifty. `EventWindow` and `BranchSummary` are what the
store returns instead; the arithmetic that a reader sees (how many there are,
what `after` to pass next, what a payload was clipped to) stays here, in one
place, for both surfaces.

Clipping reports itself. A clipped payload carries `payload_clipped` and its
true size, so a reader can *see* it holds a fragment and go fetch the row. Silent
truncation would be a tool that lies by omission, which is worse than one that
returns nothing (N105 is what that costs).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

__all__ = [
    "PAYLOAD_BUDGET",
    "BranchSummary",
    "EventWindow",
    "event",
    "events",
    "kinds",
    "split_kinds",
]

# Per-event payload ceiling for a windowed read. Chosen against the 256 KiB
# output budget: 50 events at 4 KiB each is ~200 KiB plus envelope, so a default
# page fits with room to spare. `event` is exempt — that call exists to return
# one row whole.
PAYLOAD_BUDGET = 4096


# The window a store resolved: the rows to show, how many the filter matched in
# all, and the highest seq among them (`None` for an empty session). `last_seq`
# is what makes "is there more" answerable without the reader doing arithmetic
# against two numbers it was not given.
@dataclass(frozen=True)
class EventWindow:
    rows: Sequence[Any]
    total: int
    last_seq: int | None


# What a session holds, without reading a payload: the counts `kinds` reports as
# its orientation.
@dataclass(frozen=True)
class BranchSummary:
    total: int
    first_ts: datetime | None
    last_ts: datetime | None
    counts: dict[str, int]


def split_kinds(kind: str | None) -> list[str] | None:
    """The kinds named by a comma-separated filter, or None for all of them.

    Comma-separated, because a diagnosis usually wants two or three kinds
    together — `tool_proposal,tool_result,harness_event` is the shape of "what
    did it try and what happened". Split here rather than in the store so the
    syntax is the operation's, and both surfaces accept the same spelling.
    """

    if not kind:
        return None
    wanted = [part.strip() for part in kind.split(",") if part.strip()]
    return wanted or None


def kinds(summary: BranchSummary, *, branch_id: str) -> dict[str, Any]:
    """What happened in this branch and how often, reading none of it."""

    return {
        "branch_id": branch_id,
        "total_events": summary.total,
        "first_ts": summary.first_ts.isoformat() if summary.first_ts else None,
        "last_ts": summary.last_ts.isoformat() if summary.last_ts else None,
        # Descending, so the dominant kind is first and a reader sees at a glance
        # that a session is 99% streamed chunks.
        "kinds": dict(sorted(summary.counts.items(), key=lambda item: -item[1])),
    }


def events(window: EventWindow, *, branch_id: str) -> dict[str, Any]:
    """A window of the log, with payloads clipped per event.

    The window is already the rows the reader asked for — tail or page — so
    there is nothing to select here, only the two things a window has to say
    about itself: how many events exist, and where to resume.
    """

    out: list[dict[str, Any]] = []
    for row in window.rows:
        encoded = json.dumps(row.payload, default=str)
        clipped = len(encoded) > PAYLOAD_BUDGET
        entry: dict[str, Any] = {
            "seq": row.seq,
            "ts": row.ts.isoformat(),
            "kind": row.kind,
            "payload": encoded[:PAYLOAD_BUDGET] if clipped else row.payload,
        }
        if clipped:
            entry["payload_clipped"] = True
            entry["payload_bytes"] = len(encoded)
        out.append(entry)

    body: dict[str, Any] = {
        "branch_id": branch_id,
        "total_events": window.total,
        "returned": len(out),
        "events": out,
    }
    # What to pass as `after` to continue, absent when the window reached the
    # end — so "is there more" is answerable without the caller doing
    # arithmetic against two numbers.
    if window.rows and window.last_seq is not None and window.rows[-1].seq < window.last_seq:
        body["next_after"] = window.rows[-1].seq
    return body


def event(row: Any | None, *, branch_id: str, seq: int) -> dict[str, Any]:
    """One event, payload whole. The only shape that is never clipped."""

    if row is None:
        return {"error": f"no event with seq {seq}"}
    return {
        "branch_id": branch_id,
        "seq": row.seq,
        "ts": row.ts.isoformat(),
        "kind": row.kind,
        "payload": row.payload,
    }
