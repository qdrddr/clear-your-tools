"""Regression tests for fullest input_schema resolution across catalog layers."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from cyt.cyt_mcp.catalog import (
    _enrich_tools_with_fullest_schemas,
    _fetch_catalog_from_registry,
)
from cyt.hook.catalog_registry import (
    RegisterStatus,
    _union_layer_tools,
    catalog_for_hook,
    register_catalog,
)
from cyt.injection.session_log_build import build_tool_catalog_log_entry, build_tool_log_entry
from cyt.pruners.tools_filter import filter_tools_for_query
from cyt.tiers.adapters.tools import prepare_tool_for_tier_pipeline
from cyt.tiers.manager import NoOpTierManager, _managers
from cyt.tiers.models import Tier
from cyt.tools.injection_schema import ensure_tool_injection_schema, schema_required_property_names
from cyt_mcp.catalog import catalog_payload, merge_catalog_payloads
from cyt_mcp.catalog_export import stub_dict_from_hook_tool
from cyt_mcp.config import sample_aggregator_config
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.session_runtime import MultiWorkspaceCoordinator, WorkspaceSessionRuntime
from tests.support.tool_schema_completeness_fixtures import (
    FULL_WS_DISK_CATALOG_PATH,
    PARTIAL_WS_REGISTRY_PATH,
    cyt_mcp_hook_config,
    load_bm25_catalog_tools,
    load_scenario,
    load_tool_list,
    materialize_workspace,
    multi_required_backend_tools,
    partial_schema_from_backend,
    register_ws_catalog,
    reset_catalog_state,
    write_full_disk_catalog,
)

PARTIAL_SEMBLE = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
}
FULL_SEMBLE = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "repo": {"type": "string"},
    },
    "required": ["query", "repo"],
}


@pytest.fixture(autouse=True)
def _isolate_catalog_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("cyt.hook.active_workspace.touch_active_workspace", lambda *_a, **_k: None)
    reset_catalog_state()
    _managers.clear()
    yield
    reset_catalog_state()
    _managers.clear()


def _tool_by_name(tools: list[dict], name: str) -> dict:
    return next(tool for tool in tools if tool.get("name") == name)


def test_enrich_tools_with_fullest_schemas_merges_disk_repo() -> None:
    partial_tools = load_tool_list(PARTIAL_WS_REGISTRY_PATH)
    disk_tools = load_tool_list(FULL_WS_DISK_CATALOG_PATH)
    disk_schemas = {
        str(tool["name"]): tool["input_schema"]
        for tool in disk_tools
        if isinstance(tool.get("input_schema"), dict)
    }

    enriched = _enrich_tools_with_fullest_schemas(partial_tools, disk_schemas=disk_schemas)
    semble = _tool_by_name(enriched, "semble_search")
    assert semble["input_schema"]["required"] == ["query", "repo"]
    gitnexus = _tool_by_name(enriched, "gitnexus_cypher")
    assert gitnexus["input_schema"]["required"] == ["statement"]


def test_fetch_catalog_from_registry_enriches_partial_semble_from_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = materialize_workspace(tmp_path)
    config = cyt_mcp_hook_config(workspace)
    partial_tools = load_tool_list(PARTIAL_WS_REGISTRY_PATH)
    full_disk_tools = load_tool_list(FULL_WS_DISK_CATALOG_PATH)
    register_ws_catalog(workspace, partial_tools)
    write_full_disk_catalog(monkeypatch, tmp_path, full_disk_tools)

    fetched = _fetch_catalog_from_registry(config, allow_stale=True)
    semble = _tool_by_name(fetched, "semble_search")
    assert set(semble["input_schema"]["required"]) == {"query", "repo"}


def test_union_layer_tools_keeps_fullest_schema_on_layer_conflict() -> None:
    ws_tools = [
        {
            "name": "semble_search",
            "input_schema": copy.deepcopy(PARTIAL_SEMBLE),
            "cyt_catalog_scope": "workspace",
        },
    ]
    usr_tools = [
        {
            "name": "semble_search",
            "input_schema": copy.deepcopy(FULL_SEMBLE),
            "cyt_catalog_scope": "user",
        },
    ]
    merged = _union_layer_tools(usr_tools, ws_tools)
    assert len(merged) == 1
    semble = merged[0]
    assert semble["cyt_catalog_scope"] == "user"
    assert set(semble["input_schema"]["required"]) == {"query", "repo"}


def test_union_layer_tools_does_not_add_repo_to_query_only_tools() -> None:
    ws_tools = [
        {
            "name": "fff_grep",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    ]
    usr_tools = [
        {
            "name": "fff_grep",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    ]
    merged = _union_layer_tools(usr_tools, ws_tools)
    grep = merged[0]
    assert grep["input_schema"]["required"] == ["query"]
    assert "repo" not in grep["input_schema"].get("properties", {})


def test_build_tool_catalog_log_entry_uses_fullest_schema_among_peers() -> None:
    tools = [
        {
            "name": "semble_search",
            "server_key": "semble",
            "tool_name": "search",
            "input_schema": copy.deepcopy(PARTIAL_SEMBLE),
        },
        {
            "name": "semble_search",
            "server_key": "semble",
            "tool_name": "search",
            "input_schema": copy.deepcopy(FULL_SEMBLE),
        },
    ]
    entry = build_tool_catalog_log_entry("cyt_mcp", tools)
    semble = _tool_by_name(entry["tools"], "semble_search")
    assert set(semble["input_schema"]["required"]) == {"query", "repo"}


def test_prune_semble_includes_repo_when_master_catalog_is_full(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario("prune_preserves_single_required_tools")
    tools = load_bm25_catalog_tools()
    index_dir = str(tmp_path / "bm25")
    config = {
        "tools": {
            "enabled": True,
            "tiers": {
                "mode": "off",
                "database": {"path": str(tmp_path / "tier_state.db")},
            },
            "sequence": ["bm25"],
            "policy": {
                "system_tool": "prune_optional",
                "mcp_tool": "prune_all",
                "minimum_tools": 5,
            },
            "pipelines": {
                "bm25": {
                    "index_dir": index_dir,
                    "score_tool": 0.0,
                    "score_tool_enum": 0.0,
                    "prune_enums": False,
                },
            },
        },
        "models": {
            "bm25": {
                "index_dir": index_dir,
                "mmap": False,
                "stem_language": "english",
                "stopwords": "en",
            },
        },
    }
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "cyt.tiers.manager.get_tier_manager",
        lambda *_args, **_kwargs: NoOpTierManager(),
    )

    semble_result = filter_tools_for_query(
        copy.deepcopy(tools),
        "Locate primary code implementing BM25, use MCP: semble_search gitnexus_cypher",
        ["bm25"],
        config=config,
        for_hook=True,
        catalog_bulk_id="cyt_mcp",
    )
    assert semble_result.tools is not None
    semble = _tool_by_name(semble_result.tools, "semble_search")
    assert set(semble["input_schema"]["required"]) == {"query", "repo"}

    grep_result = filter_tools_for_query(
        copy.deepcopy(tools),
        str(scenario.raw["query"]),
        ["bm25"],
        config=config,
        for_hook=True,
        catalog_bulk_id="cyt_mcp",
    )
    assert grep_result.tools is not None
    grep = _tool_by_name(grep_result.tools, str(scenario.raw["tool_name"]))
    assert grep["input_schema"]["required"] == scenario.raw["expected_required"]
    for forbidden in scenario.raw.get("forbidden_properties", []):
        assert forbidden not in grep["input_schema"].get("properties", {})


def test_prune_jcodemunch_keeps_repo_and_query_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario("prune_preserves_dual_required_jcodemunch")
    tools = load_bm25_catalog_tools()
    index_dir = str(tmp_path / "bm25")
    config = {
        "tools": {
            "enabled": True,
            "tiers": {"mode": "off", "database": {"path": str(tmp_path / "tier_state.db")}},
            "sequence": ["bm25"],
            "policy": {
                "system_tool": "prune_optional",
                "mcp_tool": "prune_all",
                "minimum_tools": 5,
            },
            "pipelines": {
                "bm25": {
                    "index_dir": index_dir,
                    "score_tool": 0.0,
                    "score_tool_enum": 0.0,
                    "prune_enums": False,
                },
            },
        },
        "models": {
            "bm25": {
                "index_dir": index_dir,
                "mmap": False,
                "stem_language": "english",
                "stopwords": "en",
            },
        },
    }
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "cyt.tiers.manager.get_tier_manager",
        lambda *_args, **_kwargs: NoOpTierManager(),
    )

    result = filter_tools_for_query(
        copy.deepcopy(tools),
        str(scenario.raw["query"]),
        ["bm25"],
        config=config,
        for_hook=True,
        catalog_bulk_id="cyt_mcp",
    )
    assert result.tools is not None
    tool = _tool_by_name(result.tools, str(scenario.raw["tool_name"]))
    assert set(tool["input_schema"]["required"]) == set(scenario.raw["expected_required"])


@pytest.mark.asyncio
async def test_create_workspace_runtime_schedules_force_catalog_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    config = sample_aggregator_config(
        catalog_scope="workspace",
        workspace_root=workspace,
        mcp_servers={"semble": {}, "fff": {}},
    )
    cache = RuntimeToolCache()
    cache.replace([{"name": "semble_search", "input_schema": PARTIAL_SEMBLE}])
    bootstrap = WorkspaceSessionRuntime(
        workspace_root=workspace,
        config=config,
        cache=cache,
        config_holder=ConfigHolder(config),
    )
    server = MagicMock()
    coordinator = MultiWorkspaceCoordinator(
        server,
        bootstrap,
        agent=config.agent,
        aggregator_path=None,
    )

    refresh_calls: list[dict] = []

    async def _track_refresh(*_args: object, **kwargs: object) -> None:
        refresh_calls.append(dict(kwargs))

    monkeypatch.setattr(
        "cyt_mcp.session_runtime.refresh_catalog_cache",
        _track_refresh,
    )
    monkeypatch.setattr("cyt_mcp.session_runtime.hydrate_runtime_cache", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "cyt_mcp.session_runtime.ensure_backend_servers_mounted",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr("cyt_mcp.hook_daemon_push._push_contexts", {})
    monkeypatch.setattr(
        "cyt_mcp.tool_list_notify.notify_all_sessions_list_changed",
        AsyncMock(),
    )

    coordinator._create_workspace_runtime(workspace, "test-key")
    await asyncio.sleep(0.05)

    assert refresh_calls
    assert refresh_calls[0].get("force") is True


def test_register_ws_catalog_does_not_strip_other_tool_schemas(
    tmp_path: Path,
) -> None:
    workspace = materialize_workspace(tmp_path)
    tools = load_tool_list(FULL_WS_DISK_CATALOG_PATH)
    register_ws_catalog(workspace, tools)
    merged = catalog_for_hook("cursor", workspace, allow_stale=False)
    semble = _tool_by_name(merged, "semble_search")
    gitnexus = _tool_by_name(merged, "gitnexus_cypher")
    assert set(semble["input_schema"]["required"]) == {"query", "repo"}
    assert gitnexus["input_schema"]["required"] == ["statement"]


def test_partial_registry_without_disk_stays_query_only(
    tmp_path: Path,
) -> None:
    """Contract exception: propagation_contract.json degraded_partial_registry_without_disk."""
    from tests.support.tool_schema_completeness_fixtures import load_propagation_pipeline_scenario

    scenario = load_propagation_pipeline_scenario("degraded_partial_registry_without_disk")
    workspace = materialize_workspace(tmp_path)
    config = cyt_mcp_hook_config(workspace)
    partial_tools = load_tool_list(PARTIAL_WS_REGISTRY_PATH)
    register_ws_catalog(workspace, partial_tools)

    fetched = _fetch_catalog_from_registry(config, allow_stale=True)
    semble = _tool_by_name(fetched, str(scenario.raw["tool_ref"]))
    assert sorted(semble["input_schema"]["required"]) == sorted(scenario.raw["expected_required"])
    for forbidden in scenario.raw.get("forbidden_properties") or []:
        assert forbidden not in semble["input_schema"].get("properties", {})


@pytest.mark.parametrize(
    ("tool_name", "expected_required"),
    multi_required_backend_tools(),
    ids=[name for name, _ in multi_required_backend_tools()],
)
@pytest.mark.parametrize("tier", [Tier.ACTIVE, Tier.HOT, Tier.EXTRA_HOT], ids=["T2", "T3", "T4"])
def test_ensure_injection_schema_restores_all_backend_required_for_tiers(
    tool_name: str,
    expected_required: list[str],
    tier: Tier,
) -> None:
    """Every multi-required backend tool keeps all required fields after tier materialization."""
    catalog = load_bm25_catalog_tools()
    full_tool = _tool_by_name(catalog, tool_name)
    partial = copy.deepcopy(full_tool)
    partial["input_schema"] = partial_schema_from_backend(full_tool)
    tiered = prepare_tool_for_tier_pipeline(partial, tier)
    tiered["cyt_injection_tier"] = {
        Tier.ACTIVE: "t2",
        Tier.HOT: "t3",
        Tier.EXTRA_HOT: "t4",
    }[tier]
    merged = ensure_tool_injection_schema(tiered, catalog_tools=catalog)
    injected_schema = merged.get("input_schema") or {}
    assert sorted(schema_required_property_names(injected_schema)) == expected_required


def test_build_tool_log_entry_type1_uses_fullest_schema_from_catalog() -> None:
    tools = [
        {
            "name": "jcodemunch_search_symbols",
            "server_key": "jcodemunch",
            "tool_name": "search_symbols",
            "input_schema": copy.deepcopy(PARTIAL_SEMBLE),
        },
        {
            "name": "jcodemunch_search_symbols",
            "server_key": "jcodemunch",
            "tool_name": "search_symbols",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "repo": {"type": "string"},
                },
                "required": ["query", "repo"],
            },
        },
    ]
    entry = build_tool_log_entry(
        tools[0],
        catalog="cyt_mcp",
        full=False,
        catalog_tools=tools,
    )
    assert set(entry["input_schema"]["required"]) == {"query", "repo"}


def test_catalog_payload_merges_fullest_schema_from_search_index() -> None:
    cache = RuntimeToolCache()
    cache.replace(
        [
            {
                "name": "semble_search",
                "inputSchema": copy.deepcopy(PARTIAL_SEMBLE),
            },
        ],
        search_index={
            "semble_search": {
                "name": "semble_search",
                "inputSchema": copy.deepcopy(FULL_SEMBLE),
            },
        },
    )
    payload = catalog_payload(cache, agent="cursor")
    semble = _tool_by_name(payload["tools"], "semble_search")
    assert set(semble["input_schema"]["required"]) == {"query", "repo"}


def test_merge_catalog_payloads_picks_fullest_schema_on_conflict() -> None:
    base = {
        "agent": "cursor",
        "tools": [
            {
                "name": "semble_search",
                "input_schema": copy.deepcopy(PARTIAL_SEMBLE),
                "cyt_catalog_scope": "user",
            },
        ],
        "degraded_servers": [],
    }
    overlay = {
        "agent": "cursor",
        "tools": [
            {
                "name": "semble_search",
                "input_schema": copy.deepcopy(FULL_SEMBLE),
                "cyt_catalog_scope": "workspace",
            },
        ],
        "degraded_servers": ["ws-down"],
    }
    merged = merge_catalog_payloads(base, overlay)
    semble = _tool_by_name(merged["tools"], "semble_search")
    assert set(semble["input_schema"]["required"]) == {"query", "repo"}
    assert semble["cyt_catalog_scope"] == "user"


def test_frontend_stub_preserves_all_backend_required_properties() -> None:
    tool = {
        "name": "context-mode_ctx_batch_execute",
        "description": "batch execute",
        "input_schema": {
            "type": "object",
            "properties": {
                "commands": {"type": "array"},
                "queries": {"type": "array"},
            },
            "required": ["commands", "queries"],
        },
    }
    stub = stub_dict_from_hook_tool(
        tool,
        retain={"tool": ["name"], "required_properties": ["name"]},
    )
    assert set(stub["inputSchema"]["required"]) == {"commands", "queries"}
    assert set(stub["inputSchema"]["properties"]) == {"commands", "queries"}


def test_usr_layer_register_preserves_fullest_schema(tmp_path: Path) -> None:
    workspace = materialize_workspace(tmp_path)
    ws_tools = load_tool_list(PARTIAL_WS_REGISTRY_PATH)
    usr_tools = load_tool_list(FULL_WS_DISK_CATALOG_PATH)
    register_ws_catalog(workspace, ws_tools)
    content_hash = register_catalog(
        {
            "agent": "cursor",
            "scope": "workspace",
            "workspace_root": str(workspace),
            "catalog_layer": "usr",
            "instance_id": "pid:usr-schema",
            "content_hash": "usr-full",
            "tools": usr_tools,
        },
    )
    assert content_hash.status == RegisterStatus.STORED

    merged = catalog_for_hook("cursor", workspace, allow_stale=False)
    semble = _tool_by_name(merged, "semble_search")
    assert set(semble["input_schema"]["required"]) == {"query", "repo"}
