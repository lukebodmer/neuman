"""Run a Python script in the working directory's own flake environment.

The environment is a *lookup*, not a privilege. `nix-direnv` has already
realised the project's flake and left the result on disk as declarative text, so
resolving it needs no evaluator, no network and no store write. The tool parses
that text, carries the variables an interpreter needs, and execs it.

The parser is deliberately narrower than shell syntax, and that is the
load-bearing part: `.direnv` lives inside the working directory, so *sourcing*
the cache would execute text found in a repository Neuman was merely pointed at,
which inverts D23 — injected text may propose work, never widen reach. Only
`NAME='single-quoted'` is accepted, only six variables are carried, and every
path entry must be under `/nix/store`.

A script runs as the user, with their environment and network, and can remove
and rename files: on this machine that is what running as the user means. What
stands between the script and the filesystem is the approval verdict, and that
is the whole control.

**Two different "no environment" answers**, and conflating them was the first
version's real mistake (N85).

A directory that *declares* an environment — a `.envrc`, a `flake.nix` — and
has not built it is a refusal. Running against a bare interpreter there would
silently lack the packages the project means to provide, failing as a
`ModuleNotFoundError` twenty lines in, which reads as a broken tool rather than
an environment nobody built.

A directory that declares nothing is not a failure at all. Most directories are
not projects: they hold the data a script is pointed at, and the common case
needs only the standard library. The first version refused those too, advising
the user to `direnv allow` a `.envrc` that does not exist — so now it falls back
to its own interpreter and says so.

No command filtering, for `shell`'s reason: a blocklist cannot be complete, and
filtering here would mainly suggest something other than the approval is the
control.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from direnv_env import EnvError, Environment, resolve

# What each refusal means and what to do about it. The reason comes from the
# resolver; this is the half that belongs to the tool, because it is advice to
# a *model* about a *dispatch* rather than a property of the cache.
ADVICE = {
    "no_environment": (
        "This directory declares no development environment, so the standard "
        "library is what is available. That is normal and not a problem to "
        "report — only mention it if an import fails."
    ),
    "env_unrealised": (
        "No realised development environment here. Ask the user to `cd` into "
        "this directory so direnv can build it (or run `direnv allow`), then "
        "try again."
    ),
    "env_stale": (
        "The environment is older than the flake that defines it. Ask the user "
        "to re-enter the directory so direnv can rebuild it."
    ),
    "env_collected": (
        "This environment was built but its store paths have since been "
        "garbage-collected. Ask the user to re-enter the directory so direnv "
        "can rebuild it."
    ),
    "env_untrusted": (
        "This environment's cache was not written by nix and will not be used. "
        "Report this to the user rather than working around it."
    ),
    "env_unreadable": (
        "The environment's cache cannot be read. It may be outside the paths "
        "your policy grants."
    ),
    "no_such_directory": "Check the path, or use fs_list to find the project.",
}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("python: expected one JSON argument", file=sys.stderr)
        return 2

    try:
        args = json.loads(argv[1])
    except json.JSONDecodeError as error:
        print(f"python: invalid arguments: {error}", file=sys.stderr)
        return 2

    action = args.get("action", "run")
    if action not in ("run", "describe"):
        print(f"python: unknown action: {action}", file=sys.stderr)
        return 2

    # Defaults to the session's working directory, which the harness sets and
    # `within_reach` has already checked against policy. A `cwd` given here is
    # checked by nothing in this process and needs to be: a directory outside
    # the surfaces is a question the tool host answers before this runs.
    project = args.get("cwd") or os.getcwd()
    if not isinstance(project, str) or not project:
        print("python: `cwd` must be a path", file=sys.stderr)
        return 2

    try:
        environment = resolve(project)
    except EnvError as error:
        if error.reason == "no_environment":
            # Most directories are not projects. They hold the data a script is
            # pointed at — a folder of DICOM files, an export, a directory of
            # notes — and refusing those was the first version's real mistake
            # (N85): it answered "ask the user to run `direnv allow`" for a
            # `.envrc` that does not exist and never will, when the work needed
            # nothing but the standard library.
            #
            # So a directory declaring no environment falls back to this tool's
            # own interpreter. The refusal is kept for a project that *declares*
            # an environment and has not built it, which is the case where
            # running anyway would silently lack the packages the project means
            # to provide.
            environment = None
            interpreter = sys.executable
        else:
            # Named, with the advice attached. Two lines rather than one because
            # the first is what happened and the second is what to do, and a
            # model reading a single run-on sentence tends to act on neither.
            print(f"python: {error.reason}: {error.detail}", file=sys.stderr)
            advice = ADVICE.get(error.reason)
            if advice:
                print(f"python: {advice}", file=sys.stderr)
            return 1
    else:
        interpreter = environment.interpreter
        if interpreter is None:
            print(
                f"python: {environment.rc_path} declares no Python on its PATH; "
                "this project's environment does not include one",
                file=sys.stderr,
            )
            return 1

    if action == "describe":
        return _describe(environment, interpreter)

    code = args.get("code")
    if not isinstance(code, str) or not code:
        print("python: `code` is required for `run`", file=sys.stderr)
        return 2

    # The environment replaces rather than extends. A flake env is a complete
    # description of what the project builds against, and inheriting the
    # dispatch's PATH would mean a script sometimes finding Neuman's own tools
    # and sometimes the project's depending on name collisions.
    env = dict(environment.env) if environment is not None else {}
    env["HOME"] = os.environ.get("HOME", project)
    env["TERM"] = "dumb"

    # Which interpreter ran, on stderr, always. The transcript is where a
    # `ModuleNotFoundError` gets diagnosed, and "the wrong Python" is
    # indistinguishable from "the package is missing" unless this line is
    # there. stderr rather than stdout so a script's output stays its own.
    print(f"python: {interpreter}", file=sys.stderr)

    # `-I` isolates: no `PYTHONPATH` from the ambient environment (the flake's
    # is passed explicitly above), no user site-packages, no implicit cwd on
    # `sys.path`. Without it a `random.py` sitting in the project would shadow
    # the standard library, which is a confusing failure that has nothing to do
    # with the script.
    #
    # No timeout: the client executor owns the deadline and kills the process
    # group at it. One here would race that and could only ever be the weaker of
    # the two.
    try:
        completed = subprocess.run(
            [interpreter, "-I", "-c", code],
            cwd=project,
            env=env,
            check=False,
        )
    except OSError as error:
        print(f"python: cannot run {interpreter}: {error}", file=sys.stderr)
        return 1

    # The script's own exit code, passed through: a failing script is a failing
    # dispatch, which is what a caller reading the transcript expects.
    return completed.returncode


# Asks the resolved interpreter what it can import, rather than guessing from
# the cache. `importlib.metadata` reads the `.dist-info` directories on that
# interpreter's own `sys.path`, which is the same list an `import` consults —
# so a name here is a name that will import, and the two cannot drift.
#
# Deriving it from the rc file instead was the obvious shortcut and would have
# been a second implementation of "what is installed", disagreeing with the
# first the moment a package arrived by any route but a store path on PATH.
_INVENTORY = """
import importlib.metadata as m
seen = {}
for dist in m.distributions():
    name = dist.metadata["Name"]
    if name:
        seen.setdefault(name.lower(), (name, dist.version or "?"))
