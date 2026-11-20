"""Run git, as the acting user, in the working directory.

The cloning half of the analysis workflow. Fetching a repository and reading it
are the same task done by different tools on different days: `git` puts the tree
on disk and then `fs_list`, `fs_search`, `fs_read` and `python` work over it.
Nothing about the repository enters the model's context except the pages and
matches it asks for, which is the whole reason a real checkout beats trying to
stream files through a tool result.

It is a real `git`, running where the dispatch runs: on the user's machine, as
the user, in the session's working directory. What the tool sets is only what a
headless `git` needs and a terminal would otherwise supply:

* `GIT_TERMINAL_PROMPT=0`, so an authentication prompt cannot hang a dispatch
  that has no terminal to answer it.
* `GIT_PAGER=cat` and `GIT_EDITOR=true`, again for the no-terminal case: a pager
  or editor spawned into a pipe is a dispatch that never returns.

Everything else is the user's own environment — their `~/.gitconfig`, their
credentials, their network. The tool is `mutating` because a clone writes, and it
declares `network` because that is what the gate reads: an account whose policy
grants no network is not offered it. Nothing narrows where it connects — it runs
on the user's own machine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

# The real binary, substituted at build time. A path rather than a PATH lookup
# because this wrapper is installed as `git` on the tool PATH: a lookup would
# find this wrapper again and recurse.
GIT = "@git@"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("git: expected one JSON argument", file=sys.stderr)
        return 2

    try:
        args = json.loads(argv[1])
    except json.JSONDecodeError as error:
        print(f"git: invalid arguments: {error}", file=sys.stderr)
        return 2

    git_args = args.get("args")
    if (
        not isinstance(git_args, list)
        or not git_args
        or not all(isinstance(item, str) for item in git_args)
    ):
        print(
            "git: `args` must be a non-empty array of strings, "
            'e.g. ["clone", "--depth", "1", "<url>", "<dir>"]',
            file=sys.stderr,
        )
        return 2

    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_PAGER"] = "cat"
    env["GIT_EDITOR"] = "true"

    try:
        # No `cwd` of its own: the dispatch's working directory is already the
        # process's, set by the executor, and a tool that started somewhere else
        # is the bug N84 recorded.
        completed = subprocess.run([GIT, *git_args], env=env, check=False)
    except OSError as error:
        print(f"git: cannot run {GIT}: {error}", file=sys.stderr)
        return 1

    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
