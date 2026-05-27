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
        strong_model="google/gemini-3-flash-preview",
        strong_input_rate=2.5e-07,
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
    assert "tools saved:         2000" in text
    assert "net savings:" in text
    assert "net token savings" not in text


def test_stats_db_init_creates_parent_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "nested" / "stats.db")
        db = StatsDB.init(db_path)
        try:
            assert Path(db_path).exists()
        finally:
            db.close()
