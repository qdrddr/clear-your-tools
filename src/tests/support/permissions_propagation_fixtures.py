"""Fixture loader for permissions → hook → cyt-mcp → tier propagation scenarios."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from cyt.cyt_mcp.catalog import _filter_tools_by_permissions, apply_fetched_catalog
from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.permissions.editor import disable_mcp_tool, enable_mcp_tool, parse_server_tool_arg
from cyt.permissions.merge import merged_hook_config
from cyt.proxy.reverse import create_app
from cyt.tiers.adapters.tools import stamp_tool_catalog_source
from cyt.tiers.status_detail import filter_tool_detail_by_permissions
from cyt.tools.master_catalog import rebuild_master_catalog
from tests.support.permissions_gate_fixtures import patch_global_config_path

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "permissions_propagation"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"
CATALOG_TOOLS_PATH = FIXTURES_ROOT / "catalog_tools.json"


@dataclass(frozen=True)
class PropagationScenario:
    id: str
    disable_target: str
    denied_catalog_name: str
    enabled_catalog_names: frozenset[str]


@dataclass(frozen=True)
class PropagationFixturePack:
    workspace: Path
    global_config_path: Path
    workspace_config_path: Path
    tools: list[dict[str, Any]]
    catalog_tool_names: tuple[str, ...]
    scenarios: tuple[PropagationScenario, ...]


def load_catalog_tools(
    path: Path = CATALOG_TOOLS_PATH,
) -> tuple[tuple[str, ...], list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    names_raw = payload.get("catalog_tool_names")
    tools_raw = payload.get("tools")
    if not isinstance(names_raw, list):
        raise ValueError(f"{path}: expected catalog_tool_names array")
    if not isinstance(tools_raw, list):
        raise ValueError(f"{path}: expected tools array")
    names = tuple(str(name) for name in names_raw)
    tools = [dict(item) for item in tools_raw if isinstance(item, dict)]
    return names, tools


def load_propagation_scenarios(path: Path = SCENARIOS_PATH) -> tuple[PropagationScenario, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenarios_raw = payload.get("scenarios")
    if not isinstance(scenarios_raw, list):
        raise ValueError(f"{path}: expected scenarios array")
    scenarios: list[PropagationScenario] = []
    for row in scenarios_raw:
        if not isinstance(row, dict):
            continue
        enabled_raw = row.get("enabled_catalog_names")
        if not isinstance(enabled_raw, list):
            raise ValueError(f"{path}: scenario {row.get('id')!r} missing enabled_catalog_names")
        scenarios.append(
            PropagationScenario(
                id=str(row["id"]),
                disable_target=str(row["disable_target"]),
                denied_catalog_name=str(row["denied_catalog_name"]),
                enabled_catalog_names=frozenset(str(name) for name in enabled_raw),
            ),
        )
    return tuple(scenarios)


def materialize_propagation_fixture_pack(tmp_path: Path) -> PropagationFixturePack:
    catalog_tool_names, tools = load_catalog_tools()
    scenarios = load_propagation_scenarios()
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

    return PropagationFixturePack(
        workspace=workspace,
        global_config_path=global_config_path,
        workspace_config_path=workspace_config_path,
        tools=tools,
        catalog_tool_names=catalog_tool_names,
        scenarios=scenarios,
    )


def cyt_mcp_catalog_tools(pack: PropagationFixturePack) -> list[dict[str, Any]]:
    return [
        stamp_tool_catalog_source({**tool, "cyt_catalog_source": "cyt_mcp"}) for tool in pack.tools
    ]


def tier_config_for_pack(pack: PropagationFixturePack, agent: str = "cursor") -> dict[str, Any]:
    global_cfg: dict[str, Any] = {}
    merged = merged_hook_config(agent, global_config=global_cfg, workspace_root=pack.workspace)
    merged_tools = merged.get("tools")
    tools_section: dict[str, Any] = dict(merged_tools) if isinstance(merged_tools, dict) else {}
    return set_hook_workspace_in_config(
        {
            **merged,
            "tiers": {
                "enabled": True,
                "database": {"path": str(pack.workspace / ".agents" / "cyt" / "tier_state.db")},
            },
            "tools": {
                **tools_section,
                "hook": {"sources": ["cyt_mcp"]},
            },
        },
        pack.workspace,
    )


def seed_catalog_on_hook(
    pack: PropagationFixturePack,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    catalog_tools = cyt_mcp_catalog_tools(pack)
    apply_fetched_catalog(config, catalog_tools)
    rebuild_master_catalog(config, blocking=True)
    return catalog_tools


def disable_tool_on_pack(
    pack: PropagationFixturePack,
    target: str,
    *,
    agent: str = "cursor",
) -> None:
    server, tool = parse_server_tool_arg(target, agent=agent)
    disable_mcp_tool(
        server,
        tool,
        scope="workspace",
        agent_target="all",
        agent=agent,
        global_config_path=pack.global_config_path,
        workspace_root=pack.workspace,
    )


def enable_tool_on_pack(
    pack: PropagationFixturePack,
    target: str,
    *,
    agent: str = "cursor",
) -> None:
    server, tool = parse_server_tool_arg(target, agent=agent)
    enable_mcp_tool(
        server,
        tool,
        scope="workspace",
        agent_target="all",
        agent=agent,
        global_config_path=pack.global_config_path,
        workspace_root=pack.workspace,
    )


def assert_catalog_and_detail_filters_agree(
    *,
    config: dict[str, Any],
    pack: PropagationFixturePack,
    enabled_names: set[str] | frozenset[str],
    denied_name: str | None = None,
    agent: str = "cursor",
) -> None:
    catalog_names = {
        str(tool["name"])
        for tool in _filter_tools_by_permissions(config, cyt_mcp_catalog_tools(pack))
    }
    if denied_name:
        assert denied_name not in catalog_names
    assert catalog_names == set(enabled_names)

    detail_entities: list[dict[str, str]] = [
        {"entity_id": f"cyt_mcp:{name}", "display_name": name} for name in sorted(enabled_names)
    ]
    if denied_name:
        detail_entities.insert(
            0,
            {"entity_id": f"cyt_mcp:{denied_name}", "display_name": denied_name},
        )
    filtered_detail = filter_tool_detail_by_permissions(
        {"by_tier": {"T4": detail_entities}},
        agent=agent,
        workspace_root=pack.workspace,
    )
    detail_names = {
        str(row.get("display_name") or row.get("name") or "")
        for rows in filtered_detail.get("by_tier", {}).values()
        if isinstance(rows, list)
        for row in rows
        if isinstance(row, dict)
    }
    if denied_name:
        assert denied_name not in detail_names
    assert detail_names == set(enabled_names)


@pytest.fixture
def propagation_pack(tmp_path: Path) -> PropagationFixturePack:
    return materialize_propagation_fixture_pack(tmp_path)


@pytest.fixture
async def propagation_hook_client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        routes={},
        config={
            "skills": {"enabled": False},
            "pruning": {"inject_via": {"cursor": "hook", "claude": "proxy", "codex": "proxy"}},
        },
    )
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def notify_hook_permissions_changed(
    hook_client: httpx.AsyncClient,
    *,
    workspace_root: Path,
    agent: str = "cursor",
) -> int:
    response = await hook_client.post(
        "/hook/permissions/changed",
        json={"workspace_root": str(workspace_root), "agent": agent},
    )
    assert response.status_code == 200
    payload = response.json()
    revision = payload.get("permissions_revision")
    assert isinstance(revision, int)
    return revision


def tool_entity_ids_in_status(status: dict[str, Any]) -> set[str]:
    tools = status.get("tools")
    if not isinstance(tools, dict):
        return set()
    ids: set[str] = set()
    by_tier = tools.get("by_tier")
    if not isinstance(by_tier, dict):
        return ids
    for rows in by_tier.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                entity_id = str(row.get("entity_id") or "").strip()
                if entity_id:
                    ids.add(entity_id)
    return ids


__all__ = [
    "CATALOG_TOOLS_PATH",
    "FIXTURES_ROOT",
    "SCENARIOS_PATH",
    "PropagationFixturePack",
    "PropagationScenario",
    "assert_catalog_and_detail_filters_agree",
    "cyt_mcp_catalog_tools",
    "disable_tool_on_pack",
    "enable_tool_on_pack",
    "load_catalog_tools",
    "load_propagation_scenarios",
    "materialize_propagation_fixture_pack",
    "notify_hook_permissions_changed",
    "patch_global_config_path",
    "propagation_hook_client",
    "propagation_pack",
    "seed_catalog_on_hook",
    "tier_config_for_pack",
    "tool_entity_ids_in_status",
]
