"""Contract tests: backend required/optional propagation across cyt-mcp pipeline stages."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from cyt.injection.session_gate import gate_tools_for_session
from cyt.injection.session_log import SessionLogIndex
from cyt.injection.tool_catalog_emit import emit_tool_catalog_session_log
from cyt.pruners.tools_filter import filter_tools_for_query
from cyt.pruners.tools_filter import filter_tools_for_query
from cyt.tiers.adapters.tools import apply_tool_tiers, prepare_tool_for_tier_pipeline, tool_entity_id
from cyt.tiers.manager import TierManager, _managers
from cyt.tiers.models import Tier
from cyt.tools.inject import format_tool_item
from cyt.tools.injection_schema import ensure_tool_injection_schema
from cyt.tools.schema_propagation_contract import (
    IdentityStage,
    PropagationStage,
    assert_backend_identity_preserved,
    assert_frontend_stub_wire_name,
    assert_identity_matches_reference,
    assert_injection_fragment_properties,
    assert_no_cross_tool_leakage,
    assert_required_equal,
    assert_tier_identity_preserved,
    assert_tier_injected_schema,
    assert_explicit_identity_preferred,
    assert_type1_wire_name,
    assert_type2_record_shape,
    backend_identity_from_tool,
    optional_names,
    required_names,
    schema_from_tool,
    tool_entity_id_from_tool,
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
    cyt_mcp_hook_config,
    load_bm25_catalog_tools,
    load_identity_pipeline_modules,
    load_pipeline_scenario,
    load_propagation_contract,
    load_propagation_pipeline_scenario,
    load_propagation_reference_tool,
    load_propagation_reference_tools,
    load_tier_identity_expectations,
    load_type2_expectations,
    master_catalog_for_pipeline_source,
    materialize_workspace,
    partial_schema_from_backend,
    register_ws_catalog,
    resolve_reference_tool_from_catalogs,
    reset_catalog_state,
    tool_record_from_type2,
    load_tool_list,
)


@pytest.fixture(autouse=True)
def _isolate_catalog_state() -> Iterator[None]:
    reset_catalog_state()
    _managers.clear()
    yield
    reset_catalog_state()
    _managers.clear()


def _tool_by_name(tools: list[dict], name: str) -> dict:
    return next(tool for tool in tools if str(tool.get("name") or "") == name)


def _backend_schema(tool: dict) -> dict:
    return schema_from_tool(tool)


def _tier_enum(tier_label: str) -> Tier:
    return {
        "t0": Tier.DORMANT,
        "t1": Tier.COLD,
        "t2": Tier.ACTIVE,
        "t3": Tier.HOT,
        "t4": Tier.EXTRA_HOT,
    }[tier_label.strip().lower()]


def test_propagation_contract_fixture_is_well_formed() -> None:
    payload = load_propagation_contract()
    refs = load_propagation_reference_tools()
    assert refs
    assert len({item.id for item in refs}) == len(refs)
    scenarios = payload.get("pipeline_scenarios")
    assert isinstance(scenarios, list) and scenarios
    assert len({str(item["id"]) for item in scenarios if isinstance(item, dict)}) == len(scenarios)
    tier_rows = payload.get("tier_expectations")
    assert isinstance(tier_rows, list) and tier_rows
    type2_rows = payload.get("type2_expectations")
    assert isinstance(type2_rows, list) and type2_rows
    tier_identity_rows = payload.get("tier_identity_expectations")
    assert isinstance(tier_identity_rows, list) and tier_identity_rows
    identity_modules = payload.get("identity_pipeline_modules")
    assert isinstance(identity_modules, list) and identity_modules
    for ref in refs:
        resolve_reference_tool_from_catalogs(ref.id)
        assert ref.id == backend_identity_from_tool(
            resolve_reference_tool_from_catalogs(ref.id),
        ).wire_name
    assert load_pipeline_scenario("backend_identity_preserved_in_type2").id == "backend_identity_preserved_in_type2"
    assert load_pipeline_scenario("backend_identity_preserved_in_type1").id == "backend_identity_preserved_in_type1"
    session_log_identity = payload.get("session_log_identity")
    assert isinstance(session_log_identity, dict)
    writer_rows = session_log_identity.get("writer_scenarios")
    assert isinstance(writer_rows, list) and writer_rows
    assert len({str(item["id"]) for item in writer_rows if isinstance(item, dict)}) == len(writer_rows)


@pytest.mark.parametrize("ref", load_propagation_reference_tools(), ids=lambda r: r.id)
def test_reference_tool_backend_schema_matches_contract(ref) -> None:
    tool = resolve_reference_tool_from_catalogs(ref.id)
    backend = _backend_schema(tool)
    assert sorted(required_names(backend)) == sorted(ref.required)
    assert ref.optional == [] or optional_names(backend).issuperset(set(ref.optional))


def test_type2_from_unpruned_master_includes_all_tools_not_pruned_subset() -> None:
    scenario = load_propagation_pipeline_scenario("unpruned_type2_contains_tool_not_in_pruned_output")
    master = master_catalog_for_pipeline_source(str(scenario.raw["master_catalog_source"]))
    # Pruned injection uses a strict subset; Type-2 always emits the full master catalog.
    pruned_subset = [_tool_by_name(master, str(scenario.raw["pruned_must_include"][0]))]
    pruned_names = {str(tool.get("name") or "") for tool in pruned_subset}
    for name in scenario.raw["pruned_must_exclude"]:
        assert name not in pruned_names

    type2_entries = emit_tool_catalog_session_log(master, payload={}, tools_inject_enabled=True)
    cyt_mcp_catalog = next(
        entry
        for entry in type2_entries
        if entry.get("kind") == "tool_catalog" and entry.get("catalog") == "cyt_mcp"
    )
    type2_names = {str(tool.get("name") or "") for tool in cyt_mcp_catalog.get("tools") or []}
    for name in scenario.raw["type2_must_include"]:
        assert name in type2_names
    assert len(type2_names) == len(master)
    assert len(type2_names) > len(pruned_names)


def test_type2_record_shape_is_normalized() -> None:
    master = load_tool_list_from_full_disk()
    entries = emit_tool_catalog_session_log(master, payload={}, tools_inject_enabled=True)
    cyt_mcp_catalog = next(
        entry
        for entry in entries
        if entry.get("kind") == "tool_catalog" and entry.get("catalog") == "cyt_mcp"
    )
    for record in cyt_mcp_catalog.get("tools") or []:
        assert_type2_record_shape(record)
        assert_backend_identity_preserved(record)


def test_type2_dual_required_tool_has_backend_required() -> None:
    ref = load_propagation_reference_tool("semble_search")
    master = load_tool_list_from_full_disk()
    entries = emit_tool_catalog_session_log(master, payload={}, tools_inject_enabled=True)
    cyt_mcp_tools = next(
        entry["tools"]
        for entry in entries
        if entry.get("kind") == "tool_catalog" and entry.get("catalog") == "cyt_mcp"
    )
    record = tool_record_from_type2(cyt_mcp_tools, ref.id)
    backend = _backend_schema(resolve_reference_tool_from_catalogs(ref.id))
    assert_required_equal(
        PropagationStage.TYPE2_CATALOG_RECORD,
        schema_from_tool(record),
        backend,
        tool_name=ref.id,
    )


def test_type1_from_gate_retains_required_for_survived_tool() -> None:
    ref = load_propagation_reference_tool("semble_search")
    master = load_tool_list_from_full_disk()
    full_tool = _tool_by_name(master, ref.id)
    backend = _backend_schema(full_tool)
    partial = copy.deepcopy(full_tool)
    partial["input_schema"] = partial_schema_from_backend(full_tool)
    partial["cyt_injection_tier"] = "t2"
    tiered = ensure_tool_injection_schema(partial, catalog_tools=master)

    kept, log_entries, _flags = gate_tools_for_session(
        [tiered],
        config={"tools": {"hook": {"tools_from": ["cyt_mcp"]}}},
        session_text="",
        index=SessionLogIndex(entries=()),
        catalog_tools=master,
    )
    assert kept
    type1 = next(entry for entry in log_entries if entry.get("kind") == "tool")
    assert_required_equal(
        PropagationStage.TYPE1_TOOL_ENTRY,
        schema_from_tool(type1),
        backend,
        tool_name=ref.id,
    )


@pytest.mark.parametrize(
    "row",
    load_propagation_contract().get("tier_expectations") or [],
    ids=lambda row: f"{row['tool_ref']}_{row['tier']}",
)
def test_tier_injected_schema_matches_contract(row: dict) -> None:
    tool = resolve_reference_tool_from_catalogs(str(row["tool_ref"]))
    backend = _backend_schema(tool)
    working = copy.deepcopy(tool)
    if str(row["tier"]).strip().lower() == "t2":
        working["input_schema"] = partial_schema_from_backend(tool)
    tiered = prepare_tool_for_tier_pipeline(working, _tier_enum(str(row["tier"])))
    assert_tier_identity_preserved(working, tiered, tier=str(row["tier"]))
    tiered["cyt_injection_tier"] = str(row["tier"])
    catalog = (
        load_dual_schema_tools()
        if load_propagation_reference_tool(str(row["tool_ref"])).catalog_source == "dual_schema"
        else load_bm25_catalog_tools()
    )
    merged = ensure_tool_injection_schema(tiered, catalog_tools=catalog)
    assert_tier_injected_schema(
        merged,
        str(row["tier"]),
        backend,
        tool_name=str(row["tool_ref"]),
    )
    fragment = format_tool_item(merged)
    assert_injection_fragment_properties(
        fragment,
        required=set(row.get("injected_required") or []),
        optional=set(row.get("injected_optional") or []) or None,
        tool_name=str(row["tool_ref"]),
    )


def test_cross_tool_query_does_not_leak_to_gitnexus() -> None:
    scenario = load_pipeline_scenario("cross_tool_no_query_on_gitnexus")
    master = master_catalog_for_pipeline_source(str(scenario.raw["master_catalog_source"]))
    owner = _tool_by_name(master, str(scenario.raw["owner_tool_ref"]))
    target = _tool_by_name(master, str(scenario.raw["target_tool_ref"]))
    assert_no_cross_tool_leakage(
        [owner, target],
        str(scenario.raw["leaked_property"]),
        str(scenario.raw["owner_tool_ref"]),
    )


@pytest.mark.parametrize(
    "row",
    load_type2_expectations(),
    ids=lambda row: str(row["tool_ref"]),
)
def test_type2_expectations_match_unpruned_emit(row: dict) -> None:
    master = load_tool_list_from_full_disk()
    entries = emit_tool_catalog_session_log(master, payload={}, tools_inject_enabled=True)
    cyt_mcp_tools = next(
        entry["tools"]
        for entry in entries
        if entry.get("kind") == "tool_catalog" and entry.get("catalog") == "cyt_mcp"
    )
    record = tool_record_from_type2(cyt_mcp_tools, str(row["tool_ref"]))
    backend = _backend_schema(resolve_reference_tool_from_catalogs(str(row["tool_ref"])))
    assert_required_equal(
        PropagationStage.TYPE2_CATALOG_RECORD,
        schema_from_tool(record),
        backend,
        tool_name=str(row["tool_ref"]),
    )
    assert sorted(required_names(schema_from_tool(record))) == sorted(row["required"])


def test_contract_degraded_partial_registry_exception() -> None:
    scenario = load_propagation_pipeline_scenario("degraded_partial_registry_without_disk")
    master = master_catalog_for_pipeline_source(str(scenario.raw["master_catalog_source"]))
    tool = _tool_by_name(master, str(scenario.raw["tool_ref"]))
    schema = schema_from_tool(tool)
    assert sorted(required_names(schema)) == sorted(scenario.raw["expected_required"])
    for forbidden in scenario.raw.get("forbidden_properties") or []:
        assert forbidden not in schema.get("properties", {})


def test_t3_injection_includes_optionals_t2_does_not() -> None:
    tool = resolve_reference_tool_from_catalogs("dual_tool_required_optional")
    backend = _backend_schema(tool)
    catalog = load_dual_schema_tools()

    for tier_label, expect_optional in (("t2", False), ("t3", True)):
        working = copy.deepcopy(tool)
        if tier_label == "t2":
            working["input_schema"] = partial_schema_from_backend(tool)
        tiered = prepare_tool_for_tier_pipeline(working, _tier_enum(tier_label))
        tiered["cyt_injection_tier"] = tier_label
        merged = ensure_tool_injection_schema(tiered, catalog_tools=catalog)
        assert_tier_injected_schema(merged, tier_label, backend, tool_name=tool["name"])
        fragment = format_tool_item(merged)
        if expect_optional:
            assert_injection_fragment_properties(
                fragment,
                required=required_names(backend),
                optional={"limit"},
                tool_name=tool["name"],
            )
        else:
            assert_injection_fragment_properties(
                fragment,
                required=required_names(backend),
                forbidden={"limit"},
                tool_name=tool["name"],
            )


def test_backend_identity_preserved_in_type2_emit() -> None:
    scenario = load_propagation_pipeline_scenario("backend_identity_preserved_in_type2")
    master = master_catalog_for_pipeline_source(str(scenario.raw["master_catalog_source"]))
    entries = emit_tool_catalog_session_log(master, payload={}, tools_inject_enabled=True)
    cyt_mcp_tools = next(
        entry["tools"]
        for entry in entries
        if entry.get("kind") == "tool_catalog" and entry.get("catalog") == "cyt_mcp"
    )
    record = tool_record_from_type2(cyt_mcp_tools, str(scenario.raw["tool_ref"]))
    assert record["server_key"] == scenario.raw["server_key"]
    assert record["tool_name"] == scenario.raw["tool_name"]
    assert_backend_identity_preserved(record)


def test_backend_identity_preserved_in_type1_emit() -> None:
    from cyt.tools.injection_schema import ensure_tool_injection_schema

    scenario = load_propagation_pipeline_scenario("backend_identity_preserved_in_type1")
    ref = load_propagation_reference_tool(str(scenario.raw["tool_ref"]))
    master = master_catalog_for_pipeline_source(str(scenario.raw["master_catalog_source"]))
    full_tool = next(t for t in master if str(t.get("name") or "") == ref.id)
    partial = copy.deepcopy(full_tool)
    partial["input_schema"] = partial_schema_from_backend(full_tool)
    partial["cyt_injection_tier"] = "t2"
    tiered = ensure_tool_injection_schema(partial, catalog_tools=master)
    _kept, log_entries, _flags = gate_tools_for_session(
        [tiered],
        config={"tools": {"hook": {"tools_from": ["cyt_mcp"]}}},
        session_text="",
        index=SessionLogIndex(entries=()),
        catalog_tools=master,
    )
    type1 = next(entry for entry in log_entries if entry.get("kind") == "tool")
    assert type1["server_key"] == scenario.raw["server_key"]
    assert type1["tool_name"] == scenario.raw["tool_name"]
    assert_backend_identity_preserved(type1)


@pytest.mark.parametrize(
    "row",
    load_tier_identity_expectations(),
    ids=lambda row: f"{row['tool_ref']}_{row['tier']}",
)
def test_tier_prep_preserves_backend_identity_t0_through_t4(row: dict) -> None:
    ref = load_propagation_reference_tool(str(row["tool_ref"]))
    tool = resolve_reference_tool_from_catalogs(ref.id)
    tiered = prepare_tool_for_tier_pipeline(copy.deepcopy(tool), _tier_enum(str(row["tier"])))
    assert_tier_identity_preserved(tool, tiered, tier=str(row["tier"]), stage=IdentityStage.TIER_PREP)
    assert_identity_matches_reference(
        tiered,
        wire_name=ref.id,
        server_key=ref.server_key,
        tool_name=ref.tool_name,
        stage=IdentityStage.TIER_PREP,
    )
    assert tool_entity_id_from_tool(tool) == tool_entity_id_from_tool(tiered)


def test_apply_tool_tiers_t0_exclusion_preserves_source_identity() -> None:
    ref = load_propagation_reference_tool("semble_search")
    tool = resolve_reference_tool_from_catalogs(ref.id)
    entity_id = tool_entity_id_from_tool(tool)
    result = apply_tool_tiers(
        [copy.deepcopy(tool)],
        tier_for_tool={entity_id: Tier.DORMANT},
        apply=True,
    )
    assert entity_id in result.excluded_t0
    assert not result.eligible_tools
    assert_tier_identity_preserved(tool, tool, stage=IdentityStage.TIER_APPLY, tier="t0")


def _run_identity_pipeline_transform(
    module_id: str,
    tool: dict[str, Any],
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    wire_name = str(tool.get("name") or "")

    if module_id == "runtime_cache_catalog_payload":
        from cyt_mcp.catalog import catalog_payload
        from cyt_mcp.runtime_cache import RuntimeToolCache

        cache = RuntimeToolCache()
        cache.replace([copy.deepcopy(tool)])
        payload = catalog_payload(
            cache,
            agent="cursor",
            server_keys=[str(tool.get("server_key") or "")],
        )
        return next(t for t in payload["tools"] if t["name"] == wire_name)

    if module_id == "hook_cache_normalize":
        from cyt.cyt_mcp.catalog import _normalize_tools_list

        normalized = _normalize_tools_list([copy.deepcopy(tool)])
        return next(t for t in normalized if t["name"] == wire_name)

    if module_id == "catalog_registry_union":
        from cyt.hook.catalog_registry import catalog_for_hook

        workspace = materialize_workspace(tmp_path)
        register_ws_catalog(workspace, load_tool_list(FULL_WS_DISK_CATALOG_PATH))
        config = cyt_mcp_hook_config(workspace)
        merged = catalog_for_hook("cursor", workspace, allow_stale=False)
        return next(t for t in merged if t["name"] == wire_name)

    if module_id == "tier_manager_prune":
        pack = materialize_fixture_pack(tmp_path)
        patch_paths(monkeypatch, pack)
        write_disk_catalog(pack)
        seed_tool_tiers(
            pack,
            {
                tool_entity_id(_tool_by_name(pack.tools, wire_name)): Tier.ACTIVE,
            },
        )
        config = live_tier_config(pack)
        manager = TierManager(pack.workspace, str(pack.db_path))
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
        return _tool_by_name(result.tools, wire_name)

    if module_id == "frontend_stub_export":
        from cyt_mcp.catalog_export import stub_dict_from_hook_tool
        from cyt_mcp.config import sample_aggregator_config

        config = sample_aggregator_config(
            stub_retain={"tool": ["name"], "required_properties": ["name"]},
        )
        stub = stub_dict_from_hook_tool(copy.deepcopy(tool), retain=config.stub_retain)
        assert_frontend_stub_wire_name(stub, tool)
        return tool

    if module_id == "client_session_capture_type2":
        from cyt_client.session_capture import _tool_record_for_catalog

        definition = {
            "name": wire_name,
            "inputSchema": copy.deepcopy(tool.get("input_schema") or {}),
            "server_key": str(tool.get("server_key") or ""),
            "tool_name": str(tool.get("tool_name") or ""),
        }
        return _tool_record_for_catalog(wire_name, definition)

    raise ValueError(f"unknown identity pipeline module id {module_id!r}")


@pytest.mark.parametrize(
    "row",
    load_identity_pipeline_modules(),
    ids=lambda row: str(row["id"]),
)
def test_identity_pipeline_module_preserves_backend_mapping(
    row: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref = load_propagation_reference_tool(str(row["tool_ref"]))
    tool = resolve_reference_tool_from_catalogs(ref.id)
    after = _run_identity_pipeline_transform(
        str(row["id"]),
        tool,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    stage = IdentityStage(str(row.get("stage") or IdentityStage.BACKEND))
    if row["id"] == "frontend_stub_export":
        assert after["name"] == ref.id
        return
    if row["id"] == "tier_manager_prune":
        assert str(after.get("name") or "") == ref.id
        return
    if row["id"] == "client_session_capture_type2":
        assert str(after.get("name") or "") == ref.id
        assert after["server_key"] == ref.server_key
        assert after["tool_name"] == ref.tool_name
        assert_backend_identity_preserved(after)
        return
    assert_tier_identity_preserved(tool, after, stage=stage)
    assert_identity_matches_reference(
        after,
        wire_name=ref.id,
        server_key=ref.server_key,
        tool_name=ref.tool_name,
        stage=stage,
    )


def test_canonical_backend_identity_prefers_explicit_fields() -> None:
    server_keys = ["codebase-memory", "semble", "search"]
    tool = {
        "name": "codebase-memory_search_graph",
        "server_key": "search",
        "tool_name": "graph",
    }
    assert_explicit_identity_preferred(
        tool,
        server_keys,
        expected_server="search",
        expected_bare="graph",
    )


def test_type1_entry_preserves_wire_name_for_cyt_mcp() -> None:
    ref = load_propagation_reference_tool("semble_search")
    master = load_tool_list_from_full_disk()
    full_tool = _tool_by_name(master, ref.id)
    partial = copy.deepcopy(full_tool)
    partial["input_schema"] = partial_schema_from_backend(full_tool)
    partial["cyt_injection_tier"] = "t2"
    tiered = ensure_tool_injection_schema(partial, catalog_tools=master)
    _kept, log_entries, _flags = gate_tools_for_session(
        [tiered],
        config={"tools": {"hook": {"tools_from": ["cyt_mcp"]}}},
        session_text="",
        index=SessionLogIndex(entries=()),
        catalog_tools=master,
    )
    type1 = next(entry for entry in log_entries if entry.get("kind") == "tool")
    assert_type1_wire_name(type1, ref.id)


def test_tier_manager_prune_preserves_required_on_survived_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = materialize_fixture_pack(tmp_path)
    patch_paths(monkeypatch, pack)
    write_disk_catalog(pack)
    seed_tool_tiers(
        pack,
        {tool_entity_id(_tool_by_name(pack.tools, "dual_tool_required_optional")): Tier.ACTIVE},
    )
    config = live_tier_config(pack)
    manager = TierManager(pack.workspace, str(pack.db_path))
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
    tool = _tool_by_name(result.tools, "dual_tool_required_optional")
    backend = _backend_schema(_tool_by_name(pack.tools, "dual_tool_required_optional"))
    assert_required_equal(
        PropagationStage.TIER_INJECTED_SCHEMA,
        schema_from_tool(tool),
        backend,
        tool_name="dual_tool_required_optional",
    )
    assert_tier_injected_schema(tool, "T2", backend, tool_name="dual_tool_required_optional")


def load_tool_list_from_full_disk() -> list[dict]:
    from tests.support.tool_schema_completeness_fixtures import load_tool_list

    return load_tool_list(FULL_WS_DISK_CATALOG_PATH)


def load_dual_schema_tools() -> list[dict]:
    from tests.support.tool_schema_completeness_fixtures import load_dual_schema_catalog_tools

    return load_dual_schema_catalog_tools()
