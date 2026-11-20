"""The append-only session event log.

**Model-visible means logged.** Every request the harness sends and every output
a model or tool produces is one row, verbatim except for secret redaction, and
model history is *derived* from these rows by `projection.py` — never held as an
in-memory list that can drift from the log. That is what makes replay, resume,
export, and live policy changes all read one representation.

Three write-time rules:

* **Verbatim.** Nothing is normalized except secrets and NULs — and both
  exceptions record themselves, so a row that was altered says so.
* **Exact-leaf redaction.** A JSON leaf string that *equals* a known secret is
  replaced and the JSON pointer recorded. Substring matching is never used: it
  over-redacts short values and gives false confidence about long ones.
* **NUL substitution**, because `jsonb` is text underneath and Postgres refuses
  `\\u0000` outright. This is a storage limit rather than a policy choice, and
  before it was handled the refusal escaped as a failed turn whose `tool_result`
  was never written at all (N105) — a tool that provably ran with no recorded
  outcome. See :func:`sanitize`.
"""

from __future__ import annotations

from typing import Any

from .ports import EventRecord, EventRepository

__all__ = [
    "KINDS",
    "MIN_SECRET_LENGTH",
    "NUL",
    "NUL_PLACEHOLDER",
    "append",
    "redact",
    "requests",
    "sanitize",
]

KINDS = frozenset(
    {
        # The user's own input. The spec's table omitted it, but the log has to
        # replay the conversation.
        "user_message",
        "request",
        "response_chunk",
        "response_final",
        "tool_proposal",
        "tool_decision",
        "tool_result",
        # Compaction, budget exhaustion, turn boundaries, interrupts.
        "harness_event",
    }
)

# Below this length, equality against a "secret" matches too much ordinary text.
MIN_SECRET_LENGTH = 8


def redact(
    value: Any,
    secrets: dict[str, str],
    *,
    min_length: int = MIN_SECRET_LENGTH,
) -> tuple[Any, list[dict[str, str]]]:
    """Return ``(redacted_value, pointers)``.

    ``secrets`` maps a name to its value; only exact leaf-string equality counts.
    """

    pointers: list[dict[str, str]] = []
    usable = {name: secret for name, secret in secrets.items() if len(secret) >= min_length}
    if not usable:
        return value, pointers

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, dict):
            return {key: walk(item, f"{path}/{key}") for key, item in node.items()}
        if isinstance(node, list):
            return [walk(item, f"{path}/{index}") for index, item in enumerate(node)]
        if isinstance(node, str):
            for name, secret in usable.items():
                if node == secret:
                    pointers.append({"path": path or "/", "reason": f"secret:{name}"})
                    return f"<redacted:{name}>"
        return node

    return walk(value, ""), pointers


# The characters JSONB cannot hold, mapped to a visible stand-in.
#
# Only the NUL character. Postgres accepts every other code point in a `jsonb` string;
# NUL is rejected outright, because `jsonb` is stored as text and NUL cannot be
# converted to it. So this is not a general escaping pass and must not grow into
# one: every character not listed here reaches the log verbatim.
NUL = "\x00"
NUL_PLACEHOLDER = "�"


def sanitize(value: Any) -> tuple[Any, list[dict[str, str]]]:
    """Return ``(storable_value, pointers)`` with NULs replaced.

    The second exception to "verbatim" (``redact`` is the first), and unlike
    that one it is not a policy choice — it is a storage limit. `jsonb` is text
    underneath, and Postgres refuses `\\u0000` with *"unsupported Unicode escape
    sequence: \\u0000 cannot be converted to text"*. A payload carrying one
    therefore cannot be stored at all.

    Which was not a theoretical problem (N105). A `shell` dispatch ran Blender
    with a probe script that called ``save_render("/dev/stdout")``, so a PNG came
    back on stdout. The client decodes tool output with
    ``errors="replace"``, which repairs invalid UTF-8 and leaves NUL
    untouched, so the byte reached this
    layer intact, the insert raised, and the exception escaped the turn — which
    the loop's catch-all reported as `model_error`. The cost was not a lost line
    of transcript: the write that failed *was* the `tool_result`, so a tool that
    had been proposed, approved and run had no recorded outcome. That is a hole
    in the governance record, not a formatting defect, which is why this is
    fixed here rather than at the one decode that happened to produce it.

    Replacing rather than refusing, because the alternative is worse in both
    directions: dropping the event loses the record of a tool that really ran,
    and failing the append is the behaviour being fixed. `\\ufffd` is the stand-in
    for the same reason the decode uses it — it is already what the surrounding
    bytes of mangled binary look like.

    Every substitution is reported as a pointer, so the row says it was altered
    and where. A sanitized payload is no longer verbatim, and a log that quietly
    rewrote its own contents would be worse than one that crashed.
    """

    pointers: list[dict[str, str]] = []

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, dict):
            return {key: walk(item, f"{path}/{key}") for key, item in node.items()}
        if isinstance(node, list):
            return [walk(item, f"{path}/{index}") for index, item in enumerate(node)]
        if isinstance(node, str) and NUL in node:
            pointers.append(
                {
                    "path": path or "/",
                    "reason": f"sanitized:nul:{node.count(NUL)}",
                }
            )
            return node.replace(NUL, NUL_PLACEHOLDER)
        # Dict *keys* are walked as values above but not themselves checked: a
        # NUL in a key would break the same insert. No code path produces one —
        # keys here are literals from the harness — so this notes the gap rather
        # than carrying a traversal nothing exercises.
        return node

    return walk(value, ""), pointers


async def append(
    repository: EventRepository,
    *,
    branch_id: str,
    kind: str,
    payload: dict[str, Any],
    message_id: str | None = None,
    secrets: dict[str, str] | None = None,
) -> EventRecord:
    """Append one event to a branch's log."""

    if kind not in KINDS:
        raise ValueError(f"unknown session event kind: {kind!r}")

    stored, pointers = redact(payload, secrets or {})
    # After redaction, not before: a secret is matched by exact equality, and a
    # value with a NUL replaced would no longer equal the secret it came from.
    # Sanitizing first would therefore let a credential through unredacted.
    stored, nul_pointers = sanitize(stored)
    return await repository.append(
        branch_id=branch_id,
        kind=kind,
        payload=stored,
        message_id=message_id,
        redactions=(pointers + nul_pointers) or None,
    )


def requests(events: list[EventRecord]) -> list[dict[str, Any]]:
    """The exact request bodies the harness sent, in order.

    The audit answer to "what did the model actually see?" — reconstructed
    history is a projection and can change as projection changes; these are the
    bytes that went out.
    """

    return [event.payload for event in events if event.kind == "request"]
