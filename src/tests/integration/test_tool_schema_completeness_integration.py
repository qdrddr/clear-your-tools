"""Integration tests for tool schema completeness across hook inject and catalog fetch."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.cyt_mcp.catalog import _fetch_catalog_from_registry
from cyt.injection.tool_catalog_emit import emit_tool_catalog_session_log
from cyt.pruners.tools_filter import filter_tools_for_query
from cyt.skills.cli import run_hook_payload
from cyt.tiers.adapters.tools import tool_entity_id
from cyt.tiers.manager import TierManager, _managers
from cyt.tiers.models import Tier
from cyt.tools.schema_propagation_contract import (
    IdentityStage,
    PropagationStage,
    assert_backend_identity_preserved,
    assert_identity_matches_reference,
    assert_injection_fragment_properties,
    assert_no_cross_tool_leakage,
    assert_required_equal,
    assert_type2_record_shape,
    required_names,
    schema_from_tool,
)
from cyt_mcp.catalog import catalog_payload
from cyt_mcp.catalog_export import frontend_payload_from_hook_tools
from cyt_mcp.config import sample_aggregator_config
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_client.tool_gate import validate_pre_tool_call
from tests.support.cyt_mcp_catalog_resilience_fixtures import (
    capture_registry_registrations,
    patch_daemon_catalog_status,
)
from tests.support.dual_schema_injection_fixtures import (
    live_tier_config,
    materialize_fixture_pack,
    patch_paths,
    seed_tool_tiers,
    write_disk_catalog,
)
from tests.support.tool_schema_completeness_fixtures import (
    FULL_WS_DISK_CATALOG_PATH,
    PARTIAL_WS_REGISTRY_PATH,
    cyt_mcp_hook_config,
    extract_injection_block,
    load_pipeline_scenario,
    load_propagation_pipeline_scenario,
    load_propagation_reference_tool,
    load_scenario,
    load_tool_list,
    master_catalog_for_pipeline_source,
    materialize_workspace,
    parse_session_log_entries,
    register_ws_catalog,
    reset_catalog_state,
    resolve_reference_tool_from_catalogs,
    tool_record_from_type2,
    write_full_disk_catalog,
)


@pytest.fixture(autouse=True)
def _isolate_catalog_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("cyt.hook.active_workspace.touch_active_workspace", lambda *_a, **_k: None)
    monkeypatch.setenv("CYT_HOOK_QUIET", "1")
    reset_catalog_state()
    _managers.clear()
    yield
    reset_catalog_state()
    _managers.clear()


def test_hook_inject_exposes_semble_repo_in_pruned_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario("hook_inject_bm25_semble_schema")
    workspace = materialize_workspace(tmp_path)
    config = cyt_mcp_hook_config(workspace, db_path=tmp_path / "tiers.db")
    register_ws_catalog(workspace, load_tool_list(FULL_WS_DISK_CATALOG_PATH))
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())

    result = run_hook_payload(
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": str(scenario.raw["prompt"]),
            "cwd": str(workspace),
            "workspace_roots": [str(workspace)],
            "model": "claude-sonnet-4-20250514",
        },
        config,
        debug=True,
    )
    assert result.outcome not in {
        "skipped_cyt_mcp_unavailable",
        "skipped_missing_tools_catalog",
        "user_prompt_no_tool_matches",
    }
    for name in scenario.raw["expected_tool_names_in_stdout"]:
        assert name in result.stdout_text
    semble_ref = load_propagation_reference_tool("semble_search")
    parsed = parse_session_log_entries(result.session_log or [])
    if semble_ref.id in parsed["type1_by_name"]:
        semble_type1 = parsed["type1_by_name"][semble_ref.id]
        assert set(required_names(schema_from_tool(semble_type1))) == set(semble_ref.required)
    if f"name='{semble_ref.id}'" in result.stdout_text:
        fragment = extract_injection_block(result.stdout_text, semble_ref.id)
        assert_injection_fragment_properties(
            fragment,
            required=set(semble_ref.required),
            tool_name=semble_ref.id,
        )


def test_registry_fetch_enrichment_unblocks_pre_tool_gate_for_semble(
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
    semble = next(tool for tool in fetched if tool["name"] == "semble_search")
    assert set(semble["input_schema"]["required"]) == {"query", "repo"}

    log_path = tmp_path / "session.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(
            [
                '{"kind":"session_state","key":"session_state:inject","tools_inject_enabled":true}',
                '{"kind":"tool_catalog","key":"tool_catalog:cyt_mcp","catalog":"cyt_mcp",'
                f'"hash":"test","tools":{__import__("json").dumps([semble])}}}',
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: log_path,
    )

    allowed = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "semble_search",
            "tool_input": {"query": "bm25 scoring", "repo": str(workspace)},
            "workspace_roots": [str(workspace)],
        },
    )
    assert allowed.allowed is True

    denied = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "semble_search",
            "tool_input": {"query": "bm25 scoring"},
            "workspace_roots": [str(workspace)],
        },
    )
    assert denied.allowed is False
    assert "missing required property 'repo'" in denied.reason


def test_hook_inject_gitnexus_keeps_statement_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gitnexus keeps its own backend required fields and does not pick up semble query."""
    workspace = materialize_workspace(tmp_path)
    config = cyt_mcp_hook_config(workspace, db_path=tmp_path / "tiers.db")
    register_ws_catalog(workspace, load_tool_list(FULL_WS_DISK_CATALOG_PATH))
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())

    result = run_hook_payload(
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Run gitnexus_cypher to query the knowledge graph for BM25",
            "cwd": str(workspace),
            "workspace_roots": [str(workspace)],
            "model": "claude-sonnet-4-20250514",
        },
        config,
        debug=True,
    )
    assert "gitnexus_cypher" in result.stdout_text
    gitnexus_ref = load_propagation_reference_tool("gitnexus_cypher")
    if result.session_log:
        parsed = parse_session_log_entries(result.session_log)
        if gitnexus_ref.id in parsed["type1_by_name"]:
            gitnexus_type1 = parsed["type1_by_name"][gitnexus_ref.id]
            assert set(required_names(schema_from_tool(gitnexus_type1))) == set(
                gitnexus_ref.required,
            )
    fragment = extract_injection_block(result.stdout_text, gitnexus_ref.id)
    assert_injection_fragment_properties(
        fragment,
        required=set(gitnexus_ref.required),
        forbidden={"query"},
        tool_name=gitnexus_ref.id,
    )


