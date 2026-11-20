"""Turning a chosen reasoning level into the fields one provider understands.

The whole of Neuman's knowledge of reasoning dialects lives here. There are
four, they do not agree on a field, and which one a model speaks is a fact about the
model rather than something its id carries — the same shape as `vision` and
`context_tokens`, and stated by the operator for the same reason.

Verified against each provider's own documentation rather than recalled:

* **`effort`** — a flat ``reasoning_effort`` string. llama.cpp's server takes
  this and passes the value to the model's jinja template; ``none`` disables
  reasoning. The OpenAI-compatible providers take the same field.
* **`budget`** — ``thinking: {"type": "enabled"}`` *alongside*
  ``reasoning_effort``. DeepSeek's shape: the envelope turns reasoning on and
  the string sets its depth. Named for the envelope rather than a token count,
  because the token-budget form it is named after (Anthropic's
  ``budget_tokens``) is removed on current models and returns a 400. The two
  halves are independent controls, which is why ``off`` is the disabled
  envelope rather than an effort string: DeepSeek's OpenAI-format
  ``reasoning_effort`` takes ``low``/``high``/``max`` and has no off value
  (``none`` is the *Anthropic*-format spelling, a different field on a
  different shape).
* **`toggle`** — ``chat_template_kwargs: {"enable_thinking": bool}``, which is
  how a template that understands nothing finer exposes it. The narrow case:
  prefer ``effort`` wherever the runtime accepts it.
* **`preserved`** — the flat string ``effort`` sends, with ``budget``'s
  ``reasoning_content`` on every replayed assistant turn. Kimi K3.1 Pro's shape:
  Moonshot's thinking-models guide says reasoning is always on, that ``thinking``
  should not be passed, and that the complete assistant message is required back
  in tool loops. There is no off level to declare (D88).

Why this is not normalised into one field: each dialect would have to be lied
about by the others' shape. A provider that does not recognise a key it is sent either ignores
it or refuses the turn, and which of those happens is not ours to choose.

A dialect also says what a *replayed* assistant turn must carry, which is
`replay_fields`. The two are one fact seen from both ends, so they live together rather than in the projection, which has no business knowing
which provider it is talking to.
"""

from __future__ import annotations

from typing import Any

from .ports import ReasoningControl

__all__ = [
    "UnknownLevel",
    "reasoning_fields",
    "replay_fields",
    "resolve_level",
    "resumes_thought_as_context",
    "summariser_fields",
]

# The strings `toggle` understands. Two values, because a template with no
# finer control has nothing else to say.
#
# `_TOGGLE_OFF` is shared with `budget`, which has depths above it but no way
# to spell "none" in its effort string. One spelling of off across the dialects
# is deliberate: the client draws a level from a list and the operator writes
# one in a preset, and neither should have to know which dialect is underneath
# to say the same thing.
_TOGGLE_ON = "on"
_TOGGLE_OFF = "off"


class UnknownLevel(ValueError):
    """A level the model does not declare.

    Refused by name rather than forwarded. A provider's own error for an
    unrecognised effort string arrives mid-turn, after the prompt is sent and
    the tokens are spent, in the provider's dialect — which is the shape of
    every bug `vision` and `context_tokens` exist to prevent.
    """


def resolve_level(control: ReasoningControl | None, chosen: str | None) -> str | None:
    """The level this turn runs at, or None when the model is not asked.

    `chosen` is the client's pick. None means "whatever the model declares",
    which is the ordinary case: a user who has expressed no preference should
    get the operator's.
    """

    if control is None:
        # The model declares no control. A client that asked for one anyway is
        # refused rather than ignored — silently dropping it would make a
        # control that does nothing, which the `contextSize` docstring names as
        # worse than one that refuses.
        if chosen:
            raise UnknownLevel("this model does not take a reasoning level")
        return None

    level = chosen or control.default
    if not level:
        return None

    if control.levels and level not in control.levels:
        raise UnknownLevel(
            f"{level!r} is not a level this model declares: {', '.join(control.levels)}"
        )
    return level


