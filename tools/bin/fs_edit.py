"""Replace exact regions of a file, leaving the rest alone.

The tool the research session needed and did not have. `fs_write` can only
replace a file whole, so iterating on one function meant re-sending the entire
file every time — which is how a five-hour session ends up re-deriving its own
setup in every script instead of editing a module in place.

Each edit names `old_text` that must appear **exactly once** and the `new_text`
to put there. Exact matching is deliberate and documented in `edit_plan`: a
fuzzy match that silently retargets would mean the human approved a diff for a
different write, and that approval is what authorises the write.

Every edit applies or none do, so a refusal leaves the file exactly as it was.
"""

from __future__ import annotations

import sys

from edit_plan import run

if __name__ == "__main__":
    raise SystemExit(run("fs_edit", sys.argv, whole_file=False))