def test_hook_inject_writes_type2_master_and_type1_survivors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contract: Type-2 from unpruned master; Type-1 + injection only for pruned survivors."""
    scenario = load_propagation_pipeline_scenario("survived_t2_type1_and_injection_have_all_required")
    ref = load_propagation_reference_tool(str(scenario.raw["tool_ref"]))
    backend_tool = resolve_reference_tool_from_catalogs(ref.id)
    backend_schema = schema_from_tool(backend_tool)

    workspace = materialize_workspace(tmp_path)
    config = cyt_mcp_hook_config(workspace, db_path=tmp_path / "tiers.db")
    master = load_tool_list(FULL_WS_DISK_CATALOG_PATH)
    register_ws_catalog(workspace, master)
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())

    result = run_hook_payload(
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": str(scenario.raw["prune_query"]),
            "cwd": str(workspace),
            "workspace_roots": [str(workspace)],
            "model": "claude-sonnet-4-20250514",
        },
        config,
        debug=True,
    )
    assert result.session_log
    parsed = parse_session_log_entries(result.session_log)
    type2_tools = parsed["type2_by_catalog"].get("cyt_mcp") or []
    assert type2_tools
    type2_record = tool_record_from_type2(type2_tools, ref.id)
    assert_type2_record_shape(type2_record)
    assert_backend_identity_preserved(type2_record)
    assert_required_equal(
        PropagationStage.TYPE2_CATALOG_RECORD,
        schema_from_tool(type2_record),
        backend_schema,
        tool_name=ref.id,
    )
    assert len(type2_tools) >= len(master)

    if ref.id in parsed["type1_by_name"]:
        type1 = parsed["type1_by_name"][ref.id]
        assert_backend_identity_preserved(type1)
        assert type1["server_key"] == ref.server_key
        assert type1["tool_name"] == ref.tool_name
        assert_required_equal(
            PropagationStage.TYPE1_TOOL_ENTRY,
            schema_from_tool(type1),
            backend_schema,
            tool_name=ref.id,
        )
        fragment = extract_injection_block(result.stdout_text, ref.id)
        assert_injection_fragment_properties(
            fragment,
            required=set(scenario.raw["expected_required"]),
            tool_name=ref.id,
        )


def test_tier_manager_live_prune_preserves_required_properties(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = materialize_fixture_pack(tmp_path)
    patch_paths(monkeypatch, pack)
    write_disk_catalog(pack)
    tool_name = "dual_tool_required_optional"
    target = next(t for t in pack.tools if str(t.get("name") or "") == tool_name)
    seed_tool_tiers(pack, {tool_entity_id(target): Tier.HOT})
    config = live_tier_config(pack)
    manager = TierManager(pack.workspace, str(pack.db_path))
    backend = schema_from_tool(next(t for t in pack.tools if t["name"] == tool_name))
    try:
        with patch("cyt.tiers.manager.get_tier_manager_for_config", return_value=manager):
            result = filter_tools_for_query(
                pack.tools,
                "search symbols with optional limit parameter",
                ["bm25"],
                config=config,
                for_hook=True,
            )
    finally:
        manager.close()

    assert result.tools
    pruned = next(t for t in result.tools if t["name"] == tool_name)
    assert str(pruned.get("cyt_injection_tier") or "").lower() == "t3"
    assert_required_equal(
        PropagationStage.TIER_INJECTED_SCHEMA,
        schema_from_tool(pruned),
        backend,
        tool_name=tool_name,
    )
    # Type-2 emit requires cyt_mcp identity fields; dual_schema fixture tools are injection-focused.
    assert tool_name in {str(t.get("name") or "") for t in pack.tools}


def test_frontend_stub_after_catalog_enrichment() -> None:
    ref = load_propagation_reference_tool("semble_search")
    backend = resolve_reference_tool_from_catalogs(ref.id)
    backend_schema = schema_from_tool(backend)
    cache = RuntimeToolCache()
    partial = copy.deepcopy(backend)
    partial["input_schema"] = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    cache.replace(
        [partial],
        search_index={
            ref.id: {
                "name": ref.id,
                "inputSchema": backend_schema,
            },
        },
    )
    payload = catalog_payload(cache, agent="cursor")
    hook_tool = next(t for t in payload["tools"] if t["name"] == ref.id)
    config = sample_aggregator_config(
        stub_retain={"tool": ["name"], "required_properties": ["name"]},
    )
    frontend = frontend_payload_from_hook_tools([hook_tool], config=config)
    stub = next(t for t in frontend["tools"] if t["name"] == ref.id)
    assert set(stub["inputSchema"]["required"]) == set(ref.required)


def test_hook_inject_preserves_backend_identity_through_prune_and_session_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hook → prune → Type-2/Type-1 must keep deterministic server_key/tool_name mapping."""
    scenario = load_scenario("hook_inject_bm25_semble_schema")
    ref = load_propagation_reference_tool("semble_search")
    workspace = materialize_workspace(tmp_path)
    config = cyt_mcp_hook_config(workspace, db_path=tmp_path / "tiers.db")
    register_ws_catalog(workspace, load_tool_list(FULL_WS_DISK_CATALOG_PATH))
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())

    result = run_hook_payload(
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": str(scenario.raw["prompt"]),
            "cwd": str(workspace),
            "workspace_roots": [str(workspace)],
            "model": "claude-sonnet-4-20250514",
        },
        config,
        debug=True,
    )
    assert result.outcome not in {
        "skipped_cyt_mcp_unavailable",
        "skipped_missing_tools_catalog",
        "user_prompt_no_tool_matches",
    }

    parsed = parse_session_log_entries(result.session_log or [])
    type2_tools = parsed["type2_by_catalog"].get("cyt_mcp") or []
    type2_record = tool_record_from_type2(type2_tools, ref.id)
    assert_identity_matches_reference(
        type2_record,
        wire_name=ref.id,
        server_key=ref.server_key,
        tool_name=ref.tool_name,
        stage=IdentityStage.TYPE2_CATALOG,
    )

    type1 = parsed["type1_by_name"].get(ref.id)
    if type1 is not None:
        assert str(type1.get("name") or "") == ref.id

    if f"name='{ref.id}'" in result.stdout_text:
        fragment = extract_injection_block(result.stdout_text, ref.id)
        assert fragment
        assert ref.server_key in fragment or ref.tool_name in fragment or ref.id in fragment


