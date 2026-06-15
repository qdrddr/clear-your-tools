"""Tests for skills injection stats recording."""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from cyt.common.pricing import StatsCosts, compute_net_savings_tokens, compute_stats_costs
from cyt.proxy.stats import ProxyRequestRecord, StatsDB, empty_totals, format_totals


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


def test_record_skills_injection_stores_request_tokens(temp_stats_db: StatsDB) -> None:
    temp_stats_db.record_skills_injection(
        query="find hook skills",
        model_name="claude-sonnet-4",
        skills_in=120,
        request_tokens=500,
        config={},
    )
    row = temp_stats_db._conn.execute(
        "SELECT request_tokens, endpoint FROM proxy_request WHERE endpoint IN ('skills', 'skills-hook')",
    ).fetchone()
    assert row is not None
    assert int(row[0]) == 500
    assert row[1] == "skills-hook"


def test_record_skills_injection_stores_skills_final_md_when_debug(
    temp_stats_db: StatsDB,
) -> None:
    injected = '<agent-skills><skill name="demo">Demo skill</skill></agent-skills>'
    temp_stats_db.record_skills_injection(
        query="find hook skills",
        model_name="claude-sonnet-4",
        skills_in=120,
        skills_final_md=injected,
        config={},
    )
    row = temp_stats_db._conn.execute(
        "SELECT skills_final_md FROM proxy_request WHERE endpoint IN ('skills', 'skills-hook')",
    ).fetchone()
    assert row is not None
    assert row[0] == injected


def test_record_skills_injection_omits_skills_final_md_by_default(
    temp_stats_db: StatsDB,
) -> None:
    temp_stats_db.record_skills_injection(
        query="find hook skills",
        model_name="claude-sonnet-4",
        skills_in=120,
        config={},
    )
    row = temp_stats_db._conn.execute(
        "SELECT skills_final_md FROM proxy_request WHERE endpoint IN ('skills', 'skills-hook')",
    ).fetchone()
    assert row is not None
    assert row[0] is None


def test_query_skills_budget_state_hook_basis(temp_stats_db: StatsDB) -> None:
    config = {
        "skills": {
            "hook": {
                "request_budget_fraction": 10.0,
                "inject_cap_multiplier_of_request_tokens": 5.0,
            },
            "proxy": {
                "inject_cap_fraction_of_savings": 0.5,
            },
        },
    }
    temp_stats_db.record_skills_injection(
        query="q",
        model_name="hook",
        skills_in=50,
        request_tokens=1000,
        config=config,
    )
    state = temp_stats_db.query_skills_budget_state(config, "hook")
    assert state.skills_injected_total == 50
    assert state.cumulative_request_tokens == 50
    assert state.limit_global == 250
    assert state.limit_global_remaining == 200


def test_query_skills_budget_state_hook_bootstrap_after_proxy_prune_only(
    temp_stats_db: StatsDB,
) -> None:
    config = {
        "skills": {
            "hook": {
                "request_budget_fraction": 10.0,
                "inject_cap_multiplier_of_request_tokens": 5.0,
            },
            "proxy": {
                "inject_cap_fraction_of_savings": 0.5,
            },
        },
    }
    temp_stats_db.record_proxy_request(
        ProxyRequestRecord(
            endpoint="anthropic",
            tools_in=1000,
            tool_count_in=2,
            tool_properties_count_in=5,
            tools_out=0,
            tool_count_out=0,
            tool_properties_count_out=0,
            prune_status="applied",
            pipeline=["llm"],
            upstream_model_name="claude-test",
            upstream_provider_dns="api.anthropic.com",
            upstream_provider="anthropic",
        ),
    )
    state = temp_stats_db.query_skills_budget_state(config, "hook")
    assert state.bootstrap is True
    assert state.cumulative_request_tokens == 0


