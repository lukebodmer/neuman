"""Resolving an edit, and describing what it would do.

One module, because **a preview and a write must not be two implementations.**
The approval frame shows a human a diff before a mutating write runs, and the
only thing that makes that diff worth trusting is that the same function
produced the bytes which later land on disk. Two code paths that agree today are
not a property; they are a coincidence with a maintenance schedule — which is
exactly N35 and N38, where the only thing exercising the code under test was
something that agreed with it.

So `plan()` is the whole decision. It reads the file, resolves the edits, and
returns the final bytes together with a diff of them. `fs_write` and `fs_edit`
write `Plan.content` and nothing else; with `preview = true` they print the diff
and write nothing. The dry run and the real run differ in one branch, after the
decision both share.

**This lives in `tools/`, not in the server, and that is forced rather than
chosen.** The file is on the acting human's machine, and the core has no
filesystem reach at all — computing a diff there would need precisely the reach
the architecture refuses it. A preview is therefore a *dispatch* through the same
tool as the write it previews, and the core learns the diff the way it learns
anything else about a filesystem: by asking the machine that has it.

Three things this deliberately does not do:

* **No fuzzy matching.** `old_text` matches byte for byte or the edit is
  refused. A near-match that silently retargets turns an approved diff into a
  different write — the human approved the diff they were shown, and whitespace
  tolerance would make that diff a guess. The refusal names the closest line so
  a model can correct it.
* **No partial application.** Every edit applies or none do. A write landing
  three of five edits leaves a file in a state nobody asked for and the tool
  cannot put it back.
* **No overlap.** Two edits touching the same region are refused rather than
  resolved, because which one won would depend on the order the model happened
  to list them in.
"""

from __future__ import annotations

import difflib
import itertools
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from typing import Any

__all__ = [
    "BOM",
    "SENTINEL",
    "EditError",
    "Plan",
    "number_diff",
    "plan",
    "render_diff",
    "report",
    "run",
    "write_plan",
]

# A diff is for a human reading a chat window, not an archive. Past this many
# lines the approval frame is unreadable anyway, and the whole patch is in the
# event log either way.
MAX_DIFF_LINES = 400

# The key the harness looks for on the last line of stdout. A sentinel rather
# than a second file descriptor because a tool has only the two streams the
# client forwards, and stdout rather than stderr because stderr is where a
# tool's errors must stay readable.
SENTINEL = "neuman_diff"

# Never matched, always preserved: the model cannot have included an invisible
# character in `old_text`, and dropping it would rewrite the file's encoding as
# a side effect of an unrelated edit.
BOM = "﻿"


class EditError(Exception):
    """An edit that cannot be applied, with advice a model can act on."""


@dataclass(frozen=True, slots=True)
class Plan:
    """What a write would do. `content` is what to write, byte for byte."""

    path: str
    content: str
    diff: str
    created: bool
    old_bytes: int
    new_bytes: int

    def structured(self) -> dict[str, Any]:
        """The human-facing half of a result, for the structured channel."""

        return {
            "path": self.path,
            "diff": self.diff,
            "rows": number_diff(self.diff),
            "created": self.created,
            "old_bytes": self.old_bytes,
            "new_bytes": self.new_bytes,
        }

    def summary(self, *, preview: bool) -> str:
        """The one line the model gets.

        It asked for this change and does not need it read back; a diff of its
        own edit is tokens spent to tell it what it just said. The human needs
        the opposite, which is why they are separate channels.

        The tense is a parameter rather than a substitution on the finished
        sentence: a preview that claims to have written something is worse than
        no message, and editing the wording later must not be able to cause it.
        """

        if preview:
            verb = "would create" if self.created else "would replace"
        else:
            verb = "created" if self.created else "wrote"
        delta = self.new_bytes - self.old_bytes
        return f"{verb} {self.path} ({self.new_bytes} bytes, {delta:+d})"


def _read(path: str) -> tuple[str, bool]:
    """Current contents, and whether the file is new."""

    try:
        with open(path, encoding="utf-8") as stream:
            return stream.read(), False
    except FileNotFoundError:
        return "", True
    except UnicodeDecodeError as error:
        raise EditError(f"{path} is not UTF-8 text, so it cannot be edited as text.") from error
    except IsADirectoryError as error:
        raise EditError(f"{path} is a directory.") from error
    except PermissionError as error:
        raise EditError(
            f"{path} cannot be read. It may be outside the paths your policy grants."
        ) from error


