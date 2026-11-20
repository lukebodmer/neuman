"""The note that stands in for tool output too large to hold in a message.

A result too large to stream is written to a file where it is produced — in the
client, as the acting user — and the model is handed a sentence naming the path
rather than the bytes. `loop.py` writes that sentence; `context.py` recognises it
to strip the preview it is attached to once the result is old enough that
re-reading the file is the cheaper move. One definition of the string, so the
writer and the reader cannot drift — the same reason `projection.py` is the only
place messages are shaped.

The marker is a literal because a `tool` message has no room for a field: a
provider parses `content` and nothing else, so "this is a pointer, not data"
has to be legible in the text both to the masking code and to the model.
"""

from __future__ import annotations

__all__ = ["PERSISTED_MARKER", "elided_stub", "persisted_note"]

# The opening words of `persisted_note`, which is what `elided_stub` searches
# for. Kept as a prefix of the note rather than a separate tag so the text the
# model reads and the text the masker recognises are the same text.
PERSISTED_MARKER = "[output too large to show; the full output is in "


def persisted_note(path: str, truncated: bool) -> str:
    """Name a scratch file the model can read, in place of the elided middle.

    Points at `fs_read` and `fs_search` explicitly: the file is an ordinary
    path, and a model that has just been handed one should not have to guess
    that its usual reading tools reach it.
    """

    note = f"{PERSISTED_MARKER}{path} — read it with fs_read or search it with fs_search]"
    if truncated:
        note = f"{note} [and was itself cut at the tool's size limit]"
    return note


def elided_stub(content: str) -> str | None:
    """The persisted note inside `content`, or None when there is not one.

    A spilled result's `content` is a preview followed by one note per stream
    that spilled, so the note runs to the end of the string. Returning the tail
    from the marker keeps both notes and drops the preview, which is the part
    that is re-readable and therefore redundant.
    """

    index = content.find(PERSISTED_MARKER)
    if index < 0:
        return None
    return content[index:].strip()
