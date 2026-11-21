"""Path arithmetic over composed policy: expansion, and what a path is inside.

Not confinement. The client runs as the human and reaches what the human
reaches (D57). What remains is the two questions a *server* can answer without
a filesystem — what does a policy entry actually name, and is this candidate
inside it — and both exist to give an answer better than an opaque failure.

`$HOME` names the acting account's home directory, and `$HOME/<path>` a path
under it: `$HOME/Documents` is that folder wherever the client runs, which is
what lets one role serve several people and more than one operating system
(D105, D106). The bare token means the home whole, dotfiles included; a role
that wants two folders names two. Which directory *is* that home is the acting
machine's answer, not this server's — the client reports it when it connects
(N145) — so with no client connected a `$HOME` entry reaches nothing: an
account is not a host account (D67), and this server has no directory of its
own to offer in its place.

An absolute path is a path. It names one machine's directory and is not
reinterpreted when the client reports a different home, so a policy that must
hold on more than one machine uses the token, and a machine that needs
different folders gets them named in that account's own policy.
"""

from __future__ import annotations

from collections.abc import Sequence

from .compose import EffectivePolicy

__all__ = ["HOME_TOKEN", "attachable", "expand_paths", "within_reach"]

# The spelling an operator should use for "the acting account's home", because
# it does not name a machine.
HOME_TOKEN = "$HOME"


def _normalize(path: str) -> str:
    """Trailing-slash-free, as `common.schema.json#/$defs/absPath` requires."""

    stripped = path.rstrip("/")
    return stripped or "/"


def _resolve(entry: str, home: str | None) -> str | None:
    """The path one policy entry names, or None if it names nothing here.

    `$HOME` is the acting account's home and `$HOME/<path>` is a path under
    it; both need the client's answer to "which home", so with no client
    connected they name nothing. Everything else is taken as written, and a
    string that is neither nor absolute names nothing: the schema refuses it at
    the edge, so this is the second line of defence rather than a parser.
    """

    if entry == HOME_TOKEN:
        return home
    if entry.startswith(HOME_TOKEN + "/"):
        rest = entry[len(HOME_TOKEN) + 1 :]
        if not rest or any(part in ("", ".", "..") for part in rest.split("/")):
            return None
        return f"{home}/{rest}" if home else None
    normalized = _normalize(entry)
    return normalized if normalized.startswith("/") else None


def _expand(entries: Sequence[str], home: str | None) -> list[str]:
    """Resolve every entry against the client's home, preserving order."""

    normalized_home = _normalize(home) if home else None
    result: list[str] = []
    for entry in entries:
        resolved = _resolve(entry, normalized_home)
        if resolved is not None:
            result.append(resolved)

    seen: set[str] = set()
    ordered: list[str] = []
    for path in result:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def expand_paths(entries: Sequence[str], *, home: str | None) -> list[str]:
    """The paths a policy entry actually reaches.

    The raw list may contain the token, which is not a path until the client
    says which home it means, so a picker built from the raw list would offer
    `$HOME` or `$HOME/Documents` as if they were directories — a confusing way
    to learn about the expansion.
    """

    return _expand(entries, home)


def within_reach(candidate: str, policy: EffectivePolicy, *, home: str | None) -> bool:
    """Is ``candidate`` inside what policy already grants for reading?

    This is what keeps a working directory a *hint*. A session can point the
    agent at a subdirectory of its reach and cannot point it anywhere else;
    without this check the feature would be a way to launder reach through the
    UI — type a path, get access.

    Compared after normalisation and against a path *boundary*, so `/srv/engine`
    is not treated as inside `/srv/eng`. A prefix test on the raw strings is how
    that gets through.
    """

    target = _normalize(candidate)
    if not target.startswith("/") or ".." in target.split("/"):
        return False

    for granted in _expand(policy.files_read, home):
        if target == granted or target.startswith(f"{granted}/"):
            return True
    return False


def attachable(
    candidate: str,
    policy: EffectivePolicy,
    *,
    home: str | None,
    drops: str | None,
) -> bool:
    """May ``candidate`` be attached to a conversation?

    Anything policy reaches for reading, and a file the person dropped on the
    window (D102): the connected client's `drops` directory holds bytes that
    person handed over from their own machine, so naming one grants the agent
    nothing the person did not give it. Strictly *inside* the directory — the
    directory itself is not a file anyone dropped — and with no client there is
    no `drops`, so such an attachment is refused until one connects, like a
    `$HOME` grant.
    """

    if within_reach(candidate, policy, home=home):
        return True
    if not drops:
        return False
    target = _normalize(candidate)
    if not target.startswith("/") or ".." in target.split("/"):
        return False
    return target.startswith(f"{_normalize(drops)}/")
