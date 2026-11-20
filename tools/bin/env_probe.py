"""Report whether a directory has a usable development environment.

Same resolver as the `python` tool, asked a different question. `python` wants
an interpreter to run code with; this wants the *name of the state* a directory
is in, so the chat window can say so and the system prompt can state it. Both
read what `nix-direnv` already cached and neither evaluates it (D33).

**Why this is not `python(action="describe")`.** Describe exists for a model
deciding what it can import, so it spawns the resolved interpreter to take a
package inventory — a hundred distributions on a scientific stack. This runs
whenever the user changes the working directory, which makes a process spawn per
keystroke-adjacent click the wrong price for a coloured outline. So it resolves
and reports, and runs nothing at all: no subprocess, no inventory, no import.

**Why it is registered but granted to nobody.** The model already has
`python(action="describe")` for this question, and a better answer to it. This
tool is for the two callers that cannot make a tool call: `/api/environment`,
which answers the browser when the human picks a directory, and the harness,
which states the environment in the system prompt. Both are the chooser's
argument (`http/browse.py`) — the human clicked a folder in their own picker, so
routing it through gate, approval and audit would pop a card per click. A
manifest no role grants is invisible to `grantable()` and still dispatchable by
a route, which is exactly the split those two callers need.

The output is JSON on stdout, because both callers parse it. `reason` is the
resolver's own name for the state — `ok` when it resolved, otherwise the
`EnvError` reason verbatim — and that name is the whole contract: the UI maps it
to an outline and the prompt maps it to a sentence. Neither reads the prose.
"""

from __future__ import annotations

import json
import os
import sys

from direnv_env import EnvError, resolve


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("env_probe: expected one JSON argument", file=sys.stderr)
        return 2

    try:
        args = json.loads(argv[1])
    except json.JSONDecodeError as error:
        print(f"env_probe: invalid arguments: {error}", file=sys.stderr)
        return 2

    # Defaults to the dispatch's own cwd for `python`'s reason: the harness sets
    # `working_dir` and `within_reach` has already checked it. A `cwd` passed
    # here is checked by the tool host against the dispatch's surfaces, since
    # the manifest declares it a path.
    project = args.get("cwd") or os.getcwd()
    if not isinstance(project, str) or not project:
        print("env_probe: `cwd` must be a path", file=sys.stderr)
        return 2

    try:
        environment = resolve(project)
    except EnvError as error:
        # A refusal is the answer, not a failure. Every state this reports is
        # one a directory is legitimately in — most directories are not projects
        # (N85) — so exiting non-zero would turn "this is a folder of data" into
        # a failed dispatch, and the caller would have to read a reason out of
        # stderr to find out it was fine.
        report = {"path": project, "reason": error.reason, "detail": error.detail}
    else:
        # `interpreter` is a `PATH` scan over the carried store entries, so it
        # costs a few `stat` calls and no spawn. Reported because it is what
        # makes a later `ModuleNotFoundError` answerable: the prompt can name
        # which Python the directory means.
        report = {
            "path": project,
            "reason": "ok",
            "profile": environment.profile,
            "rc_path": environment.rc_path,
            "interpreter": environment.interpreter,
        }

    json.dump(report, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
