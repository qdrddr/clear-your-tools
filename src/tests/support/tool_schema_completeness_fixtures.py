"""Fixtures for tool schema completeness regression tests."""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
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
PROPAGATION_CONTRACT_PATH = FIXTURES_DIR / "propagation_contract.json"
BM25_CATALOG_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "cyt_mcp_catalog" / "input" / "tools.json"
)
DUAL_SCHEMA_TOOLS_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "dual_schema_injection" / "tools.json"
)


@dataclass(frozen=True)
class SchemaCompletenessScenario:
    id: str
    description: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class PropagationReferenceTool:
    id: str
    catalog_source: str
    required: list[str]
    optional: list[str]
    server_key: str
    tool_name: str
    forbidden_on_other_tools: list[str]


@dataclass(frozen=True)
class PropagationPipelineScenario:
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


def load_propagation_contract() -> dict[str, Any]:
    payload = json.loads(PROPAGATION_CONTRACT_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{PROPAGATION_CONTRACT_PATH}: expected object")
    return payload


def load_propagation_reference_tools() -> list[PropagationReferenceTool]:
    payload = load_propagation_contract()
    tools_raw = payload.get("reference_tools")
    if not isinstance(tools_raw, list):
        raise ValueError("propagation_contract.json: expected reference_tools array")
    loaded: list[PropagationReferenceTool] = []
    for item in tools_raw:
        if not isinstance(item, dict):
            continue
        tool_id = str(item.get("id") or "").strip()
        if not tool_id:
            continue
        loaded.append(
            PropagationReferenceTool(
                id=tool_id,
                catalog_source=str(item.get("catalog_source") or ""),
                required=[str(name) for name in item.get("required") or []],
                optional=[str(name) for name in item.get("optional") or []],
                server_key=str(item.get("server_key") or ""),
                tool_name=str(item.get("tool_name") or ""),
                forbidden_on_other_tools=[
                    str(name) for name in item.get("forbidden_on_other_tools") or []
                ],
            ),
        )
    return loaded


def load_propagation_reference_tool(tool_id: str) -> PropagationReferenceTool:
    for tool in load_propagation_reference_tools():
        if tool.id == tool_id:
            return tool
    raise KeyError(f"no propagation reference tool for id {tool_id!r}")


def load_propagation_pipeline_scenarios() -> list[PropagationPipelineScenario]:
    payload = load_propagation_contract()
    scenarios_raw = payload.get("pipeline_scenarios")
    if not isinstance(scenarios_raw, list):
        raise ValueError("propagation_contract.json: expected pipeline_scenarios array")
    loaded: list[PropagationPipelineScenario] = []
    for item in scenarios_raw:
        if not isinstance(item, dict):
            continue
        scenario_id = str(item.get("id") or "").strip()
        if not scenario_id:
            continue
        loaded.append(
            PropagationPipelineScenario(
                id=scenario_id,
                description=str(item.get("description") or ""),
                raw=dict(item),
            ),
        )
    return loaded


def load_propagation_pipeline_scenario(scenario_id: str) -> PropagationPipelineScenario:
    for scenario in load_propagation_pipeline_scenarios():
        if scenario.id == scenario_id:
            return scenario
    raise KeyError(f"no propagation pipeline scenario for id {scenario_id!r}")


def load_pipeline_scenario(scenario_id: str) -> PropagationPipelineScenario:
    """Alias for :func:`load_propagation_pipeline_scenario` (contract fixture API)."""
    return load_propagation_pipeline_scenario(scenario_id)


def load_type2_expectations() -> list[dict[str, Any]]:
    payload = load_propagation_contract()
    rows = payload.get("type2_expectations")
    if not isinstance(rows, list):
        raise ValueError("propagation_contract.json: expected type2_expectations array")
    return [dict(item) for item in rows if isinstance(item, dict)]


def load_tier_identity_expectations() -> list[dict[str, Any]]:
    payload = load_propagation_contract()
    rows = payload.get("tier_identity_expectations")
    if not isinstance(rows, list):
        raise ValueError("propagation_contract.json: expected tier_identity_expectations array")
    return [dict(item) for item in rows if isinstance(item, dict)]


def load_identity_pipeline_modules() -> list[dict[str, Any]]:
    payload = load_propagation_contract()
    rows = payload.get("identity_pipeline_modules")
    if not isinstance(rows, list):
        raise ValueError("propagation_contract.json: expected identity_pipeline_modules array")
    return [dict(item) for item in rows if isinstance(item, dict)]


def load_session_log_identity_contract() -> dict[str, Any]:
    payload = load_propagation_contract()
    block = payload.get("session_log_identity")
    if not isinstance(block, dict):
        raise ValueError("propagation_contract.json: expected session_log_identity object")
    return dict(block)


def load_session_log_writer_scenarios() -> list[dict[str, Any]]:
    block = load_session_log_identity_contract()
    rows = block.get("writer_scenarios")
    if not isinstance(rows, list):
        raise ValueError("session_log_identity: expected writer_scenarios array")
    return [dict(item) for item in rows if isinstance(item, dict)]


def load_dual_schema_catalog_tools() -> list[dict[str, Any]]:
    return load_tool_list(DUAL_SCHEMA_TOOLS_PATH)


def resolve_reference_tool_from_catalogs(tool_id: str) -> dict[str, Any]:
    ref = load_propagation_reference_tool(tool_id)
    if ref.catalog_source == "dual_schema":
        catalog = load_dual_schema_catalog_tools()
    else:
        catalog = load_bm25_catalog_tools()
    for tool in catalog:
        if str(tool.get("name") or "") == tool_id:
            return dict(tool)
    raise KeyError(f"tool {tool_id!r} not found in {ref.catalog_source} catalog")


def master_catalog_for_pipeline_source(source: str) -> list[dict[str, Any]]:
    if source == "partial_ws_registry":
        return load_tool_list(PARTIAL_WS_REGISTRY_PATH)
    if source == "full_ws_disk":
        return load_tool_list(FULL_WS_DISK_CATALOG_PATH)
    if source == "dual_schema":
        return load_dual_schema_catalog_tools()
    if source == "bm25_subset":
        names = {"fff_grep", "gitnexus_cypher", "semble_search"}
        return [tool for tool in load_bm25_catalog_tools() if str(tool.get("name") or "") in names]
    raise ValueError(f"unknown master_catalog_source {source!r}")


def parse_session_jsonl(path: Path) -> dict[str, Any]:
    """Parse session JSONL into Type-2 catalogs and Type-1 tool entries."""
    type2_by_catalog: dict[str, list[dict[str, Any]]] = {}
    type1_by_name: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return {"type2_by_catalog": type2_by_catalog, "type1_by_name": type1_by_name}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or "")
        if kind == "tool_catalog":
            catalog = str(entry.get("catalog") or "executor")
            tools_raw = entry.get("tools")
            tools = tools_raw if isinstance(tools_raw, list) else []
            type2_by_catalog[catalog] = [dict(item) for item in tools if isinstance(item, dict)]
        elif kind == "tool":
            name = str(entry.get("name") or "").strip()
            if name:
                type1_by_name[name] = dict(entry)
    return {"type2_by_catalog": type2_by_catalog, "type1_by_name": type1_by_name}


