"""Resolve a flake development environment from what nix-direnv already cached.

The point of this module is what it refuses to do. A development environment is
a *lookup*, not a privilege (D33): `nix-direnv` has already realised the flake
and left the result on disk as declarative text, so reading that text needs no
evaluator, no network, and no store write. Three alternatives were considered
and each is strictly worse:

  * **Running `nix develop`** needs network access, write access to the store,
    and an evaluator — it would hand a dispatch the ability to build arbitrary
    derivations. That is a real escalation, and it is the version that would
    blur the security model.
  * **Sourcing the rc in a shell** executes attacker-influenced text found
    *inside the working directory*. A repository Neuman is reading would become
    a repository that can run commands, which inverts D23: injected text may
    propose work, never widen reach.
  * **Parsing the rc**, which is this module. Assignments are extracted as
    strings, filtered to an allowlist, and checked to be store paths. Nothing
    is evaluated.

So the parser is deliberately *narrower* than shell syntax. It accepts the
shape `nix print-dev-env` actually emits — `NAME='value'` with POSIX
single-quote escaping, one assignment per line — and refuses everything else
rather than interpreting it. A line it does not understand is a refusal, not a
best effort: this file decides which interpreter runs, so "I think this means
X" is not good enough.

Why the store-path check is the load-bearing one: a `.direnv` cache is text
*inside the directory being read*, so without the check a repository could
point this tool at any executable it shipped — and reading a repository would
be running code from it, which is the inversion D23 forbids. `/nix/store` is
content-addressed and not writable by the project, so an interpreter from
there is one the project cannot have planted. An entry outside it means
something hand-wrote this cache, and the answer is to refuse rather than to
guess what it wanted.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# The only place an interpreter this module names may live, so a repository
# cannot point the tool at a program it shipped (see the module docstring).
NIX_STORE = "/nix/store/"

# nix-direnv's layout: a GC-root symlink into the store, and the environment
# beside it under the same name. The hash is the flake's, so a directory may
# hold several once inputs have changed — the newest is the live one.
CACHE_DIR = ".direnv"
PROFILE_PREFIX = "flake-profile-"
RC_SUFFIX = ".rc"

# What a Python dispatch needs carried over, and nothing else. An allowlist
# rather than a blocklist because the rc holds hundreds of assignments and the
# ones that matter are few: the interpreter, where it finds modules, and where
# its extension modules find their shared libraries. Everything else — `AR`,
# `CONFIG_SHELL`, the stdenv build machinery — belongs to a nix *build* and
# would be noise at best in a dispatch.
#
# Notably absent: `shellHook`, which is code. Carrying it would reintroduce the
# execution this module exists to avoid.
CARRIED = frozenset(
    {
        "PATH",
        "PYTHONPATH",
        "LD_LIBRARY_PATH",
        "PKG_CONFIG_PATH",
        "CMAKE_PREFIX_PATH",
        "SOURCE_DATE_EPOCH",
    }
)

# Variables whose value is a colon-joined path list, which is what gets filtered
# to store entries. The rest are carried verbatim.
PATH_LIKE = frozenset(
    {
        "PATH",
        "PYTHONPATH",
        "LD_LIBRARY_PATH",
        "PKG_CONFIG_PATH",
        "CMAKE_PREFIX_PATH",
    }
)

# `NAME='...'` — the only form accepted. Single quotes cannot interpolate in
# POSIX shell, which is precisely why this is the only form worth accepting:
# the value is data by construction rather than by inspection. Anything
# double-quoted or bare could contain `$(...)`, and a parser that tries to
# decide whether a particular one is safe is a parser that will eventually be
# wrong.
ASSIGNMENT = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)='(?P<value>.*)'$")


class EnvError(Exception):
    """A named refusal.

    Carries a stable `reason` because the caller reports it to a model, and a
    model can act on `env_unrealised` (ask the human to run `direnv allow`)
    where it cannot act on prose. The same instinct as the working-directory
    refusal naming `outside_policy` rather than returning a bare 403.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Environment:
    """A resolved environment: where it came from, and what to run with."""

    rc_path: str
    profile: str
    env: dict[str, str]

    @property
    def interpreter(self) -> str | None:
        """The first `python3` on the environment's PATH, or None.

        Resolved here rather than by exec'ing `python3` from a PATH search,
        because the environment resolver is what decides “which Python this
        directory means” (D33) — and because naming the interpreter in the
        transcript is what makes a
        `ModuleNotFoundError` answerable: the reader can see *which* Python
        ran.
        """

        for directory in self.env.get("PATH", "").split(os.pathsep):
            if not directory:
                continue
            for name in ("python3", "python"):
                candidate = os.path.join(directory, name)
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    return candidate
        return None


