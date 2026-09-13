"""Integration tests: permissions gate through registry, catalog, inventory, interceptor."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from cyt.cyt_mcp.catalog import _filter_tools_by_permissions
from cyt.permissions.inventory.mcp import list_mcp_servers, list_mcp_tools_for_server
from cyt.permissions.inventory.skills import list_skills
from cyt.permissions.runtime import skill_policy_name
from cyt.skills.agent_interceptor import run_skill_read_intercept
from cyt.skills.catalog import build_registry, clear_registry_cache
from tests.conftest import isolate_user_home
from tests.support.permissions_gate_fixtures import (
    ALL_SKILL_NAMES,
    PermissionsGateFixturePack,
    apply_steps,
    expected_mcp_server_states,
    load_scenarios,
    materialize_fixture_pack,
    merged_config_for_pack,
    patch_global_config_path,
    skill_markdown_path,
)


@pytest.fixture
def fixture_pack(tmp_path: Path) -> PermissionsGateFixturePack:
    return materialize_fixture_pack(tmp_path)


def _configure_global_skills_dirs(pack: PermissionsGateFixturePack) -> None:
    raw = yaml.safe_load(pack.global_config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raw = {}
    raw.setdefault("skills", {})
    if isinstance(raw["skills"], dict):
        raw["skills"]["directories"] = [str(pack.skills_dir)]
        raw["skills"]["enabled"] = True
    pack.global_config_path.write_text(
        yaml.dump(raw, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario_id",
    [scenario.id for scenario in load_scenarios()[1]],
    ids=[scenario.id for scenario in load_scenarios()[1]],
)
def test_permissions_gate_registry_and_catalog(
    fixture_pack: PermissionsGateFixturePack,
    scenario_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, scenarios = load_scenarios()
    scenario = next(item for item in scenarios if item.id == scenario_id)
    patch_global_config_path(monkeypatch, fixture_pack)
    _configure_global_skills_dirs(fixture_pack)
    isolate_user_home(monkeypatch, fixture_pack.workspace / "home")
    apply_steps(fixture_pack, scenario)

    config = merged_config_for_pack(fixture_pack, agent=scenario.agent)
    clear_registry_cache()
    entries = build_registry(config, agent=scenario.agent)
    registry_names = {skill_policy_name(entry) for entry in entries}
    assert registry_names == set(scenario.expected.enabled_skills)

    filtered_tools = _filter_tools_by_permissions(config, list(fixture_pack.tools))
    filtered_names = {str(tool["name"]) for tool in filtered_tools}
    assert filtered_names == set(scenario.expected.enabled_tools)


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario_id",
    [scenario.id for scenario in load_scenarios()[1]],
    ids=[scenario.id for scenario in load_scenarios()[1]],
)
def test_permissions_gate_inventory_lists(
    fixture_pack: PermissionsGateFixturePack,
    scenario_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, scenarios = load_scenarios()
    scenario = next(item for item in scenarios if item.id == scenario_id)
    patch_global_config_path(monkeypatch, fixture_pack)
    _configure_global_skills_dirs(fixture_pack)
    apply_steps(fixture_pack, scenario)

    global_cfg = yaml.safe_load(fixture_pack.global_config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(global_cfg, dict):
        global_cfg = {}

    fixture_skill_names = set(ALL_SKILL_NAMES)

    def _isolated_skill_directories(
        cfg: dict,
        *,
        agent: str | None = None,
        workspace_root: Path | None = None,
        include_platform_defaults: bool = False,
    ) -> list[Path]:
        del cfg, agent, workspace_root, include_platform_defaults
        return [fixture_pack.skills_dir]

    with (
        patch(
            "cyt.skills.directories.resolve_skill_directories",
            side_effect=_isolated_skill_directories,
        ),
        patch(
            "cyt.permissions.inventory.mcp.mcp_server_defs_path",
            side_effect=lambda *, agent, scope, workspace_root=None: fixture_pack.mcp_defs_path,
        ),
    ):
        enabled_skills, disabled_skills = list_skills(
            agent=scenario.agent,
            scope="effective",
            workspace_root=fixture_pack.workspace,
            global_config=global_cfg,
        )
        enabled_mcp_servers, disabled_mcp_servers = list_mcp_servers(
            agent=scenario.agent,
            policy_agent=scenario.agent,
            workspace_root=fixture_pack.workspace,
        )

    enabled_names = {item.name for item in enabled_skills if item.name in fixture_skill_names}
    disabled_names = {item.name for item in disabled_skills if item.name in fixture_skill_names}
    assert enabled_names == set(scenario.expected.enabled_skills)
    assert disabled_names == set(scenario.expected.disabled_skills)

    expected_enabled_servers, expected_disabled_servers = expected_mcp_server_states(
        fixture_pack,
        scenario,
    )
    assert {item.name for item in enabled_mcp_servers} == expected_enabled_servers
    assert {item.name for item in disabled_mcp_servers} == expected_disabled_servers

    enabled_tool_names: set[str] = set()
    disabled_tool_names: set[str] = set()
    for server in ("context-mode", "gitnexus"):
        enabled_tools, disabled_tools = list_mcp_tools_for_server(
            server,
            agent=scenario.agent,
            scope="effective",
            workspace_root=fixture_pack.workspace,
            policy_agent=scenario.agent,
            catalog_tools=fixture_pack.tools,
        )
        enabled_tool_names.update(item.catalog_name for item in enabled_tools)
        disabled_tool_names.update(item.catalog_name for item in disabled_tools)

    assert enabled_tool_names == set(scenario.expected.enabled_tools)
    assert disabled_tool_names == set(scenario.expected.disabled_tools)


@pytest.mark.integration
def test_permissions_gate_interceptor_denies_disabled_skill(
    fixture_pack: PermissionsGateFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, scenarios = load_scenarios()
    scenario = next(item for item in scenarios if item.id == "workspace_disable_skill_create_hook")
    patch_global_config_path(monkeypatch, fixture_pack)
    _configure_global_skills_dirs(fixture_pack)
    apply_steps(fixture_pack, scenario)

    skill_md = skill_markdown_path(fixture_pack, "create-hook")
    config = merged_config_for_pack(fixture_pack, agent=scenario.agent)
    payload = {
        "cyt_intercept_read_path": str(skill_md),
        "cyt_intercept_query": "how to use",
        "cwd": str(fixture_pack.workspace),
        "cyt_agent": scenario.agent,
    }

    with patch("cyt.skills.agent_interceptor.skills_enabled", return_value=True):
        result = run_skill_read_intercept(payload, config)

    assert result["permission"] == "deny"
