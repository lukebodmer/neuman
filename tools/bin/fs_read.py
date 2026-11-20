"""Read a UTF-8 text file.

The narrowest tool, and the one that sets the pattern: it does no path
checking of its own. The client's tool host checks a declared `path` against
the account's surfaces before this runs, resolving symlinks, so by the time
`main` is called the question of reach has been answered once; answering it
again here would be a second implementation that eventually disagrees with the
first.

What it does own is *shape*: the manifest declares `offset` and `limit`, so
those have to mean something, a file that is not valid UTF-8 has to fail as a
tool error rather than a traceback, and a page that is not the whole file has
to say so. The last one is the point of this docstring's length.

**A partial read announces itself.** This tool bounds its own output — by the
caller's `limit` and by a byte budget — and delivers it inline
(`persist_output = false`): a page the caller asked for should be the page it
gets, not a preview of a scratch file it then has to open. Any page that is not
the whole file ends with `[showing lines X-Y of N; use offset=Z to continue]`.
That is the shape `pi`'s `read` and Claude Code's `Read` use, and for the same
reason: left unstated, a fragment reads like the whole, and the model answers
from it.

**A line larger than a page is shown from its head, and says so.** Minified
pages, JSONL rows and spilled documents are one enormous line, which no line
page can contain; it is shown up to the byte budget with a notice giving its
size and naming `fs_search` for the rest (N181).
"""

from __future__ import annotations

import json
import os
import sys

# Leading bytes that identify a format, longest first so a prefix cannot shadow a
# longer match. Deliberately short: this exists to answer "can you see my image",
# not to be `file(1)`.
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "PNG image"),
    (b"\xff\xd8\xff", "JPEG image"),
    (b"GIF87a", "GIF image"),
    (b"GIF89a", "GIF image"),
    (b"%PDF-", "PDF document"),
    (b"PK\x03\x04", "ZIP archive (or a zip-based format such as .docx/.xlsx)"),
    (b"\x1f\x8b", "gzip archive"),
    (b"\x7fELF", "ELF executable"),
    (b"BM", "BMP image"),
)

# How much of a file one call returns before it stops and says where to resume.
#
# Sized for the context window, not the client's forwarding cap. This tool pages
# by the caller's own `limit`, so its output is delivered inline rather than
# persisted (`persist_output = false`): a page the model asked for should be the
# page it gets, not a preview of a scratch file it then has to open. 32 KiB is a
# substantial page and still a fraction of the smallest window here, and the
# continuation notice is what makes the next page cheap.
OUTPUT_BUDGET = 32_768


def _describe(path: str) -> str:
    """A short, honest identification of a file the reader could not decode."""

    try:
        with open(path, "rb") as handle:
            head = handle.read(16)
        size = os.path.getsize(path)
    except OSError:
        return "unreadable"

    kind = next((name for magic, name in _MAGIC if head.startswith(magic)), None)
    if kind is None and head[4:12] in (b"ftypavif", b"ftypheic"):
        kind = "AVIF/HEIC image"
    if kind is None and head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        kind = "WebP image"
    if kind is None:
        kind = "binary data"

    return f"{kind}, {size} bytes"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("fs_read: expected one JSON argument", file=sys.stderr)
        return 2

    try:
        args = json.loads(argv[1])
    except json.JSONDecodeError as error:
        print(f"fs_read: invalid arguments: {error}", file=sys.stderr)
        return 2

    path = args.get("path")
    if not isinstance(path, str) or not path:
        print("fs_read: `path` is required", file=sys.stderr)
        return 2

    offset = args.get("offset", 1)
    limit = args.get("limit")

    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except FileNotFoundError:
        print(f"fs_read: no such file: {path}", file=sys.stderr)
        return 1
    except IsADirectoryError:
        print(f"fs_read: not a regular file: {path}", file=sys.stderr)
        return 1
    except PermissionError:
        # The ordinary outcome for a file the user's own account cannot open.
        # Saying so plainly is more useful than a traceback.
        print(f"fs_read: not permitted: {path}", file=sys.stderr)
        return 1
    except UnicodeDecodeError:
        # Say *what it is*, not only what it is not. "not valid UTF-8" sent a
        # model looking for another way to inspect the file instead of
        # reporting what it found — two wasted turns and a wrong conclusion
        # (N59). `_describe` answers the question here.
        print(f"fs_read: not a text file: {path} ({_describe(path)})", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"fs_read: {path}: {error}", file=sys.stderr)
        return 1

    # `offset` is 1-indexed because that is what an editor and a stack trace
    # both use, and a tool a model drives should agree with what the model has
    # read everywhere else.
    start = max(int(offset) - 1, 0)
    total_lines = len(lines)

    if total_lines == 0:
        # Not silence. An empty page and a page that was never produced look the
        # same to a reader, and the file existing is a fact worth stating.
        print(f"fs_read: {path} exists but is empty")
        return 0

    if start >= total_lines:
        # The read tool's own error, not an empty string: an offset past the end
        # is a question about a file with fewer lines than the caller thought,
        # and answering it with nothing hides the count that resolves it.
        print(
            f"fs_read: offset {offset} is beyond end of file ({total_lines} lines)",
            file=sys.stderr,
        )
        return 1

    wanted = lines[start:]
    if limit is not None:
        wanted = wanted[: int(limit)]

    # Fill the page a line at a time, stopping *before* a line that would cross
    # the byte budget. A line is never cut unmarked: one larger than a whole page
    # cannot be paged by line at all, so its head is shown with a notice naming
    # its size and the tool that can find text inside it.
    selected: list[str] = []
    used = 0
    for line in wanted:
        encoded = len(line.encode("utf-8"))
        if not selected and encoded > OUTPUT_BUDGET:
            # A minified page, a JSONL row, a document spilled to one line. The
            # first unattended sweep hit this on every modern site and the tool
            # answered by naming `fs_search` and `shell`, which the task's
            # envelope did not grant: three turns spent finding another way in
            # (N181). Show the head, state the size, and name the way in.
            window = line.encode("utf-8")[: OUTPUT_BUDGET - 512].decode("utf-8", "ignore")
            sys.stdout.write(window)
            lead = "" if window.endswith("\n") else "\n"
            sys.stdout.write(
                f"{lead}[line {start + 1} is {encoded:,} bytes, longer than one page; "
                f"showing its first {len(window):,}. The rest of the line is not "
                f"shown — fs_search finds text anywhere in it, by pattern.]\n"
            )
            return 0
        if selected and used + encoded > OUTPUT_BUDGET:
            break
        selected.append(line)
        used += encoded

    if not selected:
        # `limit: 0` is outside the schema, but the tool validates its own shape
        # rather than trusting the caller. Nothing asked for, nothing returned.
        return 0

    end = start + len(selected)  # exclusive, as a line index
    sys.stdout.write("".join(selected))
    if end < total_lines:
        # A blank line first when the content does not end in one, so the
        # notice cannot be read as the next line of the file.
        lead = "" if selected[-1].endswith("\n") else "\n"
        sys.stdout.write(
            f"{lead}[showing lines {start + 1}-{end} of {total_lines}; "
            f"use offset={end + 1} to continue]\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
