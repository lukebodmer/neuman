"""Pricing a turn: the provider's figure when there is one, ours when there is not."""

from __future__ import annotations

from neuman_server.decision.harness.pricing import turn_cost

# DeepSeek's off-peak Flash rates, which is the case the cache split exists for:
# a factor of fifty between a hit and a miss.
MISS = 0.15
HIT = 0.003
OUT = 0.60


def test_a_provider_that_reports_cost_is_believed_over_our_arithmetic():
    """OpenRouter bills in credits and says so; recomputing would invent a figure.

    `cost` already accounts for cache discounts and whatever routing it did, so
    a rate table of ours cannot improve on it and would disagree with the
    invoice.
    """

    cost = turn_cost(
        {"prompt_tokens": 10_000, "completion_tokens": 500, "cost": 0.0123},
        input_cost_per_m=MISS,
        output_cost_per_m=OUT,
    )

    assert cost.usd == 0.0123
    assert cost.estimated is False


def test_a_reported_zero_is_a_price_rather_than_a_missing_field():
    """A free turn reports 0.0, and `or` would read that as absent.

    The bug this guards: falling through to arithmetic and reporting a cost for
    a turn the provider charged nothing for.
    """

    cost = turn_cost(
        {"prompt_tokens": 10_000, "completion_tokens": 500, "cost": 0.0},
        input_cost_per_m=MISS,
        output_cost_per_m=OUT,
    )

    assert cost.usd == 0.0
    assert cost.estimated is False


def test_cache_hits_are_priced_at_the_hit_rate():
    """The whole reason a second rate is declared.

    DeepSeek charges $0.003 per million against $0.15 — fifty times less — and
    an agentic turn resends the same prefix every step, so the hit path is what
    a conversation actually runs on. Pricing the prompt at one rate is the
    overstatement this exists to prevent.
    """

    usage = {
        "prompt_tokens": 100_000,
        "prompt_cache_hit_tokens": 98_000,
        "prompt_cache_miss_tokens": 2_000,
        "completion_tokens": 1_000,
    }

    cost = turn_cost(
        usage,
        input_cost_per_m=MISS,
        cached_input_cost_per_m=HIT,
        output_cost_per_m=OUT,
    )

    expected = (2_000 * MISS + 98_000 * HIT + 1_000 * OUT) / 1_000_000
    assert cost.usd == expected
    assert cost.estimated is True

    # And the figure a single input rate would have produced, which is the one
    # a user would have been shown.
    flat = turn_cost(usage, input_cost_per_m=MISS, output_cost_per_m=OUT)
    assert flat.usd > cost.usd * 5


def test_an_openai_shaped_cached_subset_is_read_as_a_subset():
    """`prompt_tokens_details.cached_tokens` counts *within* `prompt_tokens`.

    DeepSeek's pair sums to the prompt; this dialect does not, and treating it
    as a pair would bill the cached tokens twice.
    """

    cost = turn_cost(
        {
            "prompt_tokens": 10_000,
            "prompt_tokens_details": {"cached_tokens": 9_000},
            "completion_tokens": 100,
        },
        input_cost_per_m=MISS,
        cached_input_cost_per_m=HIT,
        output_cost_per_m=OUT,
    )

    expected = (1_000 * MISS + 9_000 * HIT + 100 * OUT) / 1_000_000
    assert cost.usd == expected


def test_a_cache_split_that_does_not_cover_the_prompt_bills_the_remainder():
    """The pair is trusted as far as it goes and no further.

    A provider whose hit/miss do not sum to `prompt_tokens` would otherwise
    have the difference silently cost nothing.
    """

    cost = turn_cost(
        {
            "prompt_tokens": 10_000,
            "prompt_cache_hit_tokens": 1_000,
            "prompt_cache_miss_tokens": 2_000,
            "completion_tokens": 0,
        },
        input_cost_per_m=MISS,
        cached_input_cost_per_m=HIT,
        output_cost_per_m=OUT,
    )

    # 7,000 unaccounted for, billed at the miss rate rather than dropped.
    expected = (9_000 * MISS + 1_000 * HIT) / 1_000_000
    assert cost.usd == expected


def test_a_provider_without_the_discount_prices_the_whole_prompt():
    """None means "does not discount", not "discounts to zero".

    A `cached_input_cost_per_m` of 0 would make a warm prompt free, which is
    the reason the catalog carries None rather than defaulting it.
    """

    cost = turn_cost(
        {
            "prompt_tokens": 10_000,
            "prompt_tokens_details": {"cached_tokens": 9_000},
            "completion_tokens": 0,
        },
        input_cost_per_m=MISS,
        cached_input_cost_per_m=None,
        output_cost_per_m=OUT,
    )

    assert cost.usd == 10_000 * MISS / 1_000_000


def test_a_model_with_no_rates_reports_nothing_rather_than_zero():
    """Local qwen costs nothing to run, but $0.00 is a claim about billing.

    The client shows no figure at all, which is why this is None: "free" and
    "not priced" are different, and the zero is the one that gets believed.
    """

    cost = turn_cost({"prompt_tokens": 10_000, "completion_tokens": 500})

    assert cost.usd is None
    assert cost.estimated is False


def test_rates_without_counts_report_nothing():
    """A usage dict the provider never filled in prices nothing."""

    assert turn_cost({}, input_cost_per_m=MISS, output_cost_per_m=OUT).usd is None
    assert turn_cost(None, input_cost_per_m=MISS).usd is None


def test_a_string_token_count_does_not_raise_mid_turn():
    """These arrive over JSON, and a bare float() would fail after the spend.

    The shape of failure the declared catalog fields exist to prevent: an
    error at the end of a turn whose tokens are already gone.
    """

    cost = turn_cost(
        {"prompt_tokens": "10000", "completion_tokens": "500", "cost": None},
        input_cost_per_m=MISS,
        output_cost_per_m=OUT,
    )

    assert cost.usd == (10_000 * MISS + 500 * OUT) / 1_000_000

    # A cost field that is not a number falls through to arithmetic rather
    # than being forwarded as one.
    assert turn_cost({"cost": "free", "prompt_tokens": 1_000}, input_cost_per_m=MISS).usd == (
        1_000 * MISS / 1_000_000
    )
