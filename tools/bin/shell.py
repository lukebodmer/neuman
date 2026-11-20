"""Run a shell command on the user's own machine.

The tool that looks most dangerous, and it is genuinely the one with the most
reach: it runs as the acting human, in their environment, and can do anything
they can. The two controls are the capability gate before the call and the
approval verdict for it.

So this does no command filtering, and should not. A blocklist of "dangerous"
commands cannot be complete, the shell defeats any list with `command`, `$(...)`
or a script on PATH, and a filter here would mainly create the impression that
something else is enforcing a boundary. The boundary is the account and the
verdict.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("shell: expected one JSON argument", file=sys.stderr)
        return 2

    try:
        args = json.loads(argv[1])
    except json.JSONDecodeError as error:
        print(f"shell: invalid arguments: {error}", file=sys.stderr)
        return 2

    command = args.get("command")
    if not isinstance(command, str) or not command:
        print("shell: `command` is required", file=sys.stderr)
        return 2

    cwd = args.get("cwd")
    if cwd is not None and not os.path.isdir(cwd):
        print(f"shell: no such directory: {cwd}", file=sys.stderr)
        return 1

    # No timeout here: the client executor owns the deadline and kills the
    # process group at it. A timeout in this process would race that and could
    # only ever be the weaker of the two.
    try:
        completed = subprocess.run(command, shell=True, cwd=cwd, check=False)
    except OSError as error:
        print(f"shell: cannot run: {error}", file=sys.stderr)
        return 1

    # The command's own exit code, passed through. A failing command is a
    # failing dispatch, which is what a caller reading the transcript expects.
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
