"""Fixtures for tool schema completeness regression tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cyt.cyt_mcp.catalog_disk import raw_catalog_content_hash, write_disk_catalog
from cyt.hook.catalog_registry import RegisterStatus, clear_catalog_registry, register_catalog
from cyt.hook.workspace_config import set_hook_workspace_in_config

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "tool_schema_completeness"
PARTIAL_WS_REGISTRY_PATH = FIXTURES_DIR / "partial_ws_registry.json"
FULL_WS_DISK_CATALOG_PATH = FIXTURES_DIR / "full_ws_disk_catalog.json"
SCENARIOS_PATH = FIXTURES_DIR / "scenarios.json"
BM25_CATALOG_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "cyt_mcp_catalog" / "input" / "tools.json"
)


@dataclass(frozen=True)
class SchemaCompletenessScenario:
    id: str
    description: str
    raw: dict[str, Any]


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[SchemaCompletenessScenario]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError(f"{path}: expected scenarios array")
    loaded: list[SchemaCompletenessScenario] = []
    for item in scenarios:
        if not isinstance(item, dict):
            continue
        scenario_id = str(item.get("id") or "").strip()
        if not scenario_id:
            continue
        loaded.append(
            SchemaCompletenessScenario(
                id=scenario_id,
                description=str(item.get("description") or ""),
                raw=dict(item),
            ),
        )
    return loaded


def load_scenario(scenario_id: str) -> SchemaCompletenessScenario:
    for scenario in load_scenarios():
        if scenario.id == scenario_id:
            return scenario
    raise KeyError(f"no schema completeness scenario for id {scenario_id!r}")


def load_tool_list(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tools = payload.get("tools") if isinstance(payload, dict) else payload
    if not isinstance(tools, list):
        raise ValueError(f"{path}: expected tools array")
    return [dict(tool) for tool in tools if isinstance(tool, dict)]


def load_bm25_catalog_tools() -> list[dict[str, Any]]:
    payload = json.loads(BM25_CATALOG_PATH.read_text(encoding="utf-8"))
    tools = payload.get("tools") if isinstance(payload, dict) else payload
    if not isinstance(tools, list):
        raise ValueError(f"{BM25_CATALOG_PATH}: expected tools array")
    return [dict(tool) for tool in tools if isinstance(tool, dict)]


def materialize_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    cyt_config_dir = workspace / ".agents" / "cyt" / "config"
    mcp_dir = cyt_config_dir / "mcp"
    mcp_dir.mkdir(parents=True)
    (cyt_config_dir / "config.yaml").write_text(
        "skills:\n  directories:\n  - .agents/skills\n",
        encoding="utf-8",
    )
    (mcp_dir / "cursor.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    return workspace.resolve()


def cyt_mcp_hook_config(workspace: Path, *, db_path: Path | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {
        "pruning": {
            "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
            "tools": {
                "enabled": True,
                "hook": {
                    "tools_from": ["cyt_mcp"],
                    "cyt_mcp": {"agent": "cursor"},
                },
                "sequence": ["bm25"],
            },
        },
        "skills": {"enabled": False},
        "tools": {
            "tiers": {
                "mode": "shadow",
                "database": {"path": str(db_path or workspace / "tiers.db")},
            },
        },
    }
    return set_hook_workspace_in_config(config, workspace)


def register_ws_catalog(workspace: Path, tools: list[dict[str, Any]]) -> None:
    content_hash = raw_catalog_content_hash(tools)
    result = register_catalog(
        {
            "agent": "cursor",
            "scope": "workspace",
            "workspace_root": str(workspace),
            "catalog_layer": "ws",
            "instance_id": "pid:schema-completeness",
            "content_hash": content_hash,
            "tools": tools,
        },
    )
    assert result.status == RegisterStatus.STORED


def write_full_disk_catalog(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tools: list[dict[str, Any]],
    *,
    slug: str = "cursor-workspace-test",
) -> str:
    from cyt.cyt_mcp import catalog_disk

    cache_dir = tmp_path / "cyt-mcp-catalog"
    monkeypatch.setattr(catalog_disk, "cyt_mcp_catalog_cache_dir", lambda: cache_dir)
    content_hash = raw_catalog_content_hash(tools)
    write_disk_catalog(
        slug,
        agent="cursor",
        tools=tools,
        content_hash=content_hash,
    )
    return content_hash


def reset_catalog_state() -> None:
    clear_catalog_registry(purge_disk_snapshot=True)
    from cyt.cyt_mcp.catalog import clear_cyt_mcp_catalog_cache
    from cyt.tools.master_catalog import clear_master_catalog_cache

    clear_cyt_mcp_catalog_cache()
    clear_master_catalog_cache()
