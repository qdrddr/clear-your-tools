"""Integration regression: session JSONL Type-1/Type-2 records carry cyt_mcp backend identity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cyt.injection.session_gate import gate_tools_for_session
from cyt.injection.session_log import SessionLogIndex
from cyt.injection.tool_catalog_emit import emit_tool_catalog_session_log
from cyt.tools.injection_schema import ensure_tool_injection_schema
from cyt.tools.schema_propagation_contract import assert_backend_identity_preserved
from cyt_client.session_capture import (
    merge_tool_into_cyt_mcp_catalog,
    persist_cyt_mcp_search_result,
)
from cyt_client.session_pre_tool_exposure import build_type1_tool_entry_from_catalog_record
from cyt_client.sessions import read_session_log_file
from cyt_client.tool_gate import extract_post_tool_example_capture
from tests.support.tool_schema_completeness_fixtures import (
    FULL_WS_DISK_CATALOG_PATH,
    load_propagation_reference_tool,
    load_session_log_identity_contract,
    load_tool_list,
    parse_session_log_entries,
    partial_schema_from_backend,
    resolve_reference_tool_from_catalogs,
)

pytestmark = pytest.mark.integration


def test_hook_emit_type2_and_type1_both_carry_backend_identity() -> None:
    ref = load_propagation_reference_tool("semble_search")
    master = load_tool_list(FULL_WS_DISK_CATALOG_PATH)
    type2_entries = emit_tool_catalog_session_log(master, payload={}, tools_inject_enabled=True)
    type2_tools = next(
        entry["tools"]
        for entry in type2_entries
        if entry.get("kind") == "tool_catalog" and entry.get("catalog") == "cyt_mcp"
    )
    type2_record = next(t for t in type2_tools if t["name"] == ref.id)
    assert_backend_identity_preserved(type2_record)

    full_tool = next(t for t in master if t["name"] == ref.id)
    partial = dict(full_tool)
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
    assert_backend_identity_preserved(type1)
    assert type1["server_key"] == ref.server_key
    assert type1["tool_name"] == ref.tool_name


def test_client_search_persist_writes_type2_with_backend_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block = load_session_log_identity_contract()
    capture = block["capture_enrichment"]
    wire_name = str(capture["wire_name"])
    definition = {
        "name": wire_name,
        "inputSchema": capture["input_schema"],
    }
    log_path = tmp_path / "session.jsonl"
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "identity-session",
        "tool_name": "mcp__cyt-mcp__get-tool-definitions",
        "tool_input": {"tool_name": wire_name},
        "tool_output": json.dumps(definition),
    }
    monkeypatch.setattr("cyt_client.session_capture.session_log_path", lambda _payload: log_path)
    assert persist_cyt_mcp_search_result(payload) is True

    _agent, entries = read_session_log_file(log_path)
    catalog = next(entry for entry in entries if entry.get("kind") == "tool_catalog")
    record = next(t for t in catalog["tools"] if t["name"] == wire_name)
    assert record["server_key"] == capture["expected_server_key"]
    assert record["tool_name"] == capture["expected_tool_name"]
    assert_backend_identity_preserved(record)


def test_client_pre_tool_deny_type1_matches_type2_identity(
    tmp_path: Path,
) -> None:
    ref = load_propagation_reference_tool("semble_search")
    tool = resolve_reference_tool_from_catalogs(ref.id)
    type2_record = merge_tool_into_cyt_mcp_catalog(
        Path("unused"),
        ref.id,
        {
            "name": ref.id,
            "inputSchema": tool["input_schema"],
            "server_key": ref.server_key,
            "tool_name": ref.tool_name,
        },
    )
    catalog_tool = next(t for t in type2_record["tools"] if t["name"] == ref.id)
    type1 = build_type1_tool_entry_from_catalog_record(
        catalog_tool,
        catalog="cyt_mcp",
        full=True,
    )
    assert_backend_identity_preserved(type1)
    assert type1["server_key"] == ref.server_key
    assert type1["tool_name"] == ref.tool_name


def test_post_tool_capture_uses_explicit_type2_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref = load_propagation_reference_tool("semble_search")
    tool = resolve_reference_tool_from_catalogs(ref.id)
    log_path = tmp_path / "session.jsonl"
    log_path.write_text(
        json.dumps(
            {
                "kind": "session_state",
                "key": "session_state:inject",
                "tools_inject_enabled": True,
            },
        )
        + "\n"
        + json.dumps(
            {
                "kind": "tool_catalog",
                "key": "tool_catalog:cyt_mcp",
                "catalog": "cyt_mcp",
                "hash": "identity-test",
                "tools": [
                    {
                        "name": ref.id,
                        "server_key": ref.server_key,
                        "tool_name": ref.tool_name,
                        "input_schema": tool["input_schema"],
                    },
                ],
            },
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("cyt_client.tool_gate.session_log_path", lambda _payload: log_path)
    capture = extract_post_tool_example_capture(
        {
            "hook_event_name": "postToolUse",
            "tool_name": f"MCP:{ref.id}",
            "tool_input": {"query": "bm25", "repo": "/tmp/repo"},
            "tool_output": json.dumps({"results": []}),
        },
    )
    assert capture is not None
    assert capture["mcp_server"] == ref.server_key
    assert capture["tool_name"] == ref.tool_name


def test_parsed_session_jsonl_type1_and_type2_identity_from_hook_inject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt.skills.cli import run_hook_payload
    from tests.support.cyt_mcp_catalog_resilience_fixtures import (
        capture_registry_registrations,
        patch_daemon_catalog_status,
    )
    from tests.support.tool_schema_completeness_fixtures import (
        cyt_mcp_hook_config,
        materialize_workspace,
        register_ws_catalog,
    )

    ref = load_propagation_reference_tool("semble_search")
    workspace = materialize_workspace(tmp_path)
    config = cyt_mcp_hook_config(workspace, db_path=tmp_path / "tiers.db")
    master = load_tool_list(FULL_WS_DISK_CATALOG_PATH)
    register_ws_catalog(workspace, master)
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())

    result = run_hook_payload(
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Locate BM25 implementation using semble_search in repo",
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
    type2 = next(t for t in type2_tools if t["name"] == ref.id)
    assert_backend_identity_preserved(type2)
    if ref.id in parsed["type1_by_name"]:
        type1 = parsed["type1_by_name"][ref.id]
        assert_backend_identity_preserved(type1)
