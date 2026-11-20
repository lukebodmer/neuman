"""List the entries of a directory.

The complement to `fs_search`: that one answers "where is this string", this one
answers "what is here". A tree cannot be explored with search alone — you have to
know a name before you can match it — so without this the only way to discover a
subdirectory was to guess it.

Output is one entry per line, `d` or `f` then a tab then the name, directories
first and each group sorted. Two readers depend on that shape: the model, which
gets a compact listing rather than prose, and the working-directory chooser,
which needs to know which entries can be descended into. A `ls -l`-style line
would carry mode bits and sizes that neither one uses.

Names, not paths. The caller already knows the directory it asked about, and
absolute paths in every line make a wide listing unreadable while telling the
model nothing it did not just supply.

Dotfiles are included, unlike `fs_search`'s walk. A search skipping `.git` is
avoiding noise across a whole tree; a listing of one directory that silently
omits entries is lying about what is there. Policy already keeps the dotfiles
that matter — `.ssh`, `.bashrc`, `.config/systemd` — out of reach entirely
(`DENY_SUBPATHS`), so hiding the rest would buy nothing.
"""

from __future__ import annotations

import json
import os
import re
import sys

# A directory with more entries than this is a data directory, not somewhere a
# person is navigating. Bounded for the same reason `fs_search` takes a `limit`:
# a listing the caller cannot act on is worse than one that stops at a number
# they chose. A listing this large also outgrows the inline preview and becomes
# a scratch file, which is not something to page through by hand.
DEFAULT_LIMIT = 1000

# Past this many entries, listing names one per line stops being an answer.
#
# Measured rather than guessed: the DICOM export that prompted this holds 4,080
# files, and listing the first thousand of them — `._FILE1`, `._FILE10`,
# `._FILE100` — filled a 16k context and ended the session with
# `exceed_context_size_error` (N82). The thousand names carried about as much
# information as one line saying how many there were, because they differ only
# in a serial number.
#
# Lower than `DEFAULT_LIMIT` on purpose: a directory of 200 source files is
# worth enumerating, and one of 4,000 scanner outputs is worth counting.
SUMMARY_THRESHOLD = 200

# A run of digits is the thing that varies between generated names, so
# replacing it with `#` turns `FILE1`, `FILE2`, … into one shape. That is the
# whole heuristic: it needs no knowledge of DICOM or of any other format, and
# a directory of hand-written names has no shared shapes so it never triggers.
_DIGITS = re.compile(r"\d+")

# A shape worth reporting as a group rather than as its members. Below this, a
# "group" is just the names with extra ceremony.
MIN_GROUP = 3


def _shape(name: str) -> str:
    """The name with digit runs collapsed, used as a grouping key."""

    return _DIGITS.sub("#", name)


def _numeric_key(name: str) -> tuple[object, ...]:
    """Sort key that orders digit runs by value rather than by character.

    `FILE2` before `FILE10`, which is what a reader of a numbered series
    expects.

    Only ever used *within* one group, which matters: a group's members share a
    shape, so they share the text/digit layout and the tuples compare
    position-by-position as the same type. Sorting a mixed set this way could
    compare an `int` against a `str` and raise.
    """

    # `_DIGITS` has no capture group, so splitting on it would discard the
    # numbers being sorted. `findall` over an alternation keeps both halves.
    parts = re.findall(r"\d+|\D+", name)
    return tuple(int(p) if p.isdigit() else p for p in parts)


