"""Pricing table tests. The headline metric of this tool is cost, so an
unpriced model must report nothing rather than a plausible-looking number.
"""

import pytest

from agentgauge.models.run import TokenUsage
from agentgauge.providers.anthropic_provider import AnthropicProvider
from agentgauge.providers.openai_provider import OpenAIProvider
from agentgauge.providers.pricing import Price, known_models, lookup

ONE_M = TokenUsage(tokens_in=1_000_000, tokens_out=1_000_000)


def test_unknown_model_has_no_price():
    assert lookup("claude-from-the-future") is None
    assert lookup("gpt-9-turbo") is None
    assert lookup("") is None


def test_unknown_model_reports_no_cost_rather_than_a_guess():
    assert AnthropicProvider("claude-from-the-future", api_key="t").compute_cost(ONE_M) is None
    assert OpenAIProvider("gpt-9-turbo", api_key="t").compute_cost(ONE_M) is None


def test_dated_snapshots_resolve_to_their_base_entry():
    dated, base = lookup("claude-sonnet-5-20260101"), lookup("claude-sonnet-5")
    assert dated is not None and base is not None
    assert dated.input == base.input


def test_longest_prefix_wins():
    """gpt-4o-mini must not be priced as gpt-4o."""
    mini, base = lookup("gpt-4o-mini"), lookup("gpt-4o")
    assert mini is not None and base is not None
    assert mini.input == 0.15
    assert base.input == 2.5


def test_current_models_are_priced():
    for model in (
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5",
        "claude-fable-5-1",
        "claude-opus-4-8",
    ):
        assert lookup(model) is not None, f"{model} missing from pricing.yaml"


def test_local_models_are_free_explicitly():
    price = lookup("ollama/qwen2.5-coder")
    assert price is not None and price.cost(ONE_M) == 0.0


def test_cache_tokens_are_billed():
    """Dropping cache tokens silently undercounts a cached run."""
    price = Price(input=10.0, output=50.0)
    read_only = TokenUsage(cache_read_tokens=1_000_000)
    write_only = TokenUsage(cache_write_tokens=1_000_000)
    assert price.cost(read_only) == pytest.approx(1.0)  # 0.10x input
    assert price.cost(write_only) == pytest.approx(12.5)  # 1.25x input


def test_explicit_cache_rate_overrides_the_multiplier():
    price = lookup("claude-fable-5-1")
    assert price is not None
    assert price.cost(TokenUsage(cache_read_tokens=1_000_000)) == pytest.approx(0.25)


def test_every_table_entry_parses():
    assert known_models()
    for model in known_models():
        price = lookup(model)
        assert price is not None, model
        assert price.cost(ONE_M) >= 0.0
