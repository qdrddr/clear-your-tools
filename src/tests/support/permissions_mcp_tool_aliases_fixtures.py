"""Fixture loader for MCP tool name alias permission scenarios (hedl_batch family)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from cyt.common.agents import AgentName
from cyt.config import save_user_config
from cyt.permissions.editor import (
    disable_mcp_tool,
    enable_mcp_tool,
    load_permissions_lists,
    parse_server_tool_arg,
)
from cyt.permissions.merge import effective_permissions
from cyt.permissions.paths import permissions_config_path
from cyt.permissions.runtime import filter_catalog_tool_dicts
from cyt.permissions.schema import EffectivePermissions
from tests.support.permissions_gate_fixtures import (
    PermissionScenario,
    PermissionStep,
    ScopeDenyEmptyAssertion,
    load_scenarios,
    patch_global_config_path,
)

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "permissions_mcp_tool_aliases"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"
CATALOG_TOOLS_PATH = FIXTURES_ROOT / "catalog_tools.json"


@dataclass(frozen=True)
class McpToolAliasFixturePack:
    workspace: Path
    global_config_path: Path
    workspace_config_path: Path
    tools: list[dict[str, Any]]
    catalog_tool_names: tuple[str, ...]
    scenarios: tuple[PermissionScenario, ...]


def load_catalog_tools(path: Path = CATALOG_TOOLS_PATH) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tools_raw = payload.get("tools")
    if not isinstance(tools_raw, list):
        raise ValueError(f"{path}: expected tools array")
    return [dict(item) for item in tools_raw if isinstance(item, dict)]


def load_alias_scenarios(
    path: Path = SCENARIOS_PATH,
) -> tuple[tuple[str, ...], tuple[PermissionScenario, ...]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    names_raw = payload.get("catalog_tool_names")
    if not isinstance(names_raw, list):
        raise ValueError(f"{path}: expected catalog_tool_names array")
    _, scenarios = load_scenarios(path)
    return tuple(str(name) for name in names_raw), scenarios


def materialize_alias_fixture_pack(tmp_path: Path) -> McpToolAliasFixturePack:
    catalog_tool_names, scenarios = load_alias_scenarios()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()

    cyt_config_dir = workspace / ".agents" / "cyt" / "config"
    cyt_config_dir.mkdir(parents=True)
    workspace_config_path = cyt_config_dir / "config.yaml"
    workspace_config_path.write_text("tools:\n  permissions:\n    deny: []\n", encoding="utf-8")

    global_config_path = tmp_path / "global" / "config.yaml"
    global_config_path.parent.mkdir(parents=True)
    global_config_path.write_text("agents: {}\n", encoding="utf-8")

    return McpToolAliasFixturePack(
        workspace=workspace,
        global_config_path=global_config_path,
        workspace_config_path=workspace_config_path,
        tools=load_catalog_tools(),
        catalog_tool_names=catalog_tool_names,
        scenarios=scenarios,
    )


def _editor_kwargs(
    pack: McpToolAliasFixturePack,
    scenario: PermissionScenario,
    step: PermissionStep,
) -> dict[str, Any]:
    return {
        "scope": step.scope,
        "agent_target": scenario.agent_target,
        "agent": scenario.agent,
        "global_config_path": pack.global_config_path,
        "workspace_root": pack.workspace,
    }


def _preseed_deny_layers(pack: McpToolAliasFixturePack, scenario: PermissionScenario) -> None:
    for layer in scenario.preseed_deny:
        path = permissions_config_path(
            layer.scope,
            agent=scenario.agent,
            global_config_path=pack.global_config_path,
            workspace_root=pack.workspace,
        )
        existing = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if not isinstance(existing, dict):
            existing = {}
        if layer.kind == "skills":
            overlay = {"skills": {"permissions": {"deny": list(layer.deny)}}}
        else:
            overlay = {"tools": {"permissions": {"deny": list(layer.deny)}}}
        save_user_config(path, overlay)


def apply_alias_step(
    pack: McpToolAliasFixturePack,
    scenario: PermissionScenario,
    step: PermissionStep,
) -> None:
    common = _editor_kwargs(pack, scenario, step)
    if step.kind != "mcp_tool":
        raise ValueError(f"unsupported step kind for alias pack: {step.kind!r}")
    server, tool = parse_server_tool_arg(step.target, agent=scenario.agent)
    if step.action == "disable":
        disable_mcp_tool(server, tool, **common)
    else:
        enable_mcp_tool(server, tool, **common)


def apply_alias_steps(pack: McpToolAliasFixturePack, scenario: PermissionScenario) -> None:
    if scenario.preseed_deny:
        _preseed_deny_layers(pack, scenario)
    for step in scenario.steps:
        apply_alias_step(pack, scenario, step)


def effective_for_alias_pack(
    pack: McpToolAliasFixturePack,
    *,
    agent: str = "cursor",
) -> EffectivePermissions:
    global_cfg = yaml.safe_load(pack.global_config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(global_cfg, dict):
        global_cfg = {}
    return effective_permissions(
        agent=agent,
        global_config=global_cfg,
        workspace_root=pack.workspace,
    )


def runtime_tool_names_for_alias_pack(
    pack: McpToolAliasFixturePack,
    *,
    agent: str = "cursor",
) -> tuple[set[str], set[str]]:
    effective = effective_for_alias_pack(pack, agent=agent)
    filtered = filter_catalog_tool_dicts(pack.tools, effective.mcp.deny)
    enabled = {str(tool["name"]) for tool in filtered}
    all_names = {str(tool["name"]) for tool in pack.tools}
    return enabled, all_names - enabled


def assert_alias_runtime_tools(
    pack: McpToolAliasFixturePack,
    scenario: PermissionScenario,
    *,
    agent: str = "cursor",
) -> None:
    enabled, disabled = runtime_tool_names_for_alias_pack(pack, agent=agent)
    expected = scenario.expected
    assert enabled == set(expected.enabled_tools), (
        f"enabled tools: got {sorted(enabled)}, expected {sorted(expected.enabled_tools)}"
    )
    assert disabled == set(expected.disabled_tools), (
        f"disabled tools: got {sorted(disabled)}, expected {sorted(expected.disabled_tools)}"
    )


def assert_alias_scope_deny_empty(
    pack: McpToolAliasFixturePack,
    assertion: ScopeDenyEmptyAssertion,
    *,
    agent: AgentName | str = "cursor",
) -> None:
    config_path = permissions_config_path(
        assertion.scope,
        agent=str(agent),
        global_config_path=pack.global_config_path,
        workspace_root=pack.workspace,
    )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raw = {}
    kind = "skills" if assertion.kind == "skills" else "mcp"
    deny, _allow = load_permissions_lists(raw, kind=kind, agent_target="all")
    assert deny == [], f"expected empty deny at {assertion.scope}/{assertion.kind}, got {deny!r}"


__all__ = [
    "CATALOG_TOOLS_PATH",
    "FIXTURES_ROOT",
    "McpToolAliasFixturePack",
    "SCENARIOS_PATH",
    "apply_alias_steps",
    "assert_alias_runtime_tools",
    "assert_alias_scope_deny_empty",
    "effective_for_alias_pack",
    "load_alias_scenarios",
    "load_catalog_tools",
    "materialize_alias_fixture_pack",
    "patch_global_config_path",
    "runtime_tool_names_for_alias_pack",
]