def _summarise(kind: str, names: list[str]) -> list[str]:
    """Group `names` by shape, densest first.

    Returns display lines. Names whose shape is unique are reported
    individually, because a one-member group tells the reader less than the
    name does — the goal is to compress what is repetitive, not to hide what
    is not.
    """

    groups: dict[str, list[str]] = {}
    for name in names:
        groups.setdefault(_shape(name), []).append(name)

    counted: list[tuple[int, str]] = []
    singles: list[str] = []
    for shape, members in groups.items():
        if len(members) < MIN_GROUP:
            singles.extend(members)
            continue
        # The extremes, so the reader can see the range without the middle.
        #
        # Sorted by the *numbers* in the name rather than lexicographically,
        # because this group exists precisely because its members are numbered:
        # a plain sort puts `FILE99` above `FILE249` and reports the range of
        # 250 files as `FILE0 … FILE99`, which understates it by more than half.
        # Caught by a test asserting the range it expected.
        members.sort(key=_numeric_key)
        counted.append(
            (
                len(members),
                f"{kind}\t{shape}\tx {len(members)}\t({members[0]} .. {members[-1]})",
            )
        )

    # Densest group first: in a data directory the big group is the answer.
    # Sorted on the count that produced the line rather than by parsing it back
    # out, which would make the display format load-bearing for the ordering.
    counted.sort(key=lambda pair: -pair[0])
    lines = [line for _, line in counted]
    lines.extend(f"{kind}\t{name}" for name in sorted(singles))
    return lines


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("fs_list: expected one JSON argument", file=sys.stderr)
        return 2

    try:
        args = json.loads(argv[1])
    except json.JSONDecodeError as error:
        print(f"fs_list: invalid arguments: {error}", file=sys.stderr)
        return 2

    path = args.get("path")
    if not isinstance(path, str) or not path:
        print("fs_list: `path` is required", file=sys.stderr)
        return 2

    dirs_only = bool(args.get("dirs_only", False))
    limit = int(args.get("limit", DEFAULT_LIMIT))

    try:
        entries = list(os.scandir(path))
    except NotADirectoryError:
        print(f"fs_list: not a directory: {path}", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"fs_list: no such directory: {path}", file=sys.stderr)
        return 1
    except PermissionError:
        # The user's own account cannot open it. The tool host's surface check
        # refuses a path outside policy before this runs, so reaching here means
        # the mode bits said no — and telling those apart would require knowing
        # the surfaces, which the tool deliberately does not.
        print(f"fs_list: permission denied: {path}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"fs_list: {path}: {error}", file=sys.stderr)
        return 1

    directories: list[str] = []
    files: list[str] = []
    for entry in entries:
        try:
            # `follow_symlinks=False`: a symlink is reported as what it is in
            # this directory, not as what it points at. Following would let a
            # link to a file outside reach be listed as an ordinary directory,
            # and the descent that followed would be denied by the kernel with
            # no explanation the caller could act on.
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            is_dir = False
        (directories if is_dir else files).append(entry.name)

    directories.sort()
    files.sort()

    # A directory large enough that names stop being the answer gets counted
    # by shape instead. `summarise` forces it either way, for a caller that
    # wants the shape of a smaller directory or the names of a larger one.
    listed = directories if dirs_only else directories + files
    requested = args.get("summarise")
    summarise = len(listed) > SUMMARY_THRESHOLD if requested is None else bool(requested)

    if summarise and listed:
        lines = _summarise("d", directories)
        if not dirs_only:
            lines.extend(_summarise("f", files))

        print(f"({len(listed)} entries, grouped by name shape; # is a run of digits)")
        for line in lines[:limit]:
            print(line)
        if len(lines) > limit:
            print(f"(stopped at {limit} groups)")
        # The counts above are the whole directory, so a caller that needs
        # actual names knows the listing exists and how to ask for it.
        print("(for names, call again with summarise=false, or use fs_search)")
        return 0

    shown = 0
    for name in directories:
        if shown >= limit:
            print(f"(stopped at {limit} entries)")
            return 0
        print(f"d\t{name}")
        shown += 1

    if not dirs_only:
        for name in files:
            if shown >= limit:
                print(f"(stopped at {limit} entries)")
                return 0
            print(f"f\t{name}")
            shown += 1

    if shown == 0:
        # Exit 0, for `fs_search`'s reason: an empty directory is an answer. A
        # non-zero exit is a failed dispatch, which would make an empty folder
        # look like a broken tool.
        print("(empty)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