def _unescape(value: str) -> str:
    r"""Undo POSIX single-quote escaping.

    A literal quote inside a single-quoted string can only be written by
    closing, escaping, and reopening — `'\''` — which is what nix emits. No
    other escape exists inside single quotes, so this is the whole grammar.
    """

    return value.replace("'\\''", "'")


def _newest_rc(cache: str) -> str:
    """The live rc file in a `.direnv` directory.

    Newest by mtime: a directory accumulates one profile per flake revision,
    and the most recent is the one direnv would load. Picking by name would
    mean sorting hashes, which carries no ordering.
    """

    try:
        entries = [
            os.path.join(cache, name)
            for name in os.listdir(cache)
            if name.startswith(PROFILE_PREFIX) and name.endswith(RC_SUFFIX)
        ]
    except OSError as error:
        raise EnvError("env_unreadable", f"{cache}: {error}") from error

    if not entries:
        raise EnvError(
            "env_unrealised",
            f"{cache} holds no realised flake environment",
        )

    try:
        return max(entries, key=os.path.getmtime)
    except OSError as error:
        raise EnvError("env_unreadable", f"{cache}: {error}") from error


def _check_profile(rc_path: str) -> str:
    """The GC root beside the rc, required to resolve into the store.

    This is the check that the cache is nix's own work rather than a file
    someone dropped in a repository. The rc names interpreters; the symlink is
    the evidence that a real `nix` produced them. A `.direnv` directory
    containing only a plausible-looking `.rc` resolves nothing.
    """

    profile = rc_path[: -len(RC_SUFFIX)]
    try:
        target = os.path.realpath(profile)
    except OSError as error:
        raise EnvError("env_unreadable", f"{profile}: {error}") from error

    if not os.path.islink(profile):
        raise EnvError(
            "env_unrealised",
            f"{profile} is not a nix profile symlink",
        )
    if not target.startswith(NIX_STORE):
        raise EnvError(
            "env_untrusted",
            f"{profile} does not resolve into {NIX_STORE} (got {target})",
        )

    # The GC root is a *root* only while something holds it. A profile whose
    # target has been collected is the ordinary state of any project not
    # entered since the last `nix-collect-garbage`, and it is worth its own
    # name: the first version of this module reported it as "declares no Python
    # on its PATH", which is true, useless, and points at the project's flake
    # rather than at the collected store. Found by running it against a real
    # cache rather than a fixture — the whole PATH existed as text and none of
    # it existed on disk.
    if not os.path.exists(target):
        raise EnvError(
            "env_collected",
            f"{profile} points at {target}, which has been garbage-collected",
        )
    return target


def _check_fresh(rc_path: str, project: str) -> None:
    """Refuse an rc older than the flake it was built from.

    A stale environment is the failure that would otherwise be silent and
    expensive: the script runs, imports resolve against last week's package
    set, and the error surfaces as a missing module rather than as the stale
    cache it is. Named so the model can say what to do about it.
    """

    try:
        rc_mtime = os.path.getmtime(rc_path)
    except OSError as error:
        raise EnvError("env_unreadable", f"{rc_path}: {error}") from error

    for name in ("flake.nix", "flake.lock"):
        source = os.path.join(project, name)
        try:
            if os.path.getmtime(source) > rc_mtime:
                raise EnvError(
                    "env_stale",
                    f"{name} is newer than {os.path.basename(rc_path)}; "
                    "the environment needs rebuilding",
                )
        except FileNotFoundError:
            # No flake is not an error here. `_newest_rc` already established
            # that a realised environment exists, and direnv can realise one
            # from `use nix` against a shell.nix just as well.
            continue
        except OSError as error:
            raise EnvError("env_unreadable", f"{source}: {error}") from error