def test_query_cumulative_request_tokens_includes_upstream_tools_sent(
    temp_stats_db: StatsDB,
) -> None:
    temp_stats_db.record_proxy_request(
        ProxyRequestRecord(
            endpoint="anthropic",
            tools_in=1000,
            tool_count_in=2,
            tool_properties_count_in=5,
            tools_out=800,
            tool_count_out=2,
            tool_properties_count_out=3,
            prune_status="applied",
            pipeline=["llm"],
            upstream_model_name="claude-test",
            upstream_provider_dns="api.anthropic.com",
            upstream_provider="anthropic",
        ),
    )
    assert temp_stats_db.query_cumulative_request_tokens() == 800


def test_query_skills_budget_state_counts_skills_and_upstream_tools_sent(
    temp_stats_db: StatsDB,
) -> None:
    config = {
        "skills": {
            "hook": {
                "request_budget_fraction": 10.0,
                "inject_cap_multiplier_of_request_tokens": 5.0,
            },
            "proxy": {
                "inject_cap_fraction_of_savings": 0.5,
            },
        },
    }
    temp_stats_db.record_skills_injection(
        query="proxy q",
        model_name="claude-test",
        skills_in=100,
        request_tokens=50_000,
        inject_path="proxy",
        config=config,
    )
    temp_stats_db.record_proxy_request(
        ProxyRequestRecord(
            endpoint="anthropic",
            tools_in=1000,
            tool_count_in=2,
            tool_properties_count_in=5,
            tools_out=300,
            tool_count_out=2,
            tool_properties_count_out=3,
            prune_status="applied",
            pipeline=["bm25"],
            upstream_model_name="claude-test",
            upstream_provider_dns="api.anthropic.com",
            upstream_provider="anthropic",
        ),
    )
    state = temp_stats_db.query_skills_budget_state(config, "hook")
    assert state.bootstrap is False
    assert state.cumulative_request_tokens == 400
    assert state.skills_injected_total == 100
    assert state.limit_global == 2000
    assert state.limit_global_remaining == 1900


def test_query_cumulative_request_tokens_excludes_pruner_and_savings(
    temp_stats_db: StatsDB,
) -> None:
    from cyt.common.token_usage import StageTokenUsage

    temp_stats_db.record_proxy_request(
        ProxyRequestRecord(
            endpoint="anthropic",
            tools_in=1000,
            tool_count_in=2,
            tool_properties_count_in=5,
            tools_out=400,
            tool_count_out=2,
            tool_properties_count_out=3,
            prune_status="applied",
            pipeline=["llm", "rerank"],
            upstream_model_name="claude-test",
            upstream_provider_dns="api.anthropic.com",
            upstream_provider="anthropic",
            pruning_stages={
                "llm": StageTokenUsage(
                    model_name="claude-test",
                    input_tokens=5000,
                    output_tokens=200,
                ),
                "rerank": StageTokenUsage(
                    model_name="rerank-model",
                    input_tokens=3000,
                    output_tokens=100,
                ),
            },
        ),
    )
    assert temp_stats_db.query_cumulative_request_tokens() == 400


def test_rollup_sums_request_tokens(temp_stats_db: StatsDB) -> None:
    from datetime import date, datetime, timedelta

    ts = int(
        (
            datetime.now().replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=2)
        ).timestamp()
        * 1000,
    )
    injected = '<agent-skills><skill name="demo">Demo skill</skill></agent-skills>'
    for request_tokens in (100, 200, 300):
        proxy_id = temp_stats_db.record_skills_injection(
            query="q",
            model_name="hook",
            skills_in=10,
            request_tokens=request_tokens,
            skills_final_md=injected,
            config={},
        )
        temp_stats_db._conn.execute(
            "UPDATE proxy_request SET ts_ms = ? WHERE id = ?",
            (ts, proxy_id),
        )
    temp_stats_db._conn.commit()
    temp_stats_db.rollup_historical(today=date.today())
    row = temp_stats_db._conn.execute(
        "SELECT request_tokens, skills_in, skills_final_md FROM proxy_request "
        "WHERE endpoint IN ('skills', 'skills-hook')",
    ).fetchone()
    assert row is not None
    assert int(row[0]) == 600
    assert int(row[1]) == 30
    assert row[2] is None


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
