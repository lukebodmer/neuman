"""Search file contents, or find files by name.

Walks and matches in-process rather than shelling out to `grep` or `rg`. Two
reasons, and the first is the real one:

* **The shape is the product.** Hits grouped by the file they are in, newest
  modified first, bounded twice with a note saying which bound stopped the walk.
  All of that would have to be rebuilt on top of a pipeline's output, and a
  model given raw `grep` output pays for it in context and in turns.
* `grep`'s exit codes conflict with the dispatch's. It exits 1 for "no
  matches", which is an ordinary result here and a failed dispatch there.

**`path` may name a file or a directory.** `os.walk` on a file yields nothing,
so a single-file search used to answer `(no matches)` for a file that plainly
contained the pattern — a false answer with a success exit, which is worse than
an error. The model read it as "the symbol is not there" and spent two extra
round-trips on a file it had already found.

**Newest-modified first.** A search on a working tree is usually looking for
something recently touched, and a directory-order walk buries it under generated
files. The candidate list is sorted by mtime before anything is read.

Bounded twice, and both say so: by `limit`, the number of matches the caller
asked for, and by a byte budget so one search cannot consume the conversation.
The output is delivered inline (`persist_output = false`) — a result the caller
asked for should be the result it gets, not a preview of a scratch file it then
has to open. When either bound stops the walk the tool says which, so a short
answer is never mistaken for a complete one.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import sys

# Not configurable: a search is expected to be cheap, and a binary that
# happens to contain the pattern is noise rather than a result.
MAX_FILE_BYTES = 2 * 1024 * 1024

# How much one search may print before it stops and says so. A match line can be
# arbitrarily long, so `limit` alone does not bound the bytes; this does. Sized
# for the context window, like `fs_read`'s page budget, and for the same reason:
# the tool's output is delivered inline rather than persisted.
OUTPUT_BUDGET = 32_768

# What one hit prints of a line longer than this: the match and its
# neighbourhood, marked at whichever ends were cut. A minified page is a single
# 130 KB line, and printing it whole meant the client's cap truncated the entry
# before the match appeared — a hit that cannot be seen is worse than no hit
# (N181).
MATCH_WINDOW = 600


def _snippet(line: str, match: re.Match[str]) -> str:
    """The hit with context, bounded, cut ends marked."""

    if len(line) <= MATCH_WINDOW:
        return line
    start = max(0, match.start() - MATCH_WINDOW // 3)
    end = min(len(line), start + MATCH_WINDOW)
    start = max(0, end - MATCH_WINDOW)
    return f"{'…' if start else ''}{line[start:end]}{'…' if end < len(line) else ''}"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("fs_search: expected one JSON argument", file=sys.stderr)
        return 2

    try:
        args = json.loads(argv[1])
    except json.JSONDecodeError as error:
        print(f"fs_search: invalid arguments: {error}", file=sys.stderr)
        return 2

    pattern = args.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        print("fs_search: `pattern` is required", file=sys.stderr)
        return 2

    # Omitted means the working directory, which is what the manifest says it
    # means. It used to claim "every readable path" while doing this anyway; the
    # tool has no list of the account's surfaces, so the description was the
    # only thing that could be corrected without inventing one.
    root = args.get("path") or "."
    glob = args.get("glob")
    name_only = bool(args.get("name_only", False))
    limit = int(args.get("limit", 100))

    try:
        expression = re.compile(pattern)
    except re.error as error:
        print(f"fs_search: invalid pattern: {error}", file=sys.stderr)
        return 2

    if os.path.isdir(root):
        candidates = _candidates(root, glob)
    elif os.path.isfile(root):
        # A file is a valid target, not a directory that happened to yield
        # nothing. The glob still applies to its name, so a caller who asks for
        # `*.py` and names a `.md` gets an honest empty result.
        candidates = [root] if not glob or fnmatch.fnmatch(os.path.basename(root), glob) else []
    else:
        # A missing path used to walk to nothing and answer `(no matches)`.
        # Naming the path is the whole fix: "no such file" is a fact, and a
        # successful empty search is a false statement about its contents.
        print(f"fs_search: no such file or directory: {root}", file=sys.stderr)
        return 1

    # Newest first, as the manifest promises. Statting candidates is cheap next
    # to reading them, and the ordering is what puts a file the caller just
    # edited above generated output.
    candidates.sort(key=_mtime, reverse=True)

    found = 0
    used = 0
    stop = None  # "limit" or "bytes"

    for path in candidates:
        if name_only:
            if expression.search(os.path.basename(path)):
                entry = f"{path}\n"
                # At least one result is always printed, whatever its size;
                # after that the byte budget is enforced.
                if used and used + len(entry) > OUTPUT_BUDGET:
                    stop = "bytes"
                    break
                sys.stdout.write(entry)
                used += len(entry)
                found += 1
        else:
            matched, wrote, exhausted = _search_file(
                path, expression, limit - found, OUTPUT_BUDGET - used, used
            )
            found += matched
            used += wrote
            if exhausted:
                stop = "bytes"
                break

        if found >= limit:
            stop = "limit"
            break

    if stop == "limit":
        print(f"(stopped at {limit} matches)")
    elif stop == "bytes":
        print(
            f"(stopped at {found} matches to stay under the output budget; "
            "narrow the pattern or path)"
        )
    elif found == 0:
        # Deliberately exit 0: "no matches" is an answer, not a failure. The
        # client treats a non-zero exit as a failed dispatch, which would make
        # an empty search look like a broken tool.
        print("(no matches)")
    return 0


def _candidates(root: str, glob: str | None) -> list[str]:
    """Every file under `root` the glob admits, dot-directories excluded.

    The glob is matched against the file's name *and* its path relative to
    `root`, so `*.py` finds files at any depth and `src/**/*.py` scopes the
    search. Matching the name alone made the second form silently return
    `(no matches)` — the same false negative as a single-file `path` before it
    was accepted.
    """

    paths: list[str] = []
    for directory, subdirectories, files in os.walk(root, onerror=None):
        # Skip what nobody means to search, and what would dominate the results.
        subdirectories[:] = [d for d in subdirectories if not d.startswith(".")]
        for name in files:
            if glob:
                relative = os.path.relpath(os.path.join(directory, name), root)
                if not (fnmatch.fnmatch(name, glob) or fnmatch.fnmatch(relative, glob)):
                    continue
            paths.append(os.path.join(directory, name))
    return paths


def _mtime(path: str) -> float:
    """Sort key that costs no correctness when a file vanishes mid-walk."""

    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _search_file(
    path: str,
    expression: re.Pattern[str],
    remaining: int,
    budget: int,
    global_used: int,
) -> tuple[int, int, bool]:
    """Print matches in `path`. Returns (matched, bytes_written, exhausted).

    `exhausted` says the budget stopped a match from being printed, which the
    caller reports: a result that stopped early must not read as a complete
    one.
    """

    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return 0, 0, False
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except (OSError, UnicodeDecodeError):
        # Unreadable or not text. Both are ordinary while walking a tree, and
        # neither is worth a line of output.
        return 0, 0, False

    matched = 0
    wrote = 0
    for number, line in enumerate(lines, start=1):
        if matched >= remaining:
            break
        match = expression.search(line)
        if match is None:
            continue
        entry = f"{path}:{number}:{_snippet(line.rstrip(), match)}\n"
        # One match is always printed, whatever its size; after that the budget
        # is enforced.
        if (global_used + wrote) and wrote + len(entry) > budget:
            return matched, wrote, True
        sys.stdout.write(entry)
        matched += 1
        wrote += len(entry)
    return matched, wrote, False


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
