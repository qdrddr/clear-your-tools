"""Tests for embedded libSQL stats persistence."""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from cyt.common.token_usage import StageTokenUsage
from cyt.proxy.stats import ProxyRequestRecord, StatsDB, format_totals


@pytest.fixture
def temp_db() -> Generator[StatsDB]:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "stats.db")
        db = StatsDB.init(db_path)
        yield db
        db.close()


def test_schema_init_and_record(temp_db: StatsDB) -> None:
    record = ProxyRequestRecord(
        endpoint="anthropic",
        tools_in=1000,
        tool_count_in=10,
        tool_properties_count_in=50,
        tools_out=400,
        tool_count_out=8,
        tool_properties_count_out=20,
        prune_status="applied",
        pipeline=["llm"],
        upstream_model_name="google/gemini-3-flash-preview",
        upstream_provider_dns="openrouter.ai",
        upstream_provider="openrouter",
        pruning_stages={
            "llm": StageTokenUsage(
                input_tokens=500,
                output_tokens=50,
                usage_source="tiktoken:cl100k_base",
                model_name="openrouter/openai/gpt-oss-120b",
                provider="openrouter",
                provider_dns_name="openrouter.ai",
            ),
        },
    )
    proxy_id = temp_db.record_proxy_request(record)
    assert proxy_id

    totals = temp_db.query_totals()
    assert totals["events"] == 1
    assert totals["tools_accepted"] == 1000
    assert totals["tools_sent_upstream"] == 400
    assert totals["tools_saved"] == 600
    assert totals["llm_input"] == 500
    assert totals["llm_output"] == 50


def test_record_without_full_tools_json(temp_db: StatsDB) -> None:
    record = ProxyRequestRecord(
        endpoint="anthropic",
        tools_in=100,
        tool_count_in=2,
        tool_properties_count_in=5,
        tools_out=80,
        tool_count_out=2,
        tool_properties_count_out=3,
        prune_status="pass_through",
        pipeline=[],
        tools_accepted_json=None,
        tools_final_json=None,
    )
    temp_db.record_proxy_request(record)
    events = temp_db.query_events(limit=1)
    assert events[0]["tools_pruned"] == 20


def test_query_totals_format() -> None:
    from cyt.common.pricing import StatsCosts

    costs = StatsCosts(
        tools_saved_usd=0.0005,
        llm_input_usd=0.000015,
        llm_output_usd=0.000012,
        rerank_input_usd=0.0,
        rerank_output_usd=0.0,
    )
    text = format_totals(
        {
            "events": 3,
            "tools_accepted": 3000,
            "tools_sent_upstream": 1000,
            "tools_saved": 2000,
            "llm_input": 100,
            "llm_output": 20,
            "rerank_input": 0,
            "rerank_output": 0,
        },
        costs,
    )
    assert "tools saved:         2000  (66.7%)" in text
    assert "net savings (input tokens):" in text
    assert "  cost:         $0.000473" in text
    assert "  tokens:     1892 (63.1%)" in text


def test_query_totals_format_green_net_savings_tokens() -> None:
    from cyt.common.pricing import StatsCosts

    costs = StatsCosts(
        tools_saved_usd=0.0005,
        llm_input_usd=0.000015,
        llm_output_usd=0.000012,
        rerank_input_usd=0.0,
        rerank_output_usd=0.0,
    )
    totals = {
        "events": 3,
        "tools_accepted": 3000,
        "tools_sent_upstream": 1000,
        "tools_saved": 2000,
        "llm_input": 100,
        "llm_output": 20,
        "rerank_input": 0,
        "rerank_output": 0,
    }
    plain = format_totals(totals, costs, color=False)
    colored = format_totals(totals, costs, color=True)
    assert "  tokens:     1892 (63.1%)" in plain
    assert "\033[32m1892 (63.1%)\033[0m" in colored
    assert "  tokens:     \033[32m1892 (63.1%)\033[0m" in colored


def test_query_upstream_saved_tokens_and_costs(temp_db: StatsDB) -> None:
    from cyt.common.pricing import compute_stats_costs

    record = ProxyRequestRecord(
        endpoint="anthropic",
        tools_in=1000,
        tool_count_in=10,
        tool_properties_count_in=50,
        tools_out=400,
        tool_count_out=8,
        tool_properties_count_out=20,
        prune_status="applied",
        pipeline=["llm"],
        upstream_model_name="google/gemini-3-flash-preview",
        upstream_provider_dns="openrouter.ai",
        upstream_provider="openrouter",
    )
    temp_db.record_proxy_request(record)

    saved = temp_db.query_upstream_saved_tokens()
    assert saved == [("google/gemini-3-flash-preview", "openrouter.ai", 600)]

    config = {
        "models": {
            "llm": {
                "remote": [
                    {
                        "name": "google/gemini-3-flash-preview",
                        "nick": "gemini-3-flash",
                        "domain_match": ["openrouter.ai"],
                        "pricing": {"input_cost_per_token": 2.5e-07},
                    },
                ],
            },
        },
    }
    costs = compute_stats_costs([], saved, config)
    assert costs.tools_saved_usd == 600 * 2.5e-07


def test_record_bm25_pruning_stage_identity(temp_db: StatsDB) -> None:
    record = ProxyRequestRecord(
        endpoint="anthropic",
        tools_in=100,
        tool_count_in=5,
        tool_properties_count_in=10,
        tools_out=80,
        tool_count_out=4,
        tool_properties_count_out=6,
        prune_status="applied",
        pipeline=["bm25"],
        pruning_stages={
            "bm25": StageTokenUsage(
                model_name="bm25",
                provider_dns_name="bm25",
                provider="bm25",
                usage_source="local:bm25",
            ),
        },
    )
    temp_db.record_proxy_request(record)

    identities = temp_db.query_distinct_model_identities()
    assert ("bm25", "bm25", "bm25", "bm25") in identities


def test_stats_db_init_creates_parent_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "nested" / "stats.db")
        db = StatsDB.init(db_path)
        try:
            assert Path(db_path).exists()
        finally:
            db.close()
