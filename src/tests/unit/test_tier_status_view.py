"""Tests for tier status view filtering and formatting."""

from __future__ import annotations

from cyt.tiers.status_view import (
    StatusFilters,
    apply_status_view,
    entity_matches_name,
    entity_matches_server,
    filter_status_entities,
    flatten_status_entities,
    format_entity_detail,
    format_status_text,
    normalize_tier_filter,
)


def _sample_payload() -> dict:
    return {
        "tools": {
            "by_tier": {
                "T3": [
                    {
                        "entity_id": "cyt_mcp:search",
                        "base_tier": "T2",
                        "effective_tier": "T3",
                        "temporary_tier": "T3",
                        "stats": {"candidates": 10, "injected": 4, "used": 2},
                        "scores": {"demand": 0.75},
                        "hints": ["high_demand"],
                    },
                ],
            },
        },
        "skills": {
            "by_tier": {
                "T1": [
                    {
                        "entity_id": "/path/to/explore/SKILL.md",
                        "name": "explore",
                        "base_tier": "T1",
                        "effective_tier": "T1",
                        "stats": {"candidates": 3},
                    },
                ],
            },
        },
    }


def test_normalize_tier_filter() -> None:
    assert normalize_tier_filter("T2") == "T2"
    assert normalize_tier_filter("2") == "T2"
    assert normalize_tier_filter("t0") == "T0"
    assert normalize_tier_filter("bad") is None


def test_flatten_and_filter_entities() -> None:
    entities = flatten_status_entities(_sample_payload())
    assert len(entities) == 2
    assert entities[0]["kind"] == "skill"
    assert entities[1]["kind"] == "tool"

    filtered = filter_status_entities(entities, StatusFilters(kind="tools"))
    assert len(filtered) == 1
    assert filtered[0]["entity_id"] == "cyt_mcp:search"

    filtered = filter_status_entities(entities, StatusFilters(tier="T3"))
    assert len(filtered) == 1
    assert filtered[0]["effective_tier"] == "T3"

    filtered = filter_status_entities(entities, StatusFilters(name="explore"))
    assert len(filtered) == 1
    assert filtered[0]["name"] == "explore"


def test_entity_matches_name() -> None:
    entity = {"entity_id": "cyt_mcp:search", "kind": "tool"}
    assert entity_matches_name(entity, "search")
    assert not entity_matches_name(entity, "missing")


def test_entity_matches_server() -> None:
    entity = {
        "kind": "tool",
        "entity_id": "cyt_mcp:context-mode_ctx_execute",
        "mcp_server": "context-mode",
    }
    assert entity_matches_server(entity, "context-mode")
    assert not entity_matches_server(entity, "codegraph")
    assert not entity_matches_server({"kind": "skill", "entity_id": "skill:x"}, "x")


def test_filter_by_server_defaults_to_tools() -> None:
    entities = [
        {"kind": "tool", "entity_id": "cyt_mcp:context-mode_a", "mcp_server": "context-mode"},
        {"kind": "tool", "entity_id": "cyt_mcp:codegraph_b", "mcp_server": "codegraph"},
        {"kind": "skill", "entity_id": "skill:demo", "name": "demo"},
    ]
    filtered = filter_status_entities(entities, StatusFilters(server="context-mode"))
    assert len(filtered) == 1
    assert filtered[0]["mcp_server"] == "context-mode"

    filters = StatusFilters.from_args(
        type("Args", (), {"kind": "all", "tier": None, "name": None, "server": "context-mode"})(),
    )
    payload = apply_status_view(
        {
            "tools": {
                "by_tier": {
                    "T2": [entities[0], entities[1]],
                },
            },
            "skills": {"by_tier": {"T1": [entities[2]]}},
        },
        filters,
    )
    assert payload["entity_count"] == 1
    assert payload["filters"]["kind"] == "tools"
    assert payload["filters"]["server"] == "context-mode"
    assert payload["mode"] == "filtered"
    assert "skills" not in payload
    assert "tools" not in payload
    assert len(payload["entities"]) == 1


def test_format_status_text_server_filter_shows_grouped_summaries() -> None:
    payload = {
        "project_id": 13,
        "root_path": "/tmp/repo",
        "entity_total": 2,
        "entity_count": 2,
        "entities": [
            {
                "kind": "tool",
                "entity_id": "cyt_mcp:context-mode_ctx_upgrade",
                "mcp_server": "context-mode",
                "base_tier": "T2",
                "effective_tier": "T2",
                "policy": "tier_active",
                "stats": {"candidates": 3},
            },
            {
                "kind": "tool",
                "entity_id": "cyt_mcp:context-mode_ctx_execute",
                "mcp_server": "context-mode",
                "base_tier": "T2",
                "effective_tier": "T2",
            },
        ],
    }
    text = format_status_text(
        payload,
        filters=StatusFilters(kind="tools", server="context-mode"),
    )
    assert "=== tools ===" in text
    assert "-- Effective T2 --" in text
    assert "  -- Base T2 --" in text
    assert "    context-mode_ctx_upgrade" in text
    assert "    context-mode_ctx_execute" in text
    assert "[tool]" not in text
    assert "policy:" not in text
    assert "stats:" not in text


def test_apply_status_view_adds_entities_and_filters() -> None:
    overview = apply_status_view(_sample_payload(), StatusFilters())
    assert overview["mode"] == "overview"
    assert "entity_total" not in overview

    payload = apply_status_view(_sample_payload(), StatusFilters(kind="skills"))
    assert payload["mode"] == "filtered"
    assert payload["entity_total"] == 2
    assert payload["entity_count"] == 1
    assert payload["filters"] == {"kind": "skills"}
    assert "overview" not in payload
    assert "tools" not in payload
    assert "skills" not in payload
    assert "histogram" not in payload
    assert payload["entities"][0]["name"] == "explore"
    assert "tier_bucket" not in payload["entities"][0]

    filtered = apply_status_view(_sample_payload(), StatusFilters(kind="skills", name="explore"))
    assert filtered["entity_count"] == 1
    assert filtered["entity_total"] == 2
    assert filtered["filters"] == {"kind": "skills", "name": "explore"}