def _closest_line(haystack: str, needle: str) -> str | None:
    """The nearest existing line to a failed match, as a correction hint."""

    lines = needle.strip().splitlines()
    if not lines:
        return None
    matches = difflib.get_close_matches(lines[0], haystack.splitlines(), n=1, cutoff=0.6)
    return matches[0] if matches else None


def _resolve(content: str, edits: list[Any], path: str) -> str:
    """Apply every edit, or raise. Exact matches only, no overlaps."""

    resolved: list[tuple[int, int, str]] = []

    for index, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise EditError(f"edits[{index}] must be an object with old_text and new_text.")
        old = edit.get("old_text")
        new = edit.get("new_text")
        if not isinstance(old, str) or not isinstance(new, str):
            raise EditError(f"edits[{index}] needs string `old_text` and `new_text`.")
        if not old:
            raise EditError(
                f"edits[{index}] has empty `old_text`. Use fs_write to create or replace a "
                "whole file; to insert, anchor on a line that is already there."
            )
        if old == new:
            raise EditError(f"edits[{index}] changes nothing: old_text == new_text.")

        count = content.count(old)
        if count == 0:
            hint = _closest_line(content, old)
            advice = f" The closest line in the file is {hint!r}." if hint else ""
            raise EditError(
                f"edits[{index}]: `old_text` does not appear in {path}. It must match "
                f"exactly, including whitespace and indentation.{advice}"
            )
        if count > 1:
            raise EditError(
                f"edits[{index}]: `old_text` appears {count} times in {path}. Include enough "
                "surrounding context to name one place uniquely."
            )

        start = content.index(old)
        resolved.append((start, start + len(old), new))

    resolved.sort()
    for (_, a_end, _), (b_start, _, _) in itertools.pairwise(resolved):
        if a_end > b_start:
            raise EditError(
                f"two edits overlap in {path}. Merge them into one edit, or target regions "
                "that do not touch."
            )

    # Right to left, so an earlier edit's offsets stay valid.
    result = content
    for start, end, new in reversed(resolved):
        result = result[:start] + new + result[end:]
    return result


def render_diff(path: str, old: str, new: str) -> str:
    """A unified diff, truncated for a chat window.

    Standard `diff -u` output, so it can be read by anything that reads a patch
    and the event log keeps something a human can apply.
    """

    lines = list(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=path,
            tofile=path,
            n=3,
        )
    )
    if len(lines) > MAX_DIFF_LINES:
        dropped = len(lines) - MAX_DIFF_LINES
        lines = lines[:MAX_DIFF_LINES]
        lines.append(f"... {dropped} more diff lines (the whole patch is in the log)\n")
    return "".join(lines)


def number_diff(diff: str) -> list[dict[str, Any]]:
    """Turn a unified diff into numbered rows, for a client that renders one.

    A patch carries its line numbers only in hunk headers (`@@ -4,7 +4,8 @@`),
    so a reader wanting "which line is this" has to track two counters through
    the body. Doing it here means every client gets the same numbering instead of
    each reimplementing the walk — and the raw `diff` stays in the payload beside
    this, because a patch is what you apply and rows are what you look at.

    Rows are `{kind, old, new, text}` where `kind` is "context", "add", "remove"
    or "hunk", and `old`/`new` are the line numbers in each file (null on the
    side where the line does not exist).
    """

    rows: list[dict[str, Any]] = []
    old_no = new_no = 0

    for line in diff.splitlines():
        if line.startswith(("--- ", "+++ ")):
            continue
        if line.startswith("@@"):
            # `@@ -old,count +new,count @@` — the counts are not needed, only
            # where each side resumes.
            try:
                chunk = line.split("@@")[1].strip()
                left, right = chunk.split(" ")[:2]
                old_no = int(left[1:].split(",")[0])
                new_no = int(right[1:].split(",")[0])
            except (IndexError, ValueError):
                # A header this cannot parse is shown as-is rather than dropped:
                # losing a hunk boundary would silently join two separate
                # changes into one apparently contiguous block.
                rows.append({"kind": "hunk", "old": None, "new": None, "text": line})
                continue
            rows.append({"kind": "hunk", "old": None, "new": None, "text": line})
            continue
        if line.startswith("+"):
            rows.append({"kind": "add", "old": None, "new": new_no, "text": line[1:]})
            new_no += 1
        elif line.startswith("-"):
            rows.append({"kind": "remove", "old": old_no, "new": None, "text": line[1:]})
            old_no += 1
        elif line.startswith("\\"):
            # "\ No newline at end of file" belongs to the line above it.
            rows.append({"kind": "context", "old": None, "new": None, "text": line})
        else:
            rows.append({"kind": "context", "old": old_no, "new": new_no, "text": line[1:]})
            old_no += 1
            new_no += 1

    return rows