def reasoning_fields(control: ReasoningControl | None, level: str | None) -> dict[str, Any]:
    """The request fields that ask for `level`, in this provider's dialect.

    Empty when the model is not asked to reason, which merges into a request
    as nothing at all — the model's own default stands.
    """

    if control is None or not level:
        return {}

    if control.control in ("effort", "preserved"):
        return {"reasoning_effort": level}

    if control.control == "budget":
        if level == _TOGGLE_OFF:
            # The envelope is the switch, and it is the *only* switch: this
            # dialect's effort string has no off value, so `off` is the
            # disabled envelope and no string at all. Sending nothing instead
            # would not be off — DeepSeek documents thinking as on by default
            # at `high`, so silence asks for reasoning rather than declining
            # it, and a model declaring `off` must be able to mean it.
            return {"thinking": {"type": "disabled"}}

        # Both fields. The envelope alone enables reasoning at the provider's
        # default depth; the string alone is ignored by a provider that gates
        # on the envelope. Sending one without the other is the failure this
        # dialect exists to name.
        return {"thinking": {"type": "enabled"}, "reasoning_effort": level}

    if control.control == "toggle":
        # A chat-template argument rather than a sampling parameter, which is
        # how Qwen3 exposes it. An endpoint without the concept drops the key.
        return {"chat_template_kwargs": {"enable_thinking": level == _TOGGLE_ON}}

    # An unrecognised control. The schema constrains this to the three above
    # and the module asserts it, so reaching here means the baseline came from
    # somewhere else — send nothing rather than guess a dialect.
    return {}


# The level names that mean "do not think". `none` is the `effort` dialect's
# off switch and `off` is the shared spelling for `budget` and `toggle` (see
# `reasoning_fields`). A summariser wants the cheapest *live* level, never an
# off one.
_OFF_LEVELS = frozenset({"none", "off"})


def summariser_fields(control: ReasoningControl | None) -> dict[str, Any]:
    """The request fields that keep a summarisation call cheap, in this dialect.

    Not `reasoning_fields(control, "off")`. Disabling reasoning on a model that
    thinks in order to answer does not make it answer briefly; it makes it
    answer badly, or — measured against this appliance's Qwen3 through the
    provider's own endpoint — continue the transcript it was asked to summarise.
    Across the same request, `reasoning_effort: "none"` returned 317 bytes of
    transcript continuation, `"low"` returned a correct structured summary
    after 1,459 bytes of reasoning, and sending no level at all spent 9,685
    bytes of reasoning on a degraded one. The cheap setting that still works is
    the lowest declared level that is not an off switch, so that is what the
    summariser asks for.

    This also fixes a bug that existed only because the dialect was hardcoded:
    the request always sent `chat_template_kwargs: {"enable_thinking": false}`,
    which is the `toggle` shape. A model whose catalog entry declares `effort`
    either ignored it or honoured it as off, and compaction then replaced the
    head with an echo of itself.
    """

    if control is None:
        return {}

    if control.control == "toggle":
        # A template with no finer control has nothing between on and off, so
        # off is the cheapest it can be asked for.
        return {"chat_template_kwargs": {"enable_thinking": False}}

    levels = [level for level in control.levels or () if level not in _OFF_LEVELS]
    # `low` where it is declared, because it is the name the graded dialects use
    # for their lightest setting; otherwise the first declared live level, which
    # the catalogs order cheapest-first.
    chosen = "low" if "low" in levels else (levels[0] if levels else None)

    if control.control == "budget":
        # The envelope is the switch and the string is the depth, and both are
        # needed for a provider that gates on the envelope (see `reasoning_fields`).
        if chosen is None:
            return {"thinking": {"type": "disabled"}}
        return {"thinking": {"type": "enabled"}, "reasoning_effort": chosen}

    if control.control in ("effort", "preserved"):
        return {"reasoning_effort": chosen} if chosen else {}

    # An unrecognised control: the schema constrains this to the ones the
    # dialects define, so send nothing rather than guess.
    return {}