for name, version in sorted(seen.values(), key=lambda p: p[0].lower()):
    print(f"{name} {version}")
"""

# A flake env with a scientific stack runs past a hundred distributions, and an
# inventory is a thing a model reads to decide what to import — not a thing it
# needs in full. Truncating keeps `describe` cheap enough to call before any
# script, which is the whole point of having it; the count is always reported
# so a truncated list never reads as the complete one.
MAX_PACKAGES = 60


def _describe(environment: Environment | None, interpreter: str) -> int:
    """Report the resolved environment without running anything.

    Exists because the packages available here come from the project's
    `flake.nix`, which the model cannot see — so without this, discovering that
    `numpy` is absent costs a failed dispatch and an error that reads like a
    broken tool. N47's lesson was that a library present in one place and
    missing in another fails only at runtime; this makes that answerable before
    the run rather than after it.

    Reading `flake.nix` would have been the wrong source even though it is the
    file a human would open: it is an expression, not a result. The one that
    prompted this tool lists `numpy` among several commented-out packages, so a
    reader of the text and a reader of the environment agree only by luck.
    """

    print(f"interpreter: {interpreter}")
    if environment is None:
        # The fallback. Said plainly rather than left blank, because "no project
        # environment" is a real and useful answer: it tells the model the
        # standard library is what it has, and that a missing package is not a
        # misconfiguration to report.
        print("environment: none declared here; using the standard library")
    else:
        print(f"environment: {environment.profile}")
        print(f"resolved from: {environment.rc_path}")

    # The import path is the useful half: it is what decides whether an
    # `import` will succeed, and it names the project's own package among the
    # dependencies.
    carried = environment.env if environment is not None else {}
    entries = [e for e in carried.get("PYTHONPATH", "").split(os.pathsep) if e]
    if entries:
        print(f"\nPYTHONPATH ({len(entries)} entries):")
        for entry in entries:
            print(f"  {entry}")
    else:
        # Not unusual: a python env built with `withPackages` puts its modules
        # beside the interpreter rather than on PYTHONPATH, so an empty one is
        # a normal shape and not a sign of trouble.
        print("\nPYTHONPATH: empty (packages are bundled with the interpreter)")

    env = dict(carried)
    env["HOME"] = os.environ.get("HOME", "/")

    try:
        listed = subprocess.run(
            [interpreter, "-I", "-c", _INVENTORY],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        print(f"\npackages: cannot ask {interpreter}: {error}")
        return 0

    if listed.returncode != 0:
        # Not a failed dispatch. The interpreter and the import path are the
        # answer `describe` exists to give, and they are already printed; an
        # inventory that could not be taken is a missing extra, not a reason to
        # report the environment as broken.
        detail = listed.stderr.strip().splitlines()
        print(f"\npackages: could not be listed ({detail[-1] if detail else 'no output'})")
        return 0

    packages = listed.stdout.split("\n")
    packages = [line for line in packages if line.strip()]

    if not packages:
        # A bare interpreter with nothing but the standard library. Worth
        # saying plainly rather than printing an empty heading, because "no
        # third-party packages" is a real and actionable answer.
        print("\npackages: none beyond the standard library")
        return 0

    shown = packages[:MAX_PACKAGES]
    print(f"\npackages ({len(packages)}):")
    for line in shown:
        print(f"  {line}")
    if len(packages) > len(shown):
        print(
            f"  … and {len(packages) - len(shown)} more; to check one by name, run:\n"
            '    python(action="run", code="import importlib.util as u; '
            "print(u.find_spec('NAME') is not None)\")"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
