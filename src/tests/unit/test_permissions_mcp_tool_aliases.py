"""Unit tests for MCP tool name alias permission matching and editor flows."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cyt.permissions.editor import (
    disable_mcp_tool,
    enable_mcp_tool,
    load_permissions_lists,
    parse_server_tool_arg,
)
from cyt.permissions.match import (
    equivalent_mcp_tool_deny_entries,
    is_catalog_tool_denied,
    is_mcp_tool_denied,
    normalize_permission_tool_input,
)
from cyt_mcp.catalog_build import build_catalog_from_tools
from fastmcp.tools.base import Tool
from tests.support.permissions_mcp_tool_aliases_fixtures import (
    CATALOG_TOOLS_PATH,
    SCENARIOS_PATH,
    apply_alias_steps,
    assert_alias_runtime_tools,
    assert_alias_scope_deny_empty,
    effective_for_alias_pack,
    load_alias_scenarios,
    load_catalog_tools,
    materialize_alias_fixture_pack,
    patch_global_config_path,
)


@pytest.fixture
def alias_pack(tmp_path: Path):
    return materialize_alias_fixture_pack(tmp_path)


def test_alias_fixture_files_exist() -> None:
    assert SCENARIOS_PATH.is_file()
    assert CATALOG_TOOLS_PATH.is_file()
    names, scenarios = load_alias_scenarios()
    assert "hedl_batch" in names
    assert len(scenarios) >= 10


def test_normalize_permission_tool_input_strips_mcp_wire_prefix() -> None:
    assert normalize_permission_tool_input("mcp__hedl__hedl_batch") == "hedl_hedl_batch"
    assert normalize_permission_tool_input("hedl_hedl_batch") == "hedl_hedl_batch"
    assert normalize_permission_tool_input("  hedl/batch  ") == "hedl/batch"


@pytest.mark.parametrize(
    "target,expected",
    [
        ("hedl/hedl_batch", ("hedl", "hedl_batch")),
        ("hedl/batch", ("hedl", "batch")),
        ("hedl_batch", ("hedl", "batch")),
        ("hedl_hedl_batch", ("hedl", "hedl_batch")),
        ("mcp__hedl__hedl_batch", ("hedl", "hedl_batch")),
        ("hedl/hedl_read", ("hedl", "hedl_read")),
        ("hedl_hedl_read", ("hedl", "hedl_read")),
    ],
)
def test_parse_server_tool_arg_accepts_agent_visible_names(
    target: str,
    expected: tuple[str, str],
) -> None:
    assert parse_server_tool_arg(target) == expected


def test_equivalent_mcp_tool_deny_entries_for_hedl_batch() -> None:
    assert equivalent_mcp_tool_deny_entries("hedl", "batch") == frozenset(
        {"hedl/batch", "hedl/hedl_batch"},
    )
    assert equivalent_mcp_tool_deny_entries("hedl", "hedl_batch") == frozenset(
        {"hedl/hedl_batch", "hedl/batch"},
    )


def test_is_mcp_tool_denied_matches_all_hedl_batch_aliases() -> None:
    for deny in ("hedl/batch", "hedl/hedl_batch"):
        assert is_mcp_tool_denied("hedl", "batch", (deny,))
        assert is_catalog_tool_denied("hedl_batch", (deny,))
    # Agent-visible wire name matches agent-alias deny entry (catalog filter uses hedl_batch).
    assert is_catalog_tool_denied("hedl_hedl_batch", ("hedl/hedl_batch",))


def test_disable_mcp_tool_stores_deny_entry(alias_pack, tmp_path: Path) -> None:
    disable_mcp_tool(
        "hedl",
        "hedl_batch",
        scope="workspace",
        agent_target="all",
        agent="cursor",
        global_config_path=alias_pack.global_config_path,
        workspace_root=alias_pack.workspace,
    )
    raw = yaml.safe_load(alias_pack.workspace_config_path.read_text(encoding="utf-8"))
    deny, _allow = load_permissions_lists(raw, kind="mcp", agent_target="all")
    assert "hedl/hedl_batch" in deny


def test_enable_mcp_tool_removes_alias_deny_entries(alias_pack) -> None:
    disable_mcp_tool(
        "hedl",
        "batch",
        scope="workspace",
        agent_target="all",
        agent="cursor",
        global_config_path=alias_pack.global_config_path,
        workspace_root=alias_pack.workspace,
    )
    enable_mcp_tool(
        "hedl",
        "hedl_batch",
        scope="workspace",
        agent_target="all",
        agent="cursor",
        global_config_path=alias_pack.global_config_path,
        workspace_root=alias_pack.workspace,
    )
    raw = yaml.safe_load(alias_pack.workspace_config_path.read_text(encoding="utf-8"))
    deny, _allow = load_permissions_lists(raw, kind="mcp", agent_target="all")
    assert deny == []


def test_build_catalog_from_tools_respects_hedl_batch_alias_deny() -> None:
    catalog, index = build_catalog_from_tools(
        [
            Tool.from_function(lambda: None, name="hedl_batch"),
            Tool.from_function(lambda: None, name="hedl_hedl_read"),
        ],
        deny_entries=("hedl/hedl_batch",),
    )
    assert [entry["name"] for entry in catalog] == ["hedl_hedl_read"]
    assert "hedl_batch" not in index


@pytest.mark.parametrize(
    "scenario_id",
    [scenario.id for scenario in load_alias_scenarios()[1]],
    ids=[scenario.id for scenario in load_alias_scenarios()[1]],
)
def test_mcp_tool_alias_scenarios_runtime_filters(
    alias_pack,
    scenario_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, scenarios = load_alias_scenarios()
    scenario = next(item for item in scenarios if item.id == scenario_id)
    patch_global_config_path(monkeypatch, alias_pack)
    apply_alias_steps(alias_pack, scenario)
    assert_alias_runtime_tools(alias_pack, scenario, agent=scenario.agent)

    if scenario.assert_scope_deny_empty is not None:
        assert_alias_scope_deny_empty(
            alias_pack,
            scenario.assert_scope_deny_empty,
            agent=scenario.agent,
        )


@pytest.mark.parametrize(
    "scenario_id",
    [scenario.id for scenario in load_alias_scenarios()[1]],
    ids=[scenario.id for scenario in load_alias_scenarios()[1]],
)
def test_mcp_tool_alias_effective_deny_union(
    alias_pack,
    scenario_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, scenarios = load_alias_scenarios()
    scenario = next(item for item in scenarios if item.id == scenario_id)
    patch_global_config_path(monkeypatch, alias_pack)
    apply_alias_steps(alias_pack, scenario)

    effective = effective_for_alias_pack(alias_pack, agent=scenario.agent)
    for tool_name in scenario.expected.disabled_tools:
        assert is_catalog_tool_denied(tool_name, effective.mcp.deny), (
            f"{tool_name} should be denied in effective permissions"
        )
    for tool_name in scenario.expected.enabled_tools:
        assert not is_catalog_tool_denied(tool_name, effective.mcp.deny), (
            f"{tool_name} should remain enabled in effective permissions"
        )


def test_catalog_tools_fixture_matches_scenario_expectations() -> None:
    tools = load_catalog_tools()
    names = {str(tool["name"]) for tool in tools}
    _, scenarios = load_alias_scenarios()
    for scenario in scenarios:
        expected = set(scenario.expected.enabled_tools) | set(scenario.expected.disabled_tools)
        assert expected.issubset(names), (
            f"scenario {scenario.id} references tools missing from catalog fixture: "
            f"{sorted(expected - names)}"
        )