def replay_fields(control: ReasoningControl | None, reasoning: str) -> dict[str, Any]:
    """What a replayed assistant turn must carry back, in this dialect.

    `reasoning` is what the model actually thought, or empty for a turn that
    thought nothing. Both cases matter to `budget`, and for different reasons.

    A model that thinks does not always *finish*. When a reply stops at
    `max_tokens` the loop continues it (N90), and continuing means resubmitting
    the assistant turn mid-thought — so the thinking has to go back with it. A
    turn that spent its whole allowance reasoning and emitted no visible content
    replayed as `{"role": "assistant", "content": ""}`, and DeepSeek answered
    *"The `reasoning_content` in the thinking mode must be passed back to the
    API"* (N115).

    The empty case is the same rule, not a special one: `budget` wants the field
    *present* on every assistant turn while thinking is on, so a turn that did
    no thinking carries an empty string rather than nothing. Omitting it there
    would fix the truncation and leave the identical 400 waiting on the next
    history containing an ordinary tool call.

    Keyed on the dialect rather than on a provider name. DeepSeek is the model
    that proved it, but the requirement belongs to the shape — anything an
    operator declares as `budget` is asking for thinking the same way, and a
    check against an id would have to be rewritten for the second such model.
    `preserved` is that second model, and it wants the same field for the same
    turns without the outbound envelope: the replay half is why it is a dialect
    of its own rather than `effort` (D88).

    Keyed on the dialect rather than on the *turn's level*, too, which is the
    narrower choice and the deliberate one. N115's wording is "in the thinking
    mode", so a turn sent with the disabled envelope arguably needs no field —
    but a history is not one turn. The level rides on each `message` frame, so
    a session that ran one turn off and the next at `high` replays both through
    one projection, and there is no level to key on that is right for every
    message in it. Sending the field always is the shape N115 already proved
    accepted; withholding it per turn is a second guess at a 400 that took a
    provider error to find the first time.
    """

    if control is None or control.control not in ("budget", "preserved"):
        # `effort` and `toggle` say nothing about replay. Sending a field a
        # dialect does not define is the guess this module exists to avoid.
        #
        # Saying nothing about *replay* is not the same as having nothing to
        # resume from: see `resumes_thought_as_context`, which is the other half
        # of this question and the one N90 left unanswered.
        return {}

    return {"reasoning_content": reasoning}


def resumes_thought_as_context(control: ReasoningControl | None) -> bool:
    """Whether an unfinished thought must come back as a following user turn.

    Two different questions wear the same word. `replay_fields` answers what the
    *provider requires* on an assistant turn — a field it will 400 without. This
    answers what the *continuation needs* to resume a thought the token cap cut
    in half, which no provider requires and the model cannot work without.

    For `budget` and `preserved` they are the same answer: `reasoning_content` goes back as a
    field, the provider wants it, and the model reads its own half-thought
    there. One mechanism serves both, which is why N90 looked finished.

    For `effort` and `toggle` they diverge. Neither dialect defines a replay
    field, so `replay_fields` correctly returns nothing — and the thinking was
    therefore dropped on the floor. llama.cpp's `effort` is a flat
    `reasoning_effort` string with no inbound counterpart: there is nowhere on
    an assistant message to put a thought.

    What that cost, measured on this appliance (N139): a 27B at `effort` spent
    all 16,384 tokens of its allowance reasoning, emitted no content, and was
    continued with a prompt five tokens *smaller* than the one before it — the
    whole thought discarded at the dialect gate. The first fix folded the
    thought back into the assistant turn's `content`, which the model read as
    its own prior speech and replayed instead of continuing.

    The thought cannot go on the assistant turn at all on these dialects, so the
    projection writes a following `user` turn: the position that means context
    rather than speech. True rather than a dict because the shape differs. A
    field merges into a message; a user turn is a separate message after it, and
    the projection owns that placement — so this says *whether*, and the caller
    says *how*.
    """

    return control is not None and control.control in ("effort", "toggle")
