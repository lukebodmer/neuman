"""Compaction when history outgrows the window.

Two moves, cheapest first.

**Mask** — a tool result spilled to a scratch file (`spill.py`) keeps its
pointer and loses the preview once it is older than the kept tail. No model
call, deterministic, and reversible: the file is still there to read. It is not
recorded as an event because it is a pure function of the history and the
window, so the next turn re-derives the same masking from the same log (makes compaction an event for the opposite reason).

**Compact** — summarize the oldest span when pressure remains, folding each
piece into a persistent *anchor* rather than writing a fresh summary. The head
is replaced by ``[pinned task, anchor, *tail]`` and the replacement is an event
the projection replays.

The anchor is why a long session does not lose its footing. A freeform summary
regenerated from the whole head drifts a little every time, and the fourth
generation has forgotten the request that started it; anchored iterative
summarization — merge the newly evicted span into a structured, standing
summary — measurably keeps paths, values, and decisions better than
reconstruction (Factory's evaluation; docs/harness.md). The task statement is
pinned verbatim *outside* the anchor, so the one thing that must never drift
cannot.

Nothing here edits a stored row. The log keeps the originals; only the
projection shortens.

Compaction's own invariant is the boundary. It *removes* messages and has to
choose where, so it cuts only where a history may legally begin — never between
an assistant's ``tool_calls`` and the replies it is owed. Three sessions died at
the provider inside one hour before it did (N116).

Each move returns :class:`ContextAction`s that the loop records. Compaction is
not deterministic and every occurrence is a real event, so each carries a unique
key per invocation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ...foundation.logging import get_logger
from .ports import ModelGateway, ReasoningControl
from .reasoning import summariser_fields
from .spill import elided_stub

_log = get_logger("neuman.server.harness.context")

__all__ = [
    "ContextAction",
    "ContextConfig",
    "ContextManager",
    "estimate_tokens",
    "measured_tokens",
]

# Characters per token, for the part of history no measurement covers.
#
# This was described as "deliberately an over-estimate", and it is not. Measured
# against this appliance's own tokenizer (llama.cpp `/tokenize`, Qwen3 27B):
#
#     English prose   361 real   459 estimated   1.27x over
#     Python source   480 real   424 estimated   0.88x under
#     A JSON blob     640 real   404 estimated   0.63x under
#     DICOM UIDs     1200 real   309 estimated   0.26x under
#
# `1.2.840.10008.5.1.4.1.1.88.59` is roughly one token per character, so a
# transcript full of paths and identifiers is under-counted fourfold — which is
# why a 25,396-token budget produced a 33,502-token request and compaction never
# fired (N91). No constant fixes that: the error is content-dependent and both
# directions occur.
#
# So this ratio is now used only for the *tail* — see `measured_tokens`.
CHARS_PER_TOKEN = 4

# What one image counts as, in characters, for the estimate.
#
# Not the length of its base64 payload. A data URI is a transport encoding,
# not content, and `len() // 4` over one invents hundreds of thousands of
# tokens: a single screenshot of 1.29M characters estimated to 322,795 tokens
# on a prompt the provider counted at 6,865, which pushed a 65k window to
# report itself full, clamped every reply to the 512-token floor, and then
# failed compaction because the summariser was handed the base64 verbatim
# (N148). Base64 tokenizes near one token per character, not one per four, so
# the error ran the other way from prose — the same content-dependent failure
# the `CHARS_PER_TOKEN` note above describes, one field over.
#
# A vision model is charged by a pixel budget, not by payload size, so a
# constant is both the only honest answer without a tokenizer and what `pi`
# does (`ESTIMATED_IMAGE_CHARS = 4800`, `packages/ai/src/utils/estimate.ts`).
ESTIMATED_IMAGE_CHARS = 4800

# A base64 image URL in a content-part array. Matched on the scheme so the
# redaction cannot catch ordinary text that happens to contain "image".
_IMAGE_URL_PREFIX = "data:image/"

# The two shapes an image takes, one per side of the provider seam: a reference
# in the derived history (`projection.IMAGE_REF`) and a `data:` URI in the
# request. Both cost one bounded constant, so the estimate does not depend on
# which side it is handed.
_IMAGE_REF = "image_ref"


def _redact_images(value: Any, count: list[int]) -> Any:
    """`value` with every image counted and its bytes taken out of the measure.

    A copy, not a mutation: the message is the live history. An image reference
    is emptied to `{}`; a base64 data URI is emptied to a string. Either way the
    bytes are not measured — a reference has none to measure, and a data URI's
    length is a transport encoding, not content (N148).
    """

    if isinstance(value, str):
        if value.startswith(_IMAGE_URL_PREFIX):
            count[0] += 1
            return ""
        return value
    if isinstance(value, dict):
        if value.get("type") == _IMAGE_REF:
            count[0] += 1
            return {}
        return {key: _redact_images(item, count) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_images(item, count) for item in value]
    return value


def _estimate_message_chars(message: dict[str, Any]) -> int:
    """`len(json.dumps(message))`, with each image counted, not measured.

    Byte-identical to the raw JSON length for a message with no image, so the
    estimator's behaviour on text is unchanged and only the one field that must
    not be measured is swapped.
    """

    count = [0]
    chars = len(json.dumps(_redact_images(message, count), sort_keys=True))
    return chars + count[0] * ESTIMATED_IMAGE_CHARS


def _render_content(content: Any) -> str:
    """A message's content as flat text, with image payloads named, not sent.

    `str(message["content"])` is what a multimodal user turn used to become:
    the Python repr of the content-part list, base64 and all. The summariser
    was therefore handed the picture it was supposed to summarise, and the
    request exceeded the very window it existed to free — 954,348 tokens
    rejected against a 65,536-token window (N148). An image is a fact the
    summary needs to record ("the model looked at the verification image"),
    not pixels it needs to read, so it becomes a word.
    """

    if not isinstance(content, list):
        return str(content or "")
    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            parts.append(str(part))
            continue
        if part.get("type") in ("image_url", _IMAGE_REF):
            parts.append("[image omitted]")
        elif part.get("text"):
            parts.append(str(part["text"]))
    return " ".join(parts)


def estimate_tokens(messages: Sequence[dict[str, Any]]) -> int:
    """Characters/4 over every message, with images bounded. Use
    `measured_tokens` where possible.

    Kept for the first turn of a session, when no reply has been received and
    there is therefore nothing measured to anchor to.
    """

    return sum(_estimate_message_chars(message) for message in messages) // CHARS_PER_TOKEN


def measured_tokens(
    messages: Sequence[dict[str, Any]], *, prompt_tokens: int | None, measured_upto: int
) -> int:
    """History size, anchored to what the provider actually counted.

    `prompt_tokens` is the provider's own count for the prompt it was last sent,
    and `measured_upto` is how many leading messages that number covered. So
    everything up to that point is *known*, and only the messages appended since
    are estimated — a handful rather than the whole transcript, which is what
    keeps `CHARS_PER_TOKEN`'s error from accumulating.

    The approach is `pi`'s (`packages/ai/src/utils/estimate.ts`), which solves
    this the same way and for the same reason: `usage.prompt_tokens` is in the
    OpenAI-compatible response every endpoint here returns, so this needs no
    tokenizer, no extra round trip, and works for a hosted provider and a local
    llama.cpp alike.

    Falls back to estimating everything when nothing has been measured yet, or
    when the count cannot describe the current prefix.
    """

    if prompt_tokens is None or prompt_tokens <= 0:
        return estimate_tokens(messages)

    # A measurement wider than the history it is meant to describe cannot be
    # trusted: compaction may have replaced the prefix it counted. Estimating is
    # the honest answer rather than carrying a number that describes messages
    # that are no longer there.
    if measured_upto <= 0 or measured_upto > len(messages):
        return estimate_tokens(messages)

    return prompt_tokens + estimate_tokens(messages[measured_upto:])


@dataclass(frozen=True, slots=True)
class ContextConfig:
    context_budget_tokens: int = 96_000
    # How much recent history survives a compaction, in tokens.
    #
    # Tokens rather than messages because a message count cannot bound a size:
    # `keep_recent_messages = 8` kept whatever eight messages happened to weigh,
    # which in the session that died was ~45,000 tokens of tool output against a
    # budget compaction existed to fit history inside (N116). The window is
    # measured in tokens, so the tail must be too.
    #
    # A floor, not the operative value: the loop derives this per turn from the
    # acting model's own window (D40), because one number cannot serve a 65k
    # local model and a 1M hosted one.
    keep_recent_tokens: int = 8_192

    # How many of the newest images stay in the request in full; older ones
    # become a placeholder that names the file to read again.
    #
    # A floor, for the same reason as `keep_recent_tokens`: the loop scales it
    # with the model's window. The unit is images rather than tokens because
    # an image's token cost is bounded and small (N148) — the cost that matters
    # is the base64 payload resent on every turn, and past the newest few the
    # model has stopped looking. Four is the floor a task genuinely comparing a
    # handful of frames still fits inside.
    keep_recent_images: int = 4


@dataclass(frozen=True, slots=True)
class ContextAction:
    """Something the manager did to the projected history."""

    kind: str  # compact | compact_failed
    key: str
    detail: dict[str, Any] = field(default_factory=dict)


def _key(kind: str, *parts: Any) -> str:
    """A stable identity for an action, so it is logged exactly once."""

    material = json.dumps([kind, *parts], sort_keys=True, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


# The first line of the anchor message, so a later compaction can find the
# summary it must merge into. A literal, not a role or a position: the anchor is
# a `system` turn that sits in the middle of history, where the projection
# replays it verbatim, and not every `system` turn is an anchor.
ANCHOR_PREFIX = "Conversation summary:\n"


def _is_anchor(message: dict[str, Any]) -> bool:
    """Whether this message is the standing summary, not ordinary history."""

    return message.get("role") == "system" and str(message.get("content") or "").startswith(
        ANCHOR_PREFIX
    )


def _anchor_text(message: dict[str, Any]) -> str:
    """The summary inside an anchor message, without its prefix."""

    return str(message.get("content") or "")[len(ANCHOR_PREFIX) :]


@dataclass
class ContextManager:
    """Applies compaction to a projected message list."""

    config: ContextConfig = field(default_factory=ContextConfig)

    async def manage(
        self,
        messages: list[dict[str, Any]],
        gateway: ModelGateway,
        *,
        budget_tokens: int | None = None,
        keep_tokens: int | None = None,
        prompt_tokens: int | None = None,
        measured_upto: int = 0,
        force: bool = False,
        model: str | None = None,
        endpoint: str | None = None,
        api_key_file: str | None = None,
        reasoning: ReasoningControl | None = None,
    ) -> tuple[list[dict[str, Any]], list[ContextAction]]:
        """Return history that fits, plus what had to be done to make it fit.

        `budget_tokens` is the window of the model this turn actually goes to,
        less the room its reply needs. It overrides the configured default,
        because the default cannot be right for every model: a catalog can hold
        a local 16k model and a hosted 200k one, and one number for both is
        wrong in whichever direction it is chosen. Passing None keeps the
        configured value, which is what an unregistered window falls back to.

        `keep_tokens` is how much recent history survives a compaction, derived
        per turn from the same window for the same reason (D40): a proportion of
        the model's own window with a floor, never a constant.

        This parameter is the fix for N86. The configured default was 96,000
        and nothing ever overrode it, so a 16,384-token model had its history
        trimmed against a ceiling six times too high: the manager reported that
        the turn fit, and the provider ended it.
        """

        actions: list[ContextAction] = []
        budget = (
            budget_tokens
            if budget_tokens and budget_tokens > 0
            else (self.config.context_budget_tokens)
        )

        # Cheapest move first: an old spilled result keeps its pointer and
        # loses the preview. This is not an LLM call and it is not recorded,
        # because it is a deterministic function of the history and the window —
        # the next turn re-derives exactly the same masking from the same log,
        # so there is nothing for an event to preserve (compaction is an
        # event for the opposite reason: summarization is not reproducible).
        messages, masked = self._mask_stale(
            messages, keep_tokens or self.config.keep_recent_tokens
        )

        # The fit check. After masking, the provider's last count describes a
        # prompt with previews that are no longer in it, so the estimator over
        # the masked history is the truer measure — and the only way it can err
        # is by over-counting, which compacts early rather than late.
        fits = (
            estimate_tokens(messages)
            if masked
            else measured_tokens(
                messages, prompt_tokens=prompt_tokens, measured_upto=measured_upto
            )
        ) <= budget

        # `force` skips the fit check and compacts regardless.
        #
        # Set when the provider has just refused the prompt, which means the
        # budget arithmetic said it fit and was wrong. Asking the same
        # arithmetic again would reach the same answer and retry the same
        # request, so the one thing that must not gate the retry is the
        # measurement that failed (N91).
        if not force and fits:
            return messages, actions

        messages = await self._compact(
            messages,
            gateway,
            actions,
            model=model,
            endpoint=endpoint,
            api_key_file=api_key_file,
            budget_tokens=budget,
            keep_tokens=keep_tokens,
            reasoning=reasoning,
        )
        return messages, actions

    # -- mask ----------------------------------------------------------------

    def _mask_stale(
        self, messages: list[dict[str, Any]], keep_tokens: int
    ) -> tuple[list[dict[str, Any]], bool]:
        """Replace old spilled tool previews with the path they point at.

        Tool output is most of what a long history weighs, and the largest
        results are already files the model can read (`spill.py`) — so the
        preview attached to the pointer is redundant the moment the result is
        old enough to fall outside the kept tail. Dropping it is free, needs no
        model, and cannot lose information: the pointer survives, so a model
        that needs the content reads it again.

        This is observation masking, and it is the cheap stage the research
        puts before summarization: JetBrains measured clearing stale tool
        output cutting cost ~52% while *improving* SWE-bench solve rate ~2.6%,
        against pure summarization that matched the cost and lengthened
        trajectories by up to 15% (docs/harness.md).

        The tail is left alone. A result from the last few turns is one the
        model is probably still reasoning about, and handing it a pointer it
        did not ask for spends a turn to save a few hundred tokens. Only
        results that already carry a spill note are masked, so nothing purely
        in-memory is ever dropped where it cannot be re-read.
        """

        if not messages:
            return messages, False

        # The earliest index whose tail still fits the kept window — the same
        # boundary `_cut_point` targets, so what masking drops is exactly what a
        # compaction would not have kept either. The newest message is always
        # protected, even when it alone outweighs the window: it is the result
        # the model is most likely still reading, and a `tool` reply cannot be
        # removed without its call, so masking it would spend a turn for nothing.
        edge = len(messages)
        kept = 0
        for index in range(len(messages) - 1, -1, -1):
            kept += estimate_tokens([messages[index]])
            if kept >= keep_tokens:
                break
            edge = index
        edge = min(edge, len(messages) - 1)

        masked = list(messages)
        changed = False
        for index in range(edge):
            message = masked[index]
            if message.get("role") != "tool":
                continue
            content = str(message.get("content") or "")
            stub = elided_stub(content)
            if stub is not None and stub != content:
                masked[index] = {**message, "content": stub}
                changed = True
        return masked, changed

    # -- compact -------------------------------------------------------------

    SUMMARY_PROMPT = (
        "You maintain a running summary of a long conversation between a user and "
        "an assistant. You are given the existing summary (possibly empty) and a "
        "block of newly evicted messages. Output ONLY the updated summary — do "
        "not continue the conversation and do not answer anything in it.\n\n"
        "Keep every heading below, in this order, and omit a heading's body only "
        "when it is genuinely empty:\n\n"
        "## Task\n"
        "The user's original request, as close to verbatim as you can.\n\n"
        "## Established facts\n"
        "Facts confirmed against the data, with the exact values, file paths, "
        "identifiers, and numbers they were confirmed with. Do not round or "
        "paraphrase a value.\n\n"
        "## Decisions\n"
        "What was decided and why, so the reasoning survives.\n\n"
        "## Artifacts\n"
        "Files created or changed, with their full paths.\n\n"
        "## Ruled out\n"
        "Approaches tried and rejected, and why — so they are not tried again.\n\n"
        "## Next step\n"
        "The immediate next action and any open question.\n\n"
        "Carry every established fact from the existing summary forward unless a "
        "newly evicted message contradicts it. Be specific: exact paths, numbers, "
        "and identifiers rather than descriptions of them."
    )

    # The most of one tool result the summariser is shown.
    #
    # Tool output is most of what a long history weighs, and the history being
    # compacted is about as large as the window it must fit inside — so sending
    # results verbatim makes the summarisation request the same size as the
    # problem it exists to solve. `pi` truncates to the same 2,000 characters
    # (`serializeConversation`), which is ample for "what did this return"
    # without being the return value.
    SUMMARY_RESULT_CHARS = 2_000

    # What one summarisation call may be asked to read, as a fraction of the
    # turn's own prompt budget. Well under it, because the summariser's reply
    # also has to fit and the budget it is given was computed for a different
    # request.
    SUMMARY_INPUT_SHARE = 0.8

    # The most a summary may itself become, including whatever the model thinks
    # before writing it.
    #
    # Generous on purpose. A reasoning model spends its budget on reasoning
    # first: measured against this appliance's qwen, a two-sentence summary took
    # 104 reasoning chunks before the first content chunk, so a 128-token cap
    # produced a `length` stop with an *empty* summary — the request succeeded
    # and returned nothing. Found by running it against the real model, which is
    # the half a fake gateway cannot test (N94).
    SUMMARY_REPLY_TOKENS = 2048

    async def _summarise(
        self,
        head: list[dict[str, Any]],
        gateway: ModelGateway,
        *,
        anchor: str = "",
        model: str | None,
        endpoint: str | None,
        api_key_file: str | None,
        budget_tokens: int | None,
        reasoning: ReasoningControl | None = None,
    ) -> str:
        """Fold `head` into `anchor`, in pieces when it is too large to send at
        once.

        The history being compacted is, by construction, about as large as the
        window it must be made to fit inside — so sending it as one message
        asks the model to read 68k tokens in order to free 68k tokens, and the
        summarisation request overflows exactly as the turn did.

        Each piece is merged into the running summary in turn rather than the
        pieces being summarized independently and folded afterwards. The two
        differ in what the model is asked to preserve: an independent piece has
        only its own text, so a fact stated in an earlier piece exists nowhere
        when a later one is written; a running anchor carries every established
        fact forward into the next call, so nothing is dropped between pieces
        except by an explicit contradiction. It is the anchored iterative
        pattern applied *within* one compaction, at the same call count.
        """

        limit = (
            int(budget_tokens * self.SUMMARY_INPUT_SHARE)
            if budget_tokens and budget_tokens > 0
            else self.config.context_budget_tokens
        )

        current = anchor
        merged_anything = False
        for chunk in self._chunk(head, limit):
            merged = await self._summarise_once(
                self._serialise(chunk),
                current,
                gateway,
                model=model,
                endpoint=endpoint,
                api_key_file=api_key_file,
                reasoning=reasoning,
            )
            if merged:
                current = merged
                merged_anything = True
            # An empty reply leaves the anchor as it was and the next chunk
            # merges into that. A single provider hiccup on one piece must not
            # cost the whole history, which is the turn-ending failure the old
            # piecewise fold also avoided — but if *every* call came back empty
            # the summariser is broken, and returning what was merged so far
            # would present a partial anchor as complete. That is the silent
            # loss this module exists to prevent, so it fails by name instead.
        return current if merged_anything else ""

    def _serialise(self, messages: list[dict[str, Any]]) -> str:
        """Render `messages` as flat text for the summariser.

        Three differences from handing over the raw JSON, each with a cost
        attached:

        * **Tool results are truncated** to `SUMMARY_RESULT_CHARS`. Summarising
          does not need a file's contents, only that it was read.
        * **Reasoning is dropped entirely.** It is the largest field on a
          reasoning model's reply and the least useful here — the visible text,
          the calls and the results already record what happened. `pi` notes
          that replaying it "can make the summarization request larger than the
          context it summarizes".
        * **It is prose, not a message array.** A JSON array of messages is a
          conversation, and a model handed one tends to answer the last turn
          rather than describe the whole. The system prompt says so too; this
          removes the temptation rather than relying on the instruction.
        """

        parts: list[str] = []
        for message in messages:
            role = message.get("role")
            content = _render_content(message.get("content"))
            if role == "system":
                if content:
                    parts.append(f"[Earlier summary]: {content}")
            elif role == "user":
                if content:
                    parts.append(f"[User]: {content}")
            elif role == "assistant":
                if content:
                    parts.append(f"[Assistant]: {content}")
                calls = [
                    f"{c.get('function', {}).get('name')}"
                    f"({c.get('function', {}).get('arguments')})"
                    for c in message.get("tool_calls") or []
                ]
                if calls:
                    parts.append("[Assistant tool calls]: " + "; ".join(calls))
            elif role == "tool":
                if not content:
                    continue
                if len(content) > self.SUMMARY_RESULT_CHARS:
                    dropped = len(content) - self.SUMMARY_RESULT_CHARS
                    content = (
                        content[: self.SUMMARY_RESULT_CHARS]
                        + f"\n[… {dropped} more characters truncated]"
                    )
                parts.append(f"[Tool result]: {content}")
        return "\n\n".join(parts)

    def _merge_input(self, anchor: str, payload: str) -> str:
        """The one user turn the summariser reads: standing state, then news.

        The anchor goes first and is labelled, because the two blocks ask for
        different treatment — facts in the anchor are carried forward, and only
        an explicit contradiction in the new messages may drop one.
        """

        if not anchor:
            return f"Newly evicted messages:\n\n{payload}"
        return (
            "Existing summary — carry every fact below forward unless the new "
            "messages contradict it:\n\n"
            f"{anchor}\n\n"
            f"Newly evicted messages:\n\n{payload}"
        )

    async def _summarise_once(
        self,
        payload: str,
        anchor: str,
        gateway: ModelGateway,
        *,
        model: str | None,
        endpoint: str | None,
        api_key_file: str | None = None,
        reasoning: ReasoningControl | None = None,
    ) -> str:
        """One summarisation call. Returns "" when it fails.

        Failure is swallowed deliberately *here* and reported by the caller: a
        provider refusing to summarise is a reason the turn cannot continue,
        not a traceback out of the context manager — and the previous version
        let a 400 propagate as a turn-ending model error that named the wrong
        cause.
        """

        request: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": self.SUMMARY_PROMPT},
                {"role": "user", "content": self._merge_input(anchor, payload)},
            ],
            "stream": True,
            "max_tokens": self.SUMMARY_REPLY_TOKENS,
        }
        # Cheap reasoning, in the *turn's own dialect*. Summarising is not a
        # reasoning task and a reasoning model spends most of its budget on
        # thinking before the answer, so the call asks for the model's cheapest
        # live level rather than the model's default (N94 measured 64.1s of
        # reasoning against 3.0s once it was cut down). Hardcoding
        # `enable_thinking: false` was the `toggle` shape, and on a model whose
        # catalog entry declares `effort` it did not turn thinking off — it made
        # the model continue the transcript it was asked to summarise, so the
        # anchor became an echo of the head (`summariser_fields`).
        request.update(summariser_fields(reasoning))
        # The turn's own model, which is the whole fix. Omitted only when the
        # caller has none to give, which is the test fakes' case.
        if model:
            request["model"] = model

        pieces: list[str] = []
        thought_parts: list[str] = []
        try:
            async for chunk in gateway.stream(
                request, endpoint=endpoint, api_key_file=api_key_file
            ):
                if chunk.kind == "content":
                    pieces.append(str(chunk.data.get("text", "")))
                elif chunk.kind == "reasoning":
                    thought_parts.append(str(chunk.data.get("text", "")))
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            _log.warning("compaction failed", fields={"error": str(exc)})
            return ""

        summary = "".join(pieces).strip()
        if summary:
            return summary

        # Reasoning only. A model that spent its whole budget thinking has still
        # produced a description of the conversation, and a rough summary beats
        # discarding the history or ending the turn. Trimmed, because reasoning
        # is longer and looser than the answer would have been.
        thought = "".join(thought_parts).strip()
        if thought:
            _log.info(
                "compaction fell back to the model's reasoning",
                fields={"chars": len(thought)},
            )
            return thought[-4000:]

        return ""

    def _chunk(self, messages: list[dict[str, Any]], limit: int) -> list[list[dict[str, Any]]]:
        """Split `messages` into runs that each estimate under `limit` tokens.

        A single message larger than the limit becomes its own chunk rather
        than being split: cutting a message in half produces two invalid
        fragments, and the summariser reading one oversized message is a
        provider error with a clear cause rather than a corrupted history.

        Sized against what `_serialise` will actually send rather than against
        the raw message. They are no longer the same: a 200 KB tool result
        reaches the summariser as 2 KB, and measuring the original would split
        the history into chunks sized for content nobody is sent — more model
        calls than the work needs, each one a round trip.
        """

        chunks: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        size = 0
        for message in messages:
            cost = len(self._serialise([message])) // CHARS_PER_TOKEN
            if current and size + cost > limit:
                chunks.append(current)
                current, size = [], 0
            current.append(message)
            size += cost
        if current:
            chunks.append(current)
        return chunks

    def _cut_point(self, messages: list[dict[str, Any]], keep_tokens: int) -> int:
        """The index to start keeping from: inside `keep_tokens`, and legal.

        Two rules, and the second is the one that was missing. Walk backwards
        accumulating estimates until `keep_tokens` is reached — that is the
        *target*. Then move the boundary **earlier** until it sits where the
        protocol allows a history to begin.

        A boundary is legal when the tail it opens contains no `tool` message
        whose calling assistant was left behind. Both ways of getting that wrong
        ended a session (N116):

        * cutting between an assistant's `tool_calls` and its reply — "Messages
          with role 'tool' must be a response to a preceding message with
          'tool_calls'";
        * cutting *inside* one assistant's fan-out, keeping the call and
          dropping some replies — "An assistant message with 'tool_calls' must
          be followed by tool messages responding to each 'tool_call_id'".

        Both are the same defect, so both have the same cure: a cut may only
        land on a message that opens a turn — anything that is not a `tool`
        reply — and never between an assistant and the replies it is owed. The
        boundary only ever moves earlier, so the tail grows slightly rather than
        shrinking, which is the safe direction: a little less freed, never an
        invalid request. Taken from `pi`'s `findValidCutPoints`
        (`packages/coding-agent/src/core/compaction/compaction.ts`), whose
        `isCutPointMessage` refuses a tool result outright.

        Returns 0 when no legal boundary frees anything, which the caller reads
        as "nothing to compact" rather than cutting anyway.
        """

        if not messages:
            return 0

        # The target: the earliest index whose tail still fits `keep_tokens`.
        target = len(messages)
        kept = 0
        for index in range(len(messages) - 1, -1, -1):
            kept += estimate_tokens([messages[index]])
            if kept >= keep_tokens:
                break
            target = index

        # Then earlier, to a message that may legally begin a history. A `tool`
        # may not: it answers something, and whatever it answers is behind the
        # cut. An assistant *offering* calls is fine — its replies follow it and
        # are kept with it.
        cut = min(target, len(messages) - 1)
        while cut > 0 and messages[cut].get("role") == "tool":
            cut -= 1

        # A compaction that keeps everything has freed nothing, and one that
        # keeps nothing has no tail to anchor the summary to.
        if cut <= 0:
            return 0
        return cut

    def _split_pinned(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """The task statement, held out of anything the summariser may rewrite.

        The first user turn is the request that started the session, and it is
        the one message whose exact wording the whole turn depends on. A
        summary may restate it, but a restatement is exactly the drift that
        left one session "explaining the frame convention" long after it had
        been asked for a coordinate report. Keeping it verbatim costs one
        message and makes the drift impossible, so the anchor carries a
        `## Task` section only as a convenience for a summary read on its own.

        The order in the replacement is ``[anchor, task, tail]`` rather than
        ``[task, anchor, tail]``, and that is a protocol constraint rather than a
        preference: the anchor is a `system` message, and a provider whose chat
        template insists system messages come first — llama.cpp's Qwen template
        raises *"System message must be at the beginning"* — refuses one that
        follows a user turn. The request already leads with the system prompt,
        so the anchor joins the leading run and the task follows it.
        """

        # The task is the first user turn. On a fresh session it is at the
        # front; once an anchor exists it sits immediately after it. Removing it
        # from wherever it is keeps the body summarizable without losing the one
        # message whose wording matters.
        for index, message in enumerate(messages):
            if message.get("role") == "user":
                body = [*messages[:index], *messages[index + 1 :]]
                return [dict(message)], body
        return [], list(messages)

    def _existing_anchor(self, messages: list[dict[str, Any]]) -> str:
        """The summary to merge into, or empty on a first compaction."""

        for message in messages:
            if _is_anchor(message):
                return _anchor_text(message)
        return ""

    async def _compact(
        self,
        messages: list[dict[str, Any]],
        gateway: ModelGateway,
        actions: list[ContextAction],
        *,
        model: str | None = None,
        endpoint: str | None = None,
        api_key_file: str | None = None,
        budget_tokens: int | None = None,
        keep_tokens: int | None = None,
        reasoning: ReasoningControl | None = None,
    ) -> list[dict[str, Any]]:
        """Replace the head of the history with a summary of it.

        `model` and `endpoint` are the turn's own, and their absence was the
        bug: the request carried neither, so `gateway.stream` fell back to the
        configured default endpoint. On an appliance whose default is a hosted
        provider, every compaction went there — naming no model, so it was
        refused with a 400 and the turn died of an overflow that compaction was
        supposed to have fixed. Compaction had therefore never once succeeded
        (N94).

        `api_key_file` travels with them, and for the same reason. Compaction
        is a real request to the turn's own provider, so sending it with
        another provider's credential is the N94 shape again one layer down:
        the right endpoint, authenticated as somebody else, refused.

        `budget_tokens` is what the summariser's own prompt must fit inside. A
        history too large for the window is summarised in pieces, because the
        thing being compacted is by definition about as large as the window it
        has to fit into — sending it whole asks the model to read 68k tokens to
        free 68k tokens.

        `keep_tokens` is how much recent history survives, derived per turn from
        the acting model's window (D40). The boundary it asks for is a target
        rather than a ceiling: `_cut_point` moves it earlier to land somewhere
        the protocol allows.
        """

        pinned, body = self._split_pinned(messages)
        keep = keep_tokens or self.config.keep_recent_tokens
        cut = self._cut_point(body, keep)

        # The anchor is part of the head on every compaction, so the new summary
        # merges into the standing one instead of replacing it. Cutting before
        # it would leave two summaries in the history and the older one would be
        # the one the model still believed; the cut therefore never lands ahead
        # of it. `_cut_point` only moves earlier, so taking the later of the two
        # is the direction that keeps the anchor where the merge needs it.
        anchor_index = next(
            (index for index, message in enumerate(body) if _is_anchor(message)), -1
        )
        if anchor_index >= 0:
            cut = max(cut, anchor_index + 1)
        if cut <= 0:
            return messages

        head, tail = body[:cut], body[cut:]
        fresh = [message for message in head if not _is_anchor(message)]
        if not fresh:
            # Only the anchor is in the head: it would be summarised into
            # itself, at the cost of a model call, changing nothing.
            return messages

        summary = await self._summarise(
            fresh,
            gateway,
            anchor=self._existing_anchor(head),
            model=model,
            endpoint=endpoint,
            api_key_file=api_key_file,
            budget_tokens=budget_tokens,
            reasoning=reasoning,
        )
        if not summary:
            # Nothing was freed, so nothing is claimed — and it is recorded as
            # its own action rather than left as an absence. "No summary" and
            # "nothing needed summarising" are different outcomes, and a caller
            # that inferred failure from a missing `compact` action would read a
            # short history as a broken summariser.
            actions.append(
                ContextAction(
                    "compact_failed",
                    _key("compact_failed", len(head)),
                    {"messages": len(head)},
                )
            )
            return messages

        result = [
            {"role": "system", "content": f"{ANCHOR_PREFIX}{summary}"},
            *pinned,
            *tail,
        ]
        actions.append(
            ContextAction(
                "compact",
                # Compaction is not deterministic and every occurrence is a real
                # event, so its key is unique per invocation.
                _key("compact", len(head), summary[:64]),
                # The replacement list is recorded so projection reproduces
                # exactly what the next request saw. `summarized_messages`
                # counts what was folded *this* time, not the anchor, so the
                # client's note describes the move that happened.
                {"summarized_messages": len(fresh), "messages": result},
            )
        )
        return result
