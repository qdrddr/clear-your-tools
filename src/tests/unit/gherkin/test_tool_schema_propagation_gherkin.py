"""Gherkin steps for tool schema propagation contract."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from cyt.cyt_mcp.catalog import _fetch_catalog_from_registry
from cyt.injection.session_gate import gate_tools_for_session
from cyt.injection.session_log import SessionLogIndex
from cyt.injection.tool_catalog_emit import emit_tool_catalog_session_log
from cyt.tiers.adapters.tools import prepare_tool_for_tier_pipeline
from cyt.tiers.models import Tier
from cyt.tools.injection_schema import ensure_tool_injection_schema
from cyt.tools.schema_propagation_contract import (
    IdentityStage,
    assert_backend_identity_preserved,
    assert_identity_matches_reference,
    assert_no_cross_tool_leakage,
    assert_required_equal,
    assert_tier_identity_preserved,
    assert_tier_injected_schema,
    optional_names,
    required_names,
    schema_from_tool,
    tool_entity_id_from_tool,
)
from cyt_client.tool_gate import validate_pre_tool_call
from tests.support.tool_schema_completeness_fixtures import (
    FULL_WS_DISK_CATALOG_PATH,
    PARTIAL_WS_REGISTRY_PATH,
    cyt_mcp_hook_config,
    load_dual_schema_catalog_tools,
    load_propagation_reference_tool,
    load_tool_list,
    materialize_workspace,
    partial_schema_from_backend as partial_schema_fixture,
    register_ws_catalog,
    reset_catalog_state,
    resolve_reference_tool_from_catalogs,
    tool_record_from_type2,
)
from tests.unit.gherkin.conftest import GherkinContext
from tests.unit.gherkin.test_tool_catalog_gate_gherkin import (
    _cyt_mcp_catalog,
    _patch_session_log_path,
    _write_session,
)

FEATURES = Path(__file__).resolve().parent / "features" / "tool_schema_propagation.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


@pytest.fixture(autouse=True)
def _reset_catalog() -> None:
    reset_catalog_state()
    yield
    reset_catalog_state()


def _master_from_full_disk() -> list[dict]:
    return load_tool_list(FULL_WS_DISK_CATALOG_PATH)


def _tool_by_name(tools: list[dict], name: str) -> dict:
    return next(t for t in tools if str(t.get("name") or "") == name)


@given(
    parsers.parse(
        "an unpruned cyt_mcp master catalog with tool {tool_name} query string repo string required",
    ),
)
def given_master_semble_dual_required(tool_name: str, gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["master_catalog"] = _master_from_full_disk()
    gherkin_context.payload["focus_tool"] = tool_name


@given(
    parsers.parse(
        "an unpruned cyt_mcp master catalog with tool {tool_name} query string required",
    ),
)
def given_master_query_only(tool_name: str, gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["master_catalog"] = _master_from_full_disk()
    gherkin_context.payload["focus_tool"] = tool_name


@given(
    parsers.parse(
        "an unpruned cyt_mcp master catalog with tool {tool_name} statement string required",
    ),
)
def given_master_statement_only(tool_name: str, gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["master_catalog"] = _master_from_full_disk()
    gherkin_context.payload["focus_tool"] = tool_name


@given(
    parsers.parse(
        "an unpruned cyt_mcp master catalog with tools {tool_names}",
    ),
)
def given_master_multi(tool_names: str, gherkin_context: GherkinContext) -> None:
    names = {part.strip() for part in tool_names.split() if part.strip()}
    master = _master_from_full_disk()
    gherkin_context.payload["master_catalog"] = master
    gherkin_context.payload["master_names"] = names


@given(parsers.parse("a pruned subset containing only {tool_name}"))
def given_pruned_subset(tool_name: str, gherkin_context: GherkinContext) -> None:
    master = gherkin_context.payload.get("master_catalog") or _master_from_full_disk()
    gherkin_context.payload["pruned_subset"] = [_tool_by_name(master, tool_name)]


@given(parsers.parse("a dual_schema tool {tool_name} with tier {tier}"))
def given_dual_schema_tier(tool_name: str, tier: str, gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["dual_tool_name"] = tool_name
    gherkin_context.payload["tier"] = tier.strip().lower()
    gherkin_context.payload["dual_catalog"] = load_dual_schema_catalog_tools()


@given(
    parsers.parse(
        "master catalog tools {tool_a} and {tool_b} from full disk fixture",
    ),
)
def given_two_tools(tool_a: str, tool_b: str, gherkin_context: GherkinContext) -> None:
    master = _master_from_full_disk()
    gherkin_context.payload["tool_a"] = _tool_by_name(master, tool_a)
    gherkin_context.payload["tool_b"] = _tool_by_name(master, tool_b)


@given("a partial workspace registry with query-only semble_search")
def given_partial_registry(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    workspace = materialize_workspace(tmp_path)
    register_ws_catalog(workspace, load_tool_list(PARTIAL_WS_REGISTRY_PATH))
    gherkin_context.payload["workspace"] = workspace
    gherkin_context.payload["config"] = cyt_mcp_hook_config(workspace)


@given(parsers.parse("a pruned {tool_name} tool with tier {tier} partial schema query only"))
def given_pruned_partial_semble(
    tool_name: str,
    tier: str,
    gherkin_context: GherkinContext,
) -> None:
    master = gherkin_context.payload.get("master_catalog") or _master_from_full_disk()
    full_tool = _tool_by_name(master, tool_name)
    partial = copy.deepcopy(full_tool)
    partial["input_schema"] = partial_schema_fixture(full_tool)
    partial["cyt_injection_tier"] = tier.strip().lower()
    gherkin_context.payload["pruned_tool"] = partial
    gherkin_context.payload["master_catalog"] = master


@when("Type-2 tool_catalog session log is emitted from the master catalog")
def when_emit_type2(gherkin_context: GherkinContext) -> None:
    master = gherkin_context.payload.get("master_catalog") or _master_from_full_disk()
    entries = emit_tool_catalog_session_log(master, payload={}, tools_inject_enabled=True)
    type2 = next(
        entry
        for entry in entries
        if entry.get("kind") == "tool_catalog" and entry.get("catalog") == "cyt_mcp"
    )
    gherkin_context.payload["type2_tools"] = type2.get("tools") or []


@when("the tool is prepared for tier injection with master catalog merge")
def when_prepare_tier_injection(gherkin_context: GherkinContext) -> None:
    tool_name = str(gherkin_context.payload.get("dual_tool_name") or "")
    tier_label = str(gherkin_context.payload.get("tier") or "t2")
    catalog = gherkin_context.payload.get("dual_catalog") or load_dual_schema_catalog_tools()
    tool = _tool_by_name(catalog, tool_name)
    backend = schema_from_tool(tool)
    working = copy.deepcopy(tool)
    if tier_label == "t2":
        working["input_schema"] = partial_schema_fixture(tool)
    tier_enum = {"t2": Tier.ACTIVE, "t3": Tier.HOT, "t4": Tier.EXTRA_HOT}[tier_label]
    tiered = prepare_tool_for_tier_pipeline(working, tier_enum)
    tiered["cyt_injection_tier"] = tier_label
    merged = ensure_tool_injection_schema(tiered, catalog_tools=catalog)
    gherkin_context.payload["merged_tool"] = merged
    gherkin_context.payload["backend_schema"] = backend


@given("an unpruned cyt_mcp master catalog from full disk fixture")
def given_master_full_disk(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["master_catalog"] = _master_from_full_disk()


@given(parsers.parse("a reference cyt_mcp tool {tool_ref} from propagation contract"))
def given_reference_tool(tool_ref: str, gherkin_context: GherkinContext) -> None:
    ref = load_propagation_reference_tool(tool_ref)
    tool = resolve_reference_tool_from_catalogs(ref.id)
    gherkin_context.payload["reference_tool"] = ref
    gherkin_context.payload["source_tool"] = tool


@when(parsers.parse("tier prep materializes the tool for tier {tier}"))
def when_tier_prep_materializes(tier: str, gherkin_context: GherkinContext) -> None:
    tool = gherkin_context.payload.get("source_tool") or {}
    tier_enum = {
        "t0": Tier.DORMANT,
        "t1": Tier.COLD,
        "t2": Tier.ACTIVE,
        "t3": Tier.HOT,
        "t4": Tier.EXTRA_HOT,
    }[tier.strip().lower()]
    prepared = prepare_tool_for_tier_pipeline(copy.deepcopy(tool), tier_enum)
    gherkin_context.payload["prepared_tool"] = prepared
    gherkin_context.payload["tier"] = tier.strip().lower()
    gherkin_context.payload["identity_stage"] = IdentityStage.TIER_PREP


@when("runtime cache catalog payload is built for the tool")
def when_runtime_cache_payload(gherkin_context: GherkinContext) -> None:
    from cyt_mcp.catalog import catalog_payload
    from cyt_mcp.runtime_cache import RuntimeToolCache

    tool = gherkin_context.payload.get("source_tool") or {}
    cache = RuntimeToolCache()
    cache.replace([copy.deepcopy(tool)])
    payload = catalog_payload(
        cache,
        agent="cursor",
        server_keys=[str(tool.get("server_key") or "")],
    )
    gherkin_context.payload["transformed_tool"] = _tool_by_name(payload["tools"], str(tool.get("name") or ""))
    gherkin_context.payload["identity_stage"] = IdentityStage.RUNTIME_CACHE


@when("hook cache normalizes the tool catalog entry")
def when_hook_cache_normalize(gherkin_context: GherkinContext) -> None:
    from cyt.cyt_mcp.catalog import _normalize_tools_list

    tool = gherkin_context.payload.get("source_tool") or {}
    normalized = _normalize_tools_list([copy.deepcopy(tool)])
    gherkin_context.payload["transformed_tool"] = _tool_by_name(
        normalized,
        str(tool.get("name") or ""),
    )
    gherkin_context.payload["identity_stage"] = IdentityStage.HOOK_CACHE


@then(parsers.parse("tool backend identity should match reference {tool_ref}"))
def then_backend_identity_matches(tool_ref: str, gherkin_context: GherkinContext) -> None:
    ref = load_propagation_reference_tool(tool_ref)
    source = gherkin_context.payload.get("source_tool") or {}
    after = (
        gherkin_context.payload.get("prepared_tool")
        or gherkin_context.payload.get("transformed_tool")
        or source
    )
    tier = str(gherkin_context.payload.get("tier") or "")
    stage = gherkin_context.payload.get("identity_stage") or (
        IdentityStage.TIER_PREP if tier else IdentityStage.BACKEND
    )
    if source and after is not source:
        assert_tier_identity_preserved(source, after, tier=tier, stage=stage)
    assert_identity_matches_reference(
        after,
        wire_name=ref.id,
        server_key=ref.server_key,
        tool_name=ref.tool_name,
        stage=stage,
    )
    if source:
        assert tool_entity_id_from_tool(source) == tool_entity_id_from_tool(after)


@when("session gate builds Type-1 log from pruned subset only")
def when_build_type1_from_pruned_subset(gherkin_context: GherkinContext) -> None:
    pruned = gherkin_context.payload.get("pruned_subset") or []
    master = gherkin_context.payload.get("master_catalog") or _master_from_full_disk()
    _kept, log_entries, _flags = gate_tools_for_session(
        pruned,
        config={"tools": {"hook": {"tools_from": ["cyt_mcp"]}}},
        session_text="",
        index=SessionLogIndex(entries=()),
        catalog_tools=master,
    )
    gherkin_context.payload["type1_entries"] = log_entries


@when("session gate builds Type-1 tool log entry with master catalog peers")
def when_build_type1(gherkin_context: GherkinContext) -> None:
    pruned = gherkin_context.payload.get("pruned_tool")
    master = gherkin_context.payload.get("master_catalog") or _master_from_full_disk()
    assert isinstance(pruned, dict)
    _kept, log_entries, _flags = gate_tools_for_session(
        [pruned],
        config={"tools": {"hook": {"tools_from": ["cyt_mcp"]}}},
        session_text="",
        index=SessionLogIndex(entries=()),
        catalog_tools=master,
    )
    gherkin_context.payload["type1_entries"] = log_entries


@when("catalog is fetched without disk enrichment")
def when_fetch_partial(gherkin_context: GherkinContext) -> None:
    workspace = gherkin_context.payload.get("workspace")
    config = gherkin_context.payload.get("config")
    assert workspace is not None and config is not None
    fetched = _fetch_catalog_from_registry(config, allow_stale=True)
    gherkin_context.payload["fetched_catalog"] = fetched


@then(
    parsers.parse(
        "Type-2 record {tool_name} should have required properties {properties}",
    ),
)
def then_type2_required(tool_name: str, properties: str, gherkin_context: GherkinContext) -> None:
    type2_tools = gherkin_context.payload.get("type2_tools") or []
    record = tool_record_from_type2(type2_tools, tool_name)
    cleaned = properties.replace(" only", "").strip()
    expected = {part.strip() for part in cleaned.split() if part.strip()}
    assert required_names(schema_from_tool(record)) == expected


@then(parsers.parse("Type-1 catalog should not include {tool_name}"))
def then_type1_excludes(tool_name: str, gherkin_context: GherkinContext) -> None:
    entries = gherkin_context.payload.get("type1_entries") or []
    type1_names = {
        str(entry.get("name") or "")
        for entry in entries
        if entry.get("kind") == "tool"
    }
    assert tool_name not in type1_names


@then(parsers.parse("Type-2 catalog should include {tool_name}"))
def then_type2_includes(tool_name: str, gherkin_context: GherkinContext) -> None:
    type2_tools = gherkin_context.payload.get("type2_tools") or []
    names = {str(t.get("name") or "") for t in type2_tools}
    assert tool_name in names


@then("Type-2 catalog tool count should exceed pruned subset count")
def then_type2_larger_than_pruned(gherkin_context: GherkinContext) -> None:
    type2_tools = gherkin_context.payload.get("type2_tools") or []
    pruned = gherkin_context.payload.get("pruned_subset") or []
    assert len(type2_tools) > len(pruned)


@then("injected schema should have required query only without optional limit")
def then_t2_injected_required_only(gherkin_context: GherkinContext) -> None:
    merged = gherkin_context.payload.get("merged_tool") or {}
    backend = gherkin_context.payload.get("backend_schema") or {}
    assert_tier_injected_schema(merged, "T2", backend, tool_name=str(merged.get("name") or ""))
    assert optional_names(schema_from_tool(merged)) == set()


@then("injected schema should have required query and optional limit")
def then_t3_injected_full(gherkin_context: GherkinContext) -> None:
    merged = gherkin_context.payload.get("merged_tool") or {}
    backend = gherkin_context.payload.get("backend_schema") or {}
    assert_tier_injected_schema(merged, "T3", backend, tool_name=str(merged.get("name") or ""))
    assert "limit" in optional_names(schema_from_tool(merged))


@then(
    parsers.parse(
        "Type-1 record {tool_name} should have required properties {properties}",
    ),
)
def then_type1_required(tool_name: str, properties: str, gherkin_context: GherkinContext) -> None:
    entries = gherkin_context.payload.get("type1_entries") or []
    type1 = next(entry for entry in entries if entry.get("kind") == "tool")
    expected_props = {part.strip() for part in properties.split() if part.strip()}
    master = gherkin_context.payload.get("master_catalog") or _master_from_full_disk()
    backend = schema_from_tool(_tool_by_name(master, tool_name))
    assert_required_equal("type1", schema_from_tool(type1), backend, tool_name=tool_name)
    assert required_names(schema_from_tool(type1)) == expected_props


@then(parsers.parse("property {prop} must not appear on {tool_name} schema"))
def then_no_cross_leak(prop: str, tool_name: str, gherkin_context: GherkinContext) -> None:
    owner = gherkin_context.payload.get("tool_a") or {}
    other = gherkin_context.payload.get("tool_b") or {}
    if str(other.get("name") or "") != tool_name:
        owner, other = other, owner
    assert_no_cross_tool_leakage([owner, other], prop, str(owner.get("name") or ""))


@then(parsers.parse("fetched {tool_name} required should be {properties} only"))
def then_fetched_partial_required(
    tool_name: str,
    properties: str,
    gherkin_context: GherkinContext,
) -> None:
    fetched = gherkin_context.payload.get("fetched_catalog") or []
    tool = _tool_by_name(fetched, tool_name)
    expected = properties.replace(" only", "").strip().split()
    assert required_names(schema_from_tool(tool)) == set(expected)


@then(
    parsers.parse(
        "Type-2 record {tool_name} should have server_key {server_key} and tool_name {bare_name}",
    ),
)
def then_type2_identity(
    tool_name: str,
    server_key: str,
    bare_name: str,
    gherkin_context: GherkinContext,
) -> None:
    type2_tools = gherkin_context.payload.get("type2_tools") or []
    record = tool_record_from_type2(type2_tools, tool_name)
    assert record["server_key"] == server_key
    assert record["tool_name"] == bare_name
    assert_backend_identity_preserved(record)


@then(
    parsers.parse(
        "Type-1 record {tool_name} should have server_key {server_key} and tool_name {bare_name}",
    ),
)
def then_type1_identity(
    tool_name: str,
    server_key: str,
    bare_name: str,
    gherkin_context: GherkinContext,
) -> None:
    entries = gherkin_context.payload.get("type1_entries") or []
    record = next(entry for entry in entries if entry.get("kind") == "tool")
    assert record["name"] == tool_name
    assert record["server_key"] == server_key
    assert record["tool_name"] == bare_name
    assert_backend_identity_preserved(record)


@given("agent cursor")
def given_agent_cursor(gherkin_context: GherkinContext) -> None:
    gherkin_context.agent = "cursor"


@given(
    parsers.parse(
        "a Type-2 cyt_mcp catalog with tool {tool_name} query string repo string required",
    ),
)
def given_type2_semble_for_gate(
    tool_name: str,
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "session.jsonl"
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "repo": {"type": "string"},
        },
        "required": ["query", "repo"],
    }
    _write_session(log_path, _cyt_mcp_catalog(tool_name, schema))
    gherkin_context.payload = {"log_path": log_path, "session_id": "session-1"}


@when(
    parsers.parse(
        "preToolUse validates cyt-mcp tool {tool_name} with args {args}",
    ),
)
def when_validate_cyt_mcp_gate(tool_name: str, args: str, gherkin_context: GherkinContext) -> None:
    arg_parts = [part.strip() for part in args.split() if part.strip()]
    tool_input: dict[str, str] = {}
    for index in range(0, len(arg_parts) - 1, 2):
        tool_input[arg_parts[index]] = arg_parts[index + 1]
    payload = {
        "hook_event_name": "preToolUse",
        "session_id": gherkin_context.payload.get("session_id", "session-1"),
        "tool_name": tool_name,
        "tool_input": tool_input,
        "cyt_agent": gherkin_context.agent,
    }
    monkeypatch = pytest.MonkeyPatch()
    _patch_session_log_path(monkeypatch, gherkin_context)
    validation = validate_pre_tool_call(payload)
    gherkin_context.payload["allowed"] = validation.allowed
    gherkin_context.payload["reason"] = validation.reason
    monkeypatch.undo()


@then("validation should allow")
def then_validation_allow(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload.get("allowed") is True