def test_apply_status_view_filters_json_blocks_by_name() -> None:
    payload = apply_status_view(
        _sample_payload(),
        StatusFilters(name="search"),
    )
    assert payload["mode"] == "filtered"
    assert payload["entity_count"] == 1
    assert payload["entities"][0]["entity_id"] == "cyt_mcp:search"
    assert "skills" not in payload
    assert "tools" not in payload
    assert "histogram" not in payload
    assert "tier_bucket" not in payload["entities"][0]


def test_format_entity_detail() -> None:
    entity = flatten_status_entities(_sample_payload())[1]
    text = format_entity_detail(entity)
    assert "[tool] search" in text
    assert "effective=T3" in text
    assert "stats:" in text
    assert "scores:" in text
    assert "high_demand" in text


def test_format_entity_detail_shows_tool_entity_id() -> None:
    entity = {
        "kind": "tool",
        "entity_id": "mcpc:@fff/grep",
        "effective_tier": "T1",
        "base_tier": "T1",
        "scope": "workspace",
        "source_path": "/tmp/mcp-config.yaml",
        "source_line": 25,
        "catalog_source": "mcpc",
        "mcp_server": "fff",
    }
    text = format_entity_detail(entity)
    assert "entity_id: mcpc:@fff/grep" in text
    assert "unknown:" not in text
    assert "scope: workspace" in text
    assert "source_path: /tmp/mcp-config.yaml:L25" in text
    assert "catalog_source: mcpc" in text
    assert "mcp_server: fff" in text


def test_format_status_text_drill_down_shows_grouped_summaries() -> None:
    base = {**_sample_payload(), "project_id": 13, "root_path": "/tmp/repo", "epoch_id": 1}
    skills_view = apply_status_view(base, StatusFilters(kind="skills"))
    text = format_status_text(skills_view, filters=StatusFilters(kind="skills"))
    assert "=== tools ===" not in text
    assert "=== skills ===" in text
    assert "-- Effective T1 --" in text
    assert "    explore" in text
    assert "policy:" not in text
    assert "stats:" not in text

    tools_view = apply_status_view(base, StatusFilters(kind="tools"))
    text = format_status_text(tools_view, filters=StatusFilters(kind="tools"))
    assert "project_id: 13" in text
    assert "=== tools ===" in text
    assert "-- Effective T3 --" in text
    assert "  -- Base T2 --" in text
    assert "    search" in text


def test_format_status_text_separates_effective_tier_sections() -> None:
    payload = {
        "project_id": 13,
        "root_path": "/tmp/repo",
        "entities": [
            {
                "kind": "skill",
                "entity_id": "skill:explain-simply",
                "name": "explain-simply",
                "base_tier": "T0",
                "effective_tier": "T0",
            },
            {
                "kind": "skill",
                "entity_id": "skill:create-hook",
                "name": "create-hook",
                "base_tier": "T1",
                "effective_tier": "T1",
            },
            {
                "kind": "skill",
                "entity_id": "skill:local-search",
                "name": "local-search",
                "base_tier": "T2",
                "effective_tier": "T2",
            },
        ],
    }
    text = format_status_text(payload, filters=StatusFilters(kind="skills"))
    assert "\n-- Effective T1 --" in text
    assert "explain-simply\n\n-- Effective T1 --" in text
    assert "create-hook\n\n-- Effective T2 --" in text


def test_format_status_text_groups_by_base_tier_under_effective() -> None:
    payload = {
        "project_id": 13,
        "root_path": "/tmp/repo",
        "entities": [
            {
                "kind": "skill",
                "entity_id": "skill:create-hook",
                "name": "create-hook",
                "base_tier": "T2",
                "effective_tier": "T2",
            },
            {
                "kind": "skill",
                "entity_id": "skill:local-search",
                "name": "local-search",
                "base_tier": "T1",
                "effective_tier": "T2",
            },
        ],
    }
    text = format_status_text(payload, filters=StatusFilters(kind="skills"))
    effective_idx = text.index("-- Effective T2 --")
    base_t1_idx = text.index("  -- Base T1 --")
    base_t2_idx = text.index("  -- Base T2 --")
    local_search_idx = text.index("    local-search")
    create_hook_idx = text.index("    create-hook")
    assert effective_idx < base_t1_idx < local_search_idx < base_t2_idx < create_hook_idx


def test_format_status_text_name_filter_shows_detail() -> None:
    payload = {
        "project_id": 13,
        "root_path": "/tmp/repo",
        "epoch_id": 1,
        "entity_total": 2,
        "entity_count": 1,
        "entities": [flatten_status_entities(_sample_payload())[1]],
    }
    text = format_status_text(payload, filters=StatusFilters(name="search"))
    assert "stats:" in text
    assert "scores:" in text
    assert "[tool] search" in text


def test_format_status_text_verbose_includes_entities() -> None:
    payload = {
        "project_id": 13,
        "root_path": "/tmp/repo",
        "epoch_id": 1,
        "entity_total": 2,
        "entity_count": 2,
        "entities": flatten_status_entities(_sample_payload()),
    }
    text = format_status_text(payload, filters=StatusFilters(kind="tools"), verbose=True)
    assert "epoch_id:" in text
    assert "[tool] search" in text
    assert "stats:" in text