def plan(
    path: str,
    *,
    content: str | None = None,
    edits: list[Any] | None = None,
) -> Plan:
    """What writing to `path` would do, and the bytes that would do it.

    Exactly one of `content` (whole file) or `edits` (partial) is given. A caller
    writes `Plan.content` and never re-derives it from the arguments — that
    re-derivation is the drift this module exists to prevent.
    """

    if (content is None) == (edits is None):
        raise EditError("give exactly one of `content` or `edits`.")

    current, created = _read(path)

    if content is not None:
        new = content
        if not created and new == current:
            raise EditError(f"{path} already has exactly these contents.")
    else:
        if created:
            raise EditError(
                f"{path} does not exist, so there is nothing to edit. Use fs_write to "
                "create it."
            )
        if not edits:
            raise EditError("`edits` is empty.")
        new = _resolve(current, edits, path)

    if current.startswith(BOM) and not new.startswith(BOM):
        new = BOM + new

    return Plan(
        path=os.path.abspath(path),
        content=new,
        diff=render_diff(path, current, new),
        created=created,
        old_bytes=len(current.encode("utf-8")),
        new_bytes=len(new.encode("utf-8")),
    )


def report(intent: Plan, *, preview: bool) -> None:
    """Print the dispatch's output: one line of prose, then the diff frame.

    The diff goes to stdout as a single JSON line with a sentinel key, which the
    harness lifts out of the stream into the approval frame and the transcript's
    structured channel. Stdout rather than stderr because stderr is model-visible
    too and a tool that writes JSON there is a tool whose errors are unreadable;
    a sentinel rather than a second fd because a tool has only the two streams
    the client forwards.
    """

    print(intent.summary(preview=preview))
    print(json.dumps({SENTINEL: intent.structured()}))


def run(name: str, argv: list[str], *, whole_file: bool) -> int:
    """The shared entry point for `fs_write` and `fs_edit`.

    Both tools differ in how they say *what* to write and not at all in how the
    decision is made, so the argument handling, the preview branch and the write
    live here once.
    """

    if len(argv) != 2:
        print(f"{name}: expected one JSON argument", file=sys.stderr)
        return 2
    try:
        args = json.loads(argv[1])
    except json.JSONDecodeError as error:
        print(f"{name}: invalid arguments: {error}", file=sys.stderr)
        return 2

    path = args.get("path")
    if not isinstance(path, str) or not path:
        print(f"{name}: `path` is required", file=sys.stderr)
        return 2
    preview = bool(args.get("preview", False))

    try:
        if whole_file:
            content = args.get("content")
            if not isinstance(content, str):
                print(f"{name}: `content` is required", file=sys.stderr)
                return 2
            intent = plan(path, content=content)
        else:
            edits = args.get("edits")
            if not isinstance(edits, list):
                print(f"{name}: `edits` must be a list", file=sys.stderr)
                return 2
            intent = plan(path, edits=edits)
    except EditError as error:
        print(f"{name}: {error}", file=sys.stderr)
        return 1

    if preview:
        # The dry run. Nothing touches the filesystem after this point, which is
        # what makes the diff safe to show before a verdict exists.
        report(intent, preview=True)
        return 0

    return write_plan(intent, name=name)


