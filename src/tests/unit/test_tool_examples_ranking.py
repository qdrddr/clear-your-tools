"""Unit tests for tool example RRF ranking and diversity."""

from __future__ import annotations

import json
from unittest.mock import patch

from cyt.tool_examples.config import ExampleRankingConfig
from cyt.tool_examples.ranking import (
    RankedExampleValue,
    effective_example_pipeline,
    rank_example_values,
    rank_full_call_examples,
    reciprocal_rank_fusion,
    select_diverse_values,
    usage_popularity_score,
    values_too_similar,
)
from cyt.tool_examples.store import ToolExampleRow


def _ranking_config(**overrides: object) -> ExampleRankingConfig:
    defaults = {
        "pipeline": ("bm25",),
        "rrf_k": 60,
        "rerank_min_candidates": 3,
        "llm_min_candidates": 5,
        "diversity_threshold": 0.6,
    }
    defaults.update(overrides)
    return ExampleRankingConfig(**defaults)  # type: ignore[arg-type]


def _row(value: str, *, success_count: int = 1, timestamp_ms: int = 100) -> ToolExampleRow:
    return ToolExampleRow(
        schema_id=1,
        json_path="inputSchema.properties.city",
        value=value,
        value_type="string",
        timestamp_ms=timestamp_ms,
        success_count=success_count,
    )


def test_usage_popularity_score_prefers_higher_counts() -> None:
    high = usage_popularity_score(10, 20)
    low = usage_popularity_score(1, 20)
    assert high > low


def test_reciprocal_rank_fusion_combines_rankings() -> None:
    scores = reciprocal_rank_fusion(
        [
            ["a", "b", "c"],
            ["b", "a", "c"],
        ],
        k=60,
    )
    assert scores["a"] > scores["c"]
    assert scores["b"] > scores["c"]


def test_values_too_similar_detects_city_variants() -> None:
    assert values_too_similar("Chicago", "Chicago, IL", threshold=0.6)
    assert values_too_similar('"Chicago"', '"Chicago, IL"', threshold=0.6)
    assert values_too_similar("Chicago, IL", "Chicago, Illinois", threshold=0.6)
    assert not values_too_similar("Chicago, IL", "New York, NY", threshold=0.6)


def test_select_diverse_values_skips_similar_variants() -> None:
    scores = {
        '"Chicago"': 1.0,
        '"Chicago, IL"': 0.9,
        '"Chicago, Illinois"': 0.85,
        '"New York, NY"': 0.8,
        '"San Francisco, CA"': 0.75,
    }
    selected = select_diverse_values(scores, max_count=3, diversity_threshold=0.6)
    assert len(selected) == 3
    chicago_variants = sum(1 for value in selected if "chicago" in value.lower())
    assert chicago_variants == 1
    assert any("new york" in value.lower() for value in selected)
    assert any("san francisco" in value.lower() for value in selected)


def test_rank_example_values_prefers_high_usage() -> None:
    rows = [
        _row('"Chicago"', success_count=1, timestamp_ms=100),
        _row('"New York, NY"', success_count=8, timestamp_ms=100),
        _row('"San Francisco, CA"', success_count=6, timestamp_ms=100),
        _row('"Chicago, IL"', success_count=1, timestamp_ms=100),
    ]
    config = {"tools": {"sequence": ["bm25"]}}
    ranked = rank_example_values(
        "city address",
        rows,
        max_count=3,
        config=config,
        ranking=_ranking_config(),
        property_name="city",
        property_description="City name",
    )
    values = [item.value for item in ranked]
    assert len(values) == 3
    assert '"New York, NY"' in values
    assert '"San Francisco, CA"' in values
    chicago_variants = [value for value in values if "Chicago" in value]
    assert len(chicago_variants) == 1


def test_rank_example_values_includes_remote_channels_when_configured() -> None:
    rows = [
        _row('"alpha"', success_count=2, timestamp_ms=100),
        _row('"beta"', success_count=2, timestamp_ms=200),
        _row('"gamma"', success_count=2, timestamp_ms=300),
    ]
    config = {"tools": {"sequence": ["bm25", "rerank", "llm"]}}

    with (
        patch(
            "cyt.tool_examples.ranking._rerank_ranking",
            return_value=['"gamma"', '"beta"', '"alpha"'],
        ) as rerank_mock,
        patch(
            "cyt.tool_examples.ranking._llm_ranking",
            return_value=['"beta"', '"alpha"', '"gamma"'],
        ) as llm_mock,
    ):
        ranked = rank_example_values(
            "beta query",
            rows,
            max_count=2,
            config=config,
            ranking=_ranking_config(pipeline=("bm25", "rerank", "llm"), llm_min_candidates=3),
            property_name="query",
            property_description="Search query",
        )

    rerank_mock.assert_called_once()
    llm_mock.assert_called_once()
    assert len(ranked) == 2
    assert all(isinstance(item, RankedExampleValue) for item in ranked)


def test_effective_example_pipeline_inherits_tools_sequence() -> None:
    config = {"tools": {"sequence": ["bm25", "rerank", "llm"]}}
    ranking = _ranking_config(pipeline="inherit")
    assert effective_example_pipeline(config, ranking, candidate_count=5) == [
        "bm25",
        "rerank",
        "llm",
    ]


def test_effective_example_pipeline_skips_rerank_when_too_few_candidates() -> None:
    config = {"tools": {"sequence": ["bm25", "rerank", "llm"]}}
    ranking = _ranking_config(pipeline="inherit", rerank_min_candidates=3, llm_min_candidates=5)
    assert effective_example_pipeline(config, ranking, candidate_count=2) == ["bm25"]


def test_effective_example_pipeline_uses_explicit_pipeline() -> None:
    config = {"tools": {"sequence": ["bm25", "rerank", "llm"]}}
    ranking = _ranking_config(pipeline=("bm25",))
    assert effective_example_pipeline(config, ranking, candidate_count=10) == ["bm25"]


def test_rank_full_call_examples_diversifies_json_payloads() -> None:
    captures = [
        ({"city": "Chicago, IL"}, 100),
        ({"city": "Chicago"}, 200),
        ({"city": "New York, NY"}, 300),
        ({"city": "San Francisco, CA"}, 400),
    ]
    config = {"tools": {"sequence": ["bm25"]}}
    ranked = rank_full_call_examples(
        "city address lookup",
        captures,
        max_count=3,
        config=config,
        ranking=_ranking_config(),
    )
    assert len(ranked) == 3
    decoded = [json.loads(item) for item in ranked]
    chicago_variants = sum(1 for item in decoded if "Chicago" in item.get("city", ""))
    assert chicago_variants == 1