def _parse(rc_path: str) -> dict[str, str]:
    """Extract the carried assignments, refusing anything not plainly data."""

    try:
        with open(rc_path, encoding="utf-8", errors="strict") as handle:
            lines = handle.read().splitlines()
    except OSError as error:
        raise EnvError("env_unreadable", f"{rc_path}: {error}") from error
    except UnicodeDecodeError as error:
        raise EnvError("env_untrusted", f"{rc_path}: not UTF-8: {error}") from error

    found: dict[str, str] = {}
    for line in lines:
        match = ASSIGNMENT.match(line.strip())
        if match is None:
            # Not a refusal. The rc is mostly `export NAME`, `unset shellHook`,
            # and assignments this tool does not carry; refusing on the first
            # one would refuse every real environment. The safety comes from
            # the allowlist and the store check below, not from understanding
            # every line.
            continue

        name = match.group("name")
        if name not in CARRIED:
            continue

        value = _unescape(match.group("value"))

        # Belt and braces. A single-quoted value cannot interpolate, so this
        # should be unreachable — which is why it is an assertion of the
        # invariant rather than a filter. If it ever fires, the grammar
        # assumption above is wrong and refusing is the only safe answer.
        if "$" in value or "`" in value:
            raise EnvError(
                "env_untrusted",
                f"{name} in {rc_path} contains a substitution",
            )

        found[name] = value

    if "PATH" not in found:
        raise EnvError("env_unrealised", f"{rc_path} declares no PATH")

    return found


def _filter_store(name: str, value: str, rc_path: str) -> str:
    """Keep the store entries of a path list, refusing any that is not one.

    Refusing rather than dropping: a non-store entry means this cache was not
    written by nix, and silently continuing with the rest would run an
    environment subtly different from the one on disk. It is also the check
    that keeps a directory from naming its own interpreter (see the module
    docstring), so a refusal here is a decision rather than an inconvenience.
    """

    kept: list[str] = []
    for entry in value.split(os.pathsep):
        if not entry:
            continue
        if not entry.startswith(NIX_STORE):
            raise EnvError(
                "env_untrusted",
                f"{name} in {rc_path} names {entry}, which is outside {NIX_STORE}",
            )
        kept.append(entry)
    return os.pathsep.join(kept)


# What makes a directory a project rather than a folder. Any one of these means
# somebody intended an environment here, so an unbuilt one is worth reporting
# rather than silently working around.
DECLARATIONS = (".envrc", "flake.nix", "shell.nix", "default.nix")


def _declares_an_environment(project: str) -> bool:
    """Whether this directory means to have a development environment."""

    return any(os.path.isfile(os.path.join(project, name)) for name in DECLARATIONS)


def resolve(project: str) -> Environment:
    """Resolve `project`'s cached flake environment, or raise `EnvError`.

    Every failure is named. The caller turns the name into a sentence a model
    can act on, which is the difference between "your environment is stale,
    ask the human to re-enter the directory" and an unexplained import error
    twenty lines into a script.
    """

    if not os.path.isdir(project):
        raise EnvError("no_such_directory", project)

    cache = os.path.join(project, CACHE_DIR)
    if not os.path.isdir(cache):
        # Two very different situations, and conflating them produced a
        # refusal with advice that could never be acted on (N85).
        #
        # A directory *declaring* an environment that has not been built is
        # `env_unrealised`: the fix is a human entering it, and running a
        # script against a bare interpreter would silently lack the packages
        # the project means to provide.
        #
        # A directory declaring nothing is not a failure at all. Most
        # directories are not projects — they hold data a script is pointed
        # at, and asking the user to `direnv allow` a folder of DICOM files is
        # advice about a file that does not exist. `no_environment` is raised
        # so the caller can choose, and the tool falls back to its own
        # interpreter.
        if _declares_an_environment(project):
            raise EnvError(
                "env_unrealised",
                f"{project} declares a development environment that has not been "
                f"built; nothing has realised it",
            )
        raise EnvError(
            "no_environment",
            f"{project} declares no development environment",
        )

    rc_path = _newest_rc(cache)
    profile = _check_profile(rc_path)
    _check_fresh(rc_path, project)

    parsed = _parse(rc_path)
    env = {
        name: _filter_store(name, value, rc_path) if name in PATH_LIKE else value
        for name, value in parsed.items()
    }

    return Environment(rc_path=rc_path, profile=profile, env=env)