def write_plan(intent: Plan, *, name: str) -> int:
    """Put `intent.content` on disk, atomically.

    Shared by both tools: they differ in how they decide *what* to write and not
    at all in how it is written.
    """

    data = intent.content.encode("utf-8")
    path = intent.path
    directory = os.path.dirname(path) or "."

    # A new file's missing parents are made, with the umask's modes like any
    # directory the user makes. The client has already checked the path against
    # the write surface, and a parent of a path inside it is inside it too —
    # except the surface root itself, which therefore exists already. A task's
    # run directory is made this way, by writing its record (D97).
    if intent.created:
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as error:
            print(f"{name}: {directory}: {error}", file=sys.stderr)
            return 1

    try:
        handle, temporary = tempfile.mkstemp(dir=directory, prefix=".neuman-")
    except OSError:
        # No temp file in that directory — usually because it is not writable,
        # which the direct write below reports properly.
        return _write_directly(path, data, intent, name=name)

    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # mkstemp gives 0600, which is not what a file the user asked for should
        # look like — but 0644 unconditionally was the wrong fix,
        # and on a shared filesystem it is a destructive one (N107).
        #
        # `os.replace` swaps in a new inode, so whatever mode the original had
        # is gone unless it is carried across deliberately. Two cases, and they
        # want different answers:
        #
        #   * **an existing file** keeps its own mode. An edit changes contents,
        #     never permissions — writing one line into a `0660` file in a
        #     group-writable share must not quietly leave it `0644`, which is a
        #     colleague locked out of a file they could edit a moment ago.
        #   * **a new file** takes `0666 & ~umask`, which is what a plain
        #     `open()` would have produced. Honouring the umask rather than
        #     naming a number is what lets the surrounding directory's
        #     convention win — a setgid share stays collaborative without the
        #     tool knowing it is a share.
        #
        # Stat'ed here rather than read from `intent.created`, which was decided
        # when the diff was computed and is a different instant. The mode has to
        # come from the file being replaced, now.
        try:
            os.chmod(temporary, os.stat(path).st_mode & 0o7777)
        except FileNotFoundError:
            umask = os.umask(0o022)
            os.umask(umask)
            os.chmod(temporary, 0o666 & ~umask)
        os.replace(temporary, path)
    except OSError as error:
        # The fallback goes FIRST, before any cleanup. The unlink below can be
        # denied by the same mode bits that denied the rename, so doing it first
        # is what once made this branch raise instead of falling back — every
        # write to a file the user may not rewrite failed with a traceback and
        # left its temp file behind (N96).
        if isinstance(error, PermissionError):
            result = _write_directly(path, data, intent, name=name)
            _discard(temporary)
            return result
        _discard(temporary)
        print(f"{name}: {path}: {error}", file=sys.stderr)
        return 1

    report(intent, preview=False)
    return 0


def _discard(temporary: str) -> None:
    """Remove a temp file if the filesystem permits it; never raise.

    Something that denies unlink cannot clean up after itself — a read-only
    directory, or a file the user may not remove — so the file is left rather
    than the dispatch dying. Best-effort cleanup must never mask the error it is
    cleaning up after (N96).

    When the unlink fails the file is still emptied, which is the part that
    matters (N109). Four of these were found in real project directories —
    `.neuman-cye8dqkz` and friends, left by a write whose rename failed — and a
    zero-byte file is a leftover someone can ignore where a 1.7 KB one looks
    like content they might need. Truncation is a write and is often permitted
    where removal is not.

    Emptying rather than renaming it something obvious: the name is already
    `.neuman-*` and already hidden, and a rename would need the same right that
    just failed.
    """

    try:
        os.unlink(temporary)
        return
    except OSError:
        pass

    try:
        with open(temporary, "wb"):
            pass
    except OSError:
        pass


def _write_directly(path: str, data: bytes, intent: Plan, *, name: str) -> int:
    """Without the temp-file dance, for a filesystem that denies rename."""

    try:
        with open(path, "wb") as stream:
            stream.write(data)
    except PermissionError:
        print(f"{name}: not permitted: {path}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"{name}: {path}: {error}", file=sys.stderr)
        return 1

    report(intent, preview=False)
    return 0
