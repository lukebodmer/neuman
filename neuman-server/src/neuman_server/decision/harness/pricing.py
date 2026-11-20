"""What a turn cost, preferring the provider's own figure over our arithmetic.

Two sources, and they are not equal:

* **Reported.** OpenRouter returns ``usage.cost`` — the credits actually
  charged — in the final SSE message of every stream, with no opt-in. It
  already accounts for cache discounts, per-provider surcharges and whatever
  routing it did, so it is right by construction and needs no rate from us.
  Verified against OpenRouter's usage-accounting documentation, which states
  the older ``usage: {include: true}`` and ``stream_options`` opt-ins are
  deprecated and that full usage details are now always included.

* **Computed.** DeepSeek reports no cost, so a turn there is priced from the
  operator's declared rates. It does report the cache split
  (``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``), which is the
  only reason computing is viable at all: it prices hits at $0.003 per
  million against $0.15 for misses, a factor of fifty, and an agentic turn
  resends the same long prefix every step. Pricing the whole prompt at the
  miss rate would overstate a warm session by nearly that factor.

Which source answered is carried out with the number, because a figure the
provider billed and a figure we multiplied are different claims and the UI
should not present them as one. `estimated` is the honest word for the second.

**What this deliberately does not model.** DeepSeek charges half rate
off-peak (01:00-04:00 and 06:00-10:00 UTC on weekdays is peak, with Chinese
public holidays counting as off-peak in full). Pricing by wall clock would put
a clock and a holiday calendar in the cost path to turn one wrong number into
a differently wrong one, so the declared rate is used as stated and a computed
figure may be up to 2x high. That is a known inaccuracy, not a hidden one —
hence `estimated`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["TurnCost", "turn_cost"]

_PER_MILLION = 1_000_000.0


@dataclass(frozen=True, slots=True)
class TurnCost:
    """What one turn cost, and how confidently.

    `usd` is None when nothing can be said — a local model with no rates, or a
    provider that reported neither cost nor countable tokens. None rather than
    0.0, because "free" and "unknown" are different and a footer showing
    $0.00 for an unpriced cloud turn is the kind of number that gets believed.

    `estimated` is True when the figure came from our arithmetic rather than
    the provider's own accounting.
    """

    usd: float | None
    estimated: bool


def _number(value: Any) -> float | None:
    """`value` as a float, or None when it is not one.

    Providers send these over JSON and a surprising field is a string or null
    often enough that a bare float() would raise mid-turn, after the tokens
    are spent — the shape of failure the catalog's declared fields exist to
    prevent.
    """

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _int(value: Any) -> int:
    number = _number(value)
    return int(number) if number is not None and number > 0 else 0


def turn_cost(
    usage: dict[str, Any] | None,
    *,
    input_cost_per_m: float = 0.0,
    cached_input_cost_per_m: float | None = None,
    output_cost_per_m: float = 0.0,
) -> TurnCost:
    """Price one turn from its usage dict and the model's declared rates."""

    if not usage:
        return TurnCost(None, False)

    # The provider's own figure wins. Checked for presence rather than truth:
    # a genuinely free turn reports 0.0, and `or` would discard it and fall
    # through to arithmetic that invents a different number.
    reported = _number(usage.get("cost"))
    if reported is not None and reported >= 0:
        return TurnCost(reported, False)

    if input_cost_per_m <= 0 and output_cost_per_m <= 0:
        # No rates to multiply by. A local model, where the answer is that
        # there is nothing to say rather than that it was free — the client
        # shows no figure at all.
        return TurnCost(None, False)

    prompt = _int(usage.get("prompt_tokens"))
    completion = _int(usage.get("completion_tokens"))

    # The cache split, in whichever dialect reported it. DeepSeek sends a
    # hit/miss pair that sums to the prompt; the OpenAI-compatible shape sends
    # `prompt_tokens_details.cached_tokens` as a subset of it.
    hit = _int(usage.get("prompt_cache_hit_tokens"))
    miss = _int(usage.get("prompt_cache_miss_tokens"))
    if not hit and not miss:
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            hit = _int(details.get("cached_tokens"))
            miss = max(prompt - hit, 0)

    if hit or miss:
        # Trust the pair over `prompt_tokens` only as far as it goes: if the
        # two do not account for the whole prompt, the remainder is billed at
        # the miss rate rather than silently dropped.
        counted = hit + miss
        if prompt > counted:
            miss += prompt - counted
    else:
        miss = prompt

    cached_rate = (
        cached_input_cost_per_m if cached_input_cost_per_m is not None else input_cost_per_m
    )

    usd = (
        miss * input_cost_per_m + hit * cached_rate + completion * output_cost_per_m
    ) / _PER_MILLION

    if prompt <= 0 and completion <= 0:
        # Rates but no counts — nothing was measured, so there is nothing to
        # report. Distinct from a turn that genuinely cost nothing.
        return TurnCost(None, False)

    return TurnCost(usd, True)
