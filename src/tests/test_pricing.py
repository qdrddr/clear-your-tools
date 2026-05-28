"""Tests for stats cost calculations."""

from __future__ import annotations

from cyt.common.pricing import compute_stats_costs, lookup_llm_pricing


def _sample_config() -> dict:
    return {
        "models": {
            "llm": {
                "remote": [
                    {
                        "name": "google/gemini-3-flash-preview",
                        "nick": "gemini-3-flash",
                        "domain_match": ["openrouter.ai"],
                        "pricing": {
                            "input_cost_per_token": 2.5e-07,
                            "output_cost_per_token": 7.5e-07,
                        },
                    },
                    {
                        "name": "claude-sonnet-4-6",
                        "nick": "sonnet",
                        "domain_match": ["anthropic.com"],
                        "pricing": {
                            "input_cost_per_token": 3e-06,
                            "output_cost_per_token": 15e-06,
                        },
                    },
                ],
            },
        },
    }


def test_lookup_llm_pricing_uses_provider_dns() -> None:
    config = _sample_config()
    openrouter = lookup_llm_pricing(
        config,
        "google/gemini-3-flash-preview",
        "openrouter.ai",
    )
    assert openrouter is not None
    assert openrouter.input_cost_per_token == 2.5e-07

    anthropic = lookup_llm_pricing(config, "claude-sonnet-4-6", "anthropic.com")
    assert anthropic is not None
    assert anthropic.input_cost_per_token == 3e-06


def test_compute_stats_costs_prices_saved_tokens_per_upstream_model() -> None:
    config = _sample_config()
    costs = compute_stats_costs(
        stage_model_tokens=[],
        upstream_saved_tokens=[
            ("google/gemini-3-flash-preview", "openrouter.ai", 600),
            ("claude-sonnet-4-6", "anthropic.com", 100),
        ],
        config=config,
    )
    expected = 600 * 2.5e-07 + 100 * 3e-06
    assert costs.tools_saved_usd == expected
