"""Write a file, whole.

Mutating, so the approval machinery gates it (D25 — `mutating = true` in the
manifest is what makes the harness ask). By the time this runs, the decision has
been made; what happens next is the write itself.

Whole-file rather than a patch: a partial write that fails halfway leaves a file
in a state neither the model nor the user asked for, and the tool has no way to
put it back. To change *part* of a file there is `fs_edit`, which resolves its
edits through the same `edit_plan.plan()` this uses — so the two tools differ in
how they say what to write and not at all in how the bytes are decided.

With `preview = true` nothing is written: the dispatch resolves the write and
prints the diff it *would* make, which is what the harness renders in the
approval frame. The guarantee worth stating is that this is the same function
both times, so an approved diff describes the write that follows (N35, N38).
"""

from __future__ import annotations

import sys

from edit_plan import run

if __name__ == "__main__":
    raise SystemExit(run("fs_write", sys.argv, whole_file=True))