def parse_session_log_entries(entries: Sequence[Any]) -> dict[str, Any]:
    """Parse in-memory session log entry list (hook details session_log)."""
    type2_by_catalog: dict[str, list[dict[str, Any]]] = {}
    type1_by_name: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or "")
        if kind == "tool_catalog":
            catalog = str(entry.get("catalog") or "executor")
            tools_raw = entry.get("tools")
            tools = tools_raw if isinstance(tools_raw, list) else []
            type2_by_catalog[catalog] = [dict(item) for item in tools if isinstance(item, dict)]
        elif kind == "tool":
            name = str(entry.get("name") or "").strip()
            if name:
                type1_by_name[name] = dict(entry)
    return {"type2_by_catalog": type2_by_catalog, "type1_by_name": type1_by_name}


def tool_record_from_type2(type2_tools: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for tool in type2_tools:
        if str(tool.get("name") or "") == name:
            return dict(tool)
    raise KeyError(f"tool {name!r} not found in Type-2 catalog")


def extract_injection_block(stdout: str, tool_name: str) -> str:
    marker = f"name='{tool_name}'"
    if marker not in stdout:
        raise KeyError(f"tool {tool_name!r} not found in injection stdout")
    start = stdout.index(marker)
    next_marker = stdout.find("\nname='", start + 1)
    if next_marker == -1:
        return stdout[start:]
    return stdout[start:next_marker]


def load_bm25_catalog_tools() -> list[dict[str, Any]]:
    payload = json.loads(BM25_CATALOG_PATH.read_text(encoding="utf-8"))
    tools = payload.get("tools") if isinstance(payload, dict) else payload
    if not isinstance(tools, list):
        raise ValueError(f"{BM25_CATALOG_PATH}: expected tools array")
    return [dict(tool) for tool in tools if isinstance(tool, dict)]


def multi_required_backend_tools() -> list[tuple[str, list[str]]]:
    """Return (tool_name, sorted_required_names) for catalog tools with 2+ required fields."""
    out: list[tuple[str, list[str]]] = []
    for tool in load_bm25_catalog_tools():
        schema = tool.get("input_schema")
        if not isinstance(schema, dict):
            continue
        required_raw = schema.get("required")
        if not isinstance(required_raw, list):
            continue
        properties = schema.get("properties")
        prop_names = properties if isinstance(properties, dict) else {}
        required = sorted(str(name) for name in required_raw if str(name) in prop_names)
        if len(required) >= 2:
            out.append((str(tool["name"]), required))
    return out


def partial_schema_from_backend(tool: dict[str, Any]) -> dict[str, Any]:
    """Keep only the first required property to simulate partial registry/catalog rows."""
    schema = tool.get("input_schema")
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}, "required": []}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {"type": "object", "properties": {}, "required": []}
    required_raw = schema.get("required")
    required = (
        [str(name) for name in required_raw if str(name) in properties]
        if isinstance(required_raw, list)
        else []
    )
    if not required:
        return copy.deepcopy(schema)
    keep = required[0]
    return {
        "type": schema.get("type", "object"),
        "properties": {keep: copy.deepcopy(properties[keep])},
        "required": [keep],
    }


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
