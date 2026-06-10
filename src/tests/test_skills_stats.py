"""Tests for skills injection stats recording."""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from cyt.common.pricing import StatsCosts, compute_net_savings_tokens, compute_stats_costs
from cyt.proxy.stats import StatsDB, empty_totals, format_totals


@pytest.fixture
def temp_stats_db() -> Generator[StatsDB]:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "stats.db")
        db = StatsDB.init(db_path)
        yield db
        db.close()


def test_record_skills_injection_and_totals(temp_stats_db: StatsDB) -> None:
    config = {
        "models": {
            "llm": {
                "remote": [
                    {
                        "name": "claude-sonnet-4",
                        "pricing": {"input_cost_per_token": 0.000003},
                    },
                ],
            },
        },
    }
    temp_stats_db.record_skills_injection(
        query="find hook skills",
        model_name="claude-sonnet-4",
        skills_in=120,
        config=config,
    )

    totals = temp_stats_db.query_totals()
    assert totals["skills_in"] == 120
    assert totals["events"] == 1

    costs = compute_stats_costs(
        temp_stats_db.query_stage_model_tokens(),
        temp_stats_db.query_upstream_saved_tokens(),
        config,
        skills_injection_tokens=temp_stats_db.query_skills_injection_tokens(),
    )
    assert costs.skills_input_usd == pytest.approx(120 * 0.000003)

    savings_costs = StatsCosts(
        tools_saved_usd=1.0,
        llm_input_usd=costs.llm_input_usd,
        llm_output_usd=costs.llm_output_usd,
        rerank_input_usd=costs.rerank_input_usd,
        rerank_output_usd=costs.rerank_output_usd,
        skills_input_usd=costs.skills_input_usd,
    )
    net_tokens, _pct = compute_net_savings_tokens(1000, 2000, savings_costs, skills_in=120)
    base_net, _ = compute_net_savings_tokens(1000, 2000, savings_costs, skills_in=0)
    assert net_tokens == base_net - 120


def test_format_totals_includes_skills_context_line() -> None:
    totals = empty_totals()
    totals["skills_in"] = 50
    costs = StatsCosts(
        tools_saved_usd=1.0,
        llm_input_usd=0.1,
        llm_output_usd=0.0,
        rerank_input_usd=0.0,
        rerank_output_usd=0.0,
        skills_input_usd=0.05,
    )
    rendered = format_totals(totals, costs, color=False)
    assert "skills context added" in rendered
    assert "50" in rendered
