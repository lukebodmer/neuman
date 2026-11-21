"""Path arithmetic over composed policy: expansion, and what a path is inside.

Not confinement. The client runs as the human and reaches what the human
reaches (D57). What remains is the two questions a *server* can answer without
a filesystem — what does a policy entry actually name, and is this candidate
inside it — and both exist to give an answer better than an opaque failure.

An entry naming a home directory expands to the configured data
subdirectories, so the home itself is never named wholesale and dotfiles are
excluded by construction. Two spellings name it: `$HOME`, which is the one to
use when the client may not be on this machine, and the absolute path that
happens to be the acting account's home. Which directory *is* that home is the
acting machine's answer, not this server's — the client reports it when it
connects (N145). With no client connected there is no home, and a `$HOME` entry
reaches nothing: an account is not a host account (D67), so this server has no
directory of its own to offer in its place.
"""

from __future__ import annotations

from collections.abc import Sequence

from .compose import EffectivePolicy

__all__ = [
    "DEFAULT_HOME_DATA_DIRS",
    "HOME_TOKEN",
    "as_home_data_dirs",
    "attachable",
    "expand_paths",
    "within_reach",
]

# The spelling an operator should use for "the acting account's home", because
# it does not name a machine. The absolute path is also accepted — an operator
# who writes it out means the same thing — and the two are expanded identically.
HOME_TOKEN = "$HOME"

# The folders a home-naming entry expands to on an appliance nobody has
# configured, and what "Use defaults" in the administration page restores.
#
# This is runtime configuration, not a deployment fact: it is a `settings` row
# an administrator edits under Files, and a change lands on the next turn
# (D104). The value here is what a fresh database has in force until then.
DEFAULT_HOME_DATA_DIRS = ("Documents", "Desktop", "Downloads", "workspace")


def as_home_data_dirs(value: object) -> tuple[str, ...] | None:
    """The configured data subdirectories, or None if `value` is not one.

    A name is one path component under the home. A separator, `.`, `..`, a
    control character or a name with padding means somewhere other than what
    the row appears to say, and an empty list would make `$HOME` name nothing
    while looking like a setting — all refused rather than interpreted.

    This is not confinement: the list decides what `$HOME` names, and `shell`
    and `python` reach past it anyway (D57). The check exists so a malformed
    row fails at the edge with a message, not as a path the model is told
    about and the client then refuses.
    """

    if not isinstance(value, (list, tuple)):
        return None
    names: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or item != item.strip():
            return None
        if item in (".", "..") or "/" in item or "\x00" in item:
            return None
        names.append(item)
    if not names:
        return None
    # Duplicates are harmless but would be repeated in the prompt on every
    # turn; collapse them while keeping the operator's order.
    return tuple(dict.fromkeys(names))


def _normalize(path: str) -> str:
    """Trailing-slash-free, as `common.schema.json#/$defs/absPath` requires."""

    stripped = path.rstrip("/")
    return stripped or "/"


def _expand(entries: Sequence[str], home: str | None, data_dirs: Sequence[str]) -> list[str]:
    """Expand a home-naming entry into its data subdirectories, preserving order."""

    home = _normalize(home) if home else None
    result: list[str] = []
    for entry in entries:
        normalized = _normalize(entry)
        if normalized == HOME_TOKEN:
            if home is not None:
                result.extend(f"{home}/{name}" for name in data_dirs)
        elif normalized == home:
            result.extend(f"{home}/{name}" for name in data_dirs)
        else:
            result.append(normalized)

    seen: set[str] = set()
    ordered: list[str] = []
    for path in result:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def expand_paths(
    entries: Sequence[str], *, home: str | None, data_dirs: Sequence[str]
) -> list[str]:
    """The paths a policy entry actually reaches.

    The raw list may contain an entry naming the home directory, which is not
    named wholesale, so a picker built from the raw list would offer a path the
    dispatch then refuses — a confusing way to learn about the expansion.
    """

    return _expand(entries, home, data_dirs)


def within_reach(
    candidate: str,
    policy: EffectivePolicy,
    *,
    home: str | None,
    data_dirs: Sequence[str],
) -> bool:
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

    for granted in _expand(policy.files_read, home, data_dirs):
        if target == granted or target.startswith(f"{granted}/"):
            return True
    return False


def attachable(
    candidate: str,
    policy: EffectivePolicy,
    *,
    home: str | None,
    drops: str | None,
    data_dirs: Sequence[str],
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

    if within_reach(candidate, policy, home=home, data_dirs=data_dirs):
        return True
    if not drops:
        return False
    target = _normalize(candidate)
    if not target.startswith("/") or ".." in target.split("/"):
        return False
    return target.startswith(f"{_normalize(drops)}/")