def test_cross_tool_pipeline_scenario_no_query_on_gitnexus() -> None:
    scenario = load_pipeline_scenario("cross_tool_no_query_on_gitnexus")
    master = master_catalog_for_pipeline_source(str(scenario.raw["master_catalog_source"]))
    owner = next(t for t in master if t["name"] == scenario.raw["owner_tool_ref"])
    target = next(t for t in master if t["name"] == scenario.raw["target_tool_ref"])
    assert_no_cross_tool_leakage(
        [owner, target],
        str(scenario.raw["leaked_property"]),
        str(scenario.raw["owner_tool_ref"]),
    )


def test_pre_tool_gate_contract_scenario_denies_missing_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Links propagation contract to preToolUse gate (Type-2 authority catalog)."""
    scenario = load_propagation_pipeline_scenario("survived_t2_type1_and_injection_have_all_required")
    ref = load_propagation_reference_tool(str(scenario.raw["tool_ref"]))
    workspace = materialize_workspace(tmp_path)
    master = load_tool_list(FULL_WS_DISK_CATALOG_PATH)
    semble = next(t for t in master if t["name"] == ref.id)

    log_path = tmp_path / "session.jsonl"
    entries = emit_tool_catalog_session_log(master, payload={}, tools_inject_enabled=True)
    log_path.write_text(
        "\n".join(__import__("json").dumps(entry) for entry in entries) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("cyt_client.tool_gate.session_log_path", lambda _payload: log_path)

    allowed = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": ref.id,
            "tool_input": {"query": "bm25", "repo": str(workspace)},
            "workspace_roots": [str(workspace)],
        },
    )
    assert allowed.allowed is True

    denied = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": ref.id,
            "tool_input": {"query": "bm25"},
            "workspace_roots": [str(workspace)],
        },
    )
    assert denied.allowed is False
    missing = set(ref.required) - {"query"}
    assert any(prop in (denied.reason or "") for prop in missing)
    _ = semble
