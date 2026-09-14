"""Tests for tier statistics aggregation and formatting."""

from __future__ import annotations

from pathlib import Path

from cyt.tiers.status_statistics import (
    append_tier_statistics_tables,
    build_kind_tier_statistics,
    format_compact_tokens,
)


def test_format_compact_tokens() -> None:
    assert format_compact_tokens(512) == "512"
    assert format_compact_tokens(10342) == "10.3k"
    assert format_compact_tokens(10000) == "10k"
    assert format_compact_tokens(1500) == "1.5k"


def test_build_kind_tier_statistics_histogram_and_temp() -> None:
    kind_block = {
        "mode": "shadow",
        "histogram": {"T0": 1, "T1": 0, "T2": 2, "T3": 1, "T4": 0},
        "by_tier": {
            "T0": [{"entity_id": "cyt_mcp:cold", "temporary": False, "stats": {}}],
            "T1": [],
            "T2": [
                {"entity_id": "cyt_mcp:a", "temporary": False, "stats": {"injected": 3}},
                {"entity_id": "cyt_mcp:b", "temporary": True, "stats": {"injected": 2}},
            ],
            "T3": [{"entity_id": "cyt_mcp:hot", "temporary": True, "stats": {"injected": 5}}],
            "T4": [],
        },
    }
    stats = build_kind_tier_statistics(kind_block, kind="tool")
    rows = stats["rows"]
    assert [row["tier"] for row in rows] == ["T0", "T1", "T2", "T3", "T4"]
    assert rows[0]["count"] == 1
    assert rows[0]["temp"] == 0
    assert rows[0]["tokens"] == 0
    assert rows[0]["effective_tokens"] == 0
    assert rows[0]["tokens_known"] == 1
    assert rows[2]["count"] == 2
    assert rows[2]["temp"] == 1
    assert rows[3]["count"] == 1
    assert rows[3]["temp"] == 1
    totals = stats["totals"]
    assert totals["count"] == 4
    assert totals["temp"] == 2
    assert stats["activity"]["injected"] == 10.0
    assert stats["activity"]["injected_entities"] == 3
    assert stats["activity"]["used_entities"] == 0


def test_build_kind_tier_statistics_tool_tokens_from_catalog() -> None:
    from cyt.tiers.tool_token_materialization import clear_carried_token_memo

    clear_carried_token_memo()
    catalog = [
        {
            "name": "search",
            "cyt_catalog_source": "cyt_mcp",
            "description": "Search the codebase.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "search query"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    ]
    kind_block = {
        "histogram": {"T0": 0, "T1": 0, "T2": 1, "T3": 0, "T4": 0},
        "by_tier": {
            "T0": [],
            "T1": [],
            "T2": [
                {
                    "entity_id": "cyt_mcp:search",
                    "temporary": False,
                    "stats": {},
                    "token_count": 999,
                },
            ],
            "T3": [],
            "T4": [],
        },
    }
    stats = build_kind_tier_statistics(kind_block, kind="tool", catalog_tools=catalog)
    row = stats["rows"][2]
    assert row["tokens"] == 999
    assert row["tokens_known"] == 1
    assert row["effective_tokens_known"] == 1
    assert row["effective_tokens"] > 0
    assert row["effective_tokens"] < row["tokens"]
    assert stats["totals"]["tokens"] == 999
    assert stats["totals"]["effective_tokens"] == row["effective_tokens"]


def test_build_kind_tier_statistics_skill_tokens_from_source_path(tmp_path: Path) -> None:
    skill_file = tmp_path / "demo-skill" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("---\nname: demo\n---\none two three four five\n", encoding="utf-8")

    kind_block = {
        "histogram": {"T0": 0, "T1": 1, "T2": 0, "T3": 0, "T4": 0},
        "by_tier": {
            "T0": [],
            "T1": [
                {
                    "entity_id": "skill:demo",
                    "temporary": False,
                    "source_path": str(skill_file),
                    "stats": {},
                },
            ],
            "T2": [],
            "T3": [],
            "T4": [],
        },
    }
    stats = build_kind_tier_statistics(kind_block, kind="skill")
    row = stats["rows"][1]
    assert row["count"] == 1
    assert row["tokens_known"] == 1
    assert row["tokens"] > 0
    assert row["effective_tokens_known"] == 1
    assert row["effective_tokens"] > 0
    assert row["effective_tokens"] < row["tokens"]
    assert stats["totals"]["effective_tokens"] == row["effective_tokens"]


def test_build_kind_tier_statistics_skill_t0_zeros() -> None:
    kind_block = {
        "histogram": {"T0": 1, "T1": 0, "T2": 0, "T3": 0, "T4": 0},
        "by_tier": {
            "T0": [{"entity_id": "skill:dormant", "temporary": False, "stats": {}}],
            "T1": [],
            "T2": [],
            "T3": [],
            "T4": [],
        },
    }
    stats = build_kind_tier_statistics(kind_block, kind="skill")
    row = stats["rows"][0]
    assert row["tokens"] == 0
    assert row["effective_tokens"] == 0
    assert row["tokens_known"] == 1
    assert row["effective_tokens_known"] == 1


def test_build_kind_tier_statistics_unknown_tokens() -> None:
    kind_block = {
        "histogram": {"T0": 0, "T1": 1, "T2": 0, "T3": 0, "T4": 0},
        "by_tier": {
            "T0": [],
            "T1": [{"entity_id": "cyt_mcp:missing", "temporary": False, "stats": {}}],
            "T2": [],
            "T3": [],
            "T4": [],
        },
    }
    stats = build_kind_tier_statistics(kind_block, kind="tool", catalog_tools=[])
    row = stats["rows"][1]
    assert row["count"] == 1
    assert row["tokens_known"] == 0
    assert row["tokens"] == 0


def test_append_tier_statistics_tables_historical_activity_lines() -> None:
    lines: list[str] = []

    def _format_table_row(columns: list[str], widths: list[int]) -> str:
        return "  ".join(str(column).ljust(width) for column, width in zip(columns, widths))

    append_tier_statistics_tables(
        lines,
        {
            "tier_statistics": {
                "tools": {
                    "rows": [],
                    "totals": {"count": 0, "temp": 0, "tokens": 0, "tokens_known": 0},
                    "activity": {
                        "injected": 270.34203593954095,
                        "used": 41.8403994495833,
                        "injected_entities": 88,
                        "used_entities": 45,
                    },
                },
                "skills": {
                    "rows": [],
                    "totals": {"count": 0, "temp": 0, "tokens": 0, "tokens_known": 0},
                    "activity": {
                        "injected": 3.0,
                        "used": 12.0,
                        "injected_entities": 2,
                        "used_entities": 5,
                    },
                },
            },
        },
        format_table_row=_format_table_row,
    )

    assert "Historical signals (decayed sum):" in lines
    assert "  Demand:  tools injected=270.3 (88)  skills injected=3 (2)" in lines
    assert "  Usage:   tools used=41.8 (45)  skills used=12 (5)" in lines
