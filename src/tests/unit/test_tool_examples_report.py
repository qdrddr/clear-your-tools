"""Unit tests for RRF-scored tool example reports."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cyt.tool_examples.report import format_query_score_report, format_query_score_report_json
from tests.support.tool_examples_fixtures import (
    install_staggered_capture_clock,
    run_ranking_query_score_report,
)


@pytest.fixture
def seeded_ranking_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    install_staggered_capture_clock(monkeypatch)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    db_path = tmp_path / "tool_examples.db"
    return db_path, workspace


def test_build_ranking_report_includes_usage_and_rrf_scores(
    seeded_ranking_db: tuple[Path, Path],
) -> None:
    db_path, workspace = seeded_ranking_db
    report = run_ranking_query_score_report(
        "06_city_diversity",
        workspace=workspace,
        db_path=db_path,
    )
    assert report.query_id == "06_city_diversity"
    assert report.tools
    city_tool = report.tools[0]
    assert city_tool.mcp_server == "geo"
    assert city_tool.tool_name == "lookup"
    assert city_tool.properties
    city_prop = city_tool.properties[0]
    assert city_prop.full_path == "geo.lookup.inputSchema.properties.city"
    assert len(city_prop.examples) == 3
    for example in city_prop.examples:
        assert example.rrf_score > 0
        assert example.usage_score >= 0
        assert "bm25" in example.channel_ranks
        assert "usage" in example.channel_ranks
        assert "recency" in example.channel_ranks


def test_ranking_report_json_round_trip(seeded_ranking_db: tuple[Path, Path]) -> None:
    db_path, workspace = seeded_ranking_db
    report = run_ranking_query_score_report(
        "07_usage_weighted_repo",
        workspace=workspace,
        db_path=db_path,
    )
    payload = json.loads(format_query_score_report_json(report))
    assert payload["query_id"] == "07_usage_weighted_repo"
    repo_examples = next(
        example
        for tool in payload["tools"]
        if tool["wire_name"] == "jcodemunch_search_symbols"
        for prop in tool["properties"]
        if prop["json_path"] == "inputSchema.properties.repo"
        for example in prop["examples"]
    )
    assert repo_examples["value"] == "local/clear-your-tools"
    assert repo_examples["usage_score"] > 0
    assert repo_examples["rrf_score"] > 0


def test_format_query_score_report_renders_channel_scores(
    seeded_ranking_db: tuple[Path, Path],
) -> None:
    db_path, workspace = seeded_ranking_db
    report = run_ranking_query_score_report(
        "06_city_diversity",
        workspace=workspace,
        db_path=db_path,
    )
    text = format_query_score_report(report)
    assert "Query ID: 06_city_diversity" in text
    assert "rrf=" in text
    assert "usage=" in text
    assert "bm25=" in text
    assert "recency=" in text
