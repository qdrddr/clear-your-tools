"""Integration tests for tool schema completeness across hook inject and catalog fetch."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cyt.cyt_mcp.catalog import _fetch_catalog_from_registry
from cyt.skills.cli import run_hook_payload
from cyt.tiers.manager import _managers
from cyt_client.tool_gate import validate_pre_tool_call
from tests.support.cyt_mcp_catalog_resilience_fixtures import (
    capture_registry_registrations,
    patch_daemon_catalog_status,
)
from tests.support.tool_schema_completeness_fixtures import (
    cyt_mcp_hook_config,
    load_scenario,
    load_tool_list,
    materialize_workspace,
    register_ws_catalog,
    reset_catalog_state,
    write_full_disk_catalog,
)
from tests.support.tool_schema_completeness_fixtures import (
    FULL_WS_DISK_CATALOG_PATH,
    PARTIAL_WS_REGISTRY_PATH,
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
    )
    assert result.outcome not in {
        "skipped_cyt_mcp_unavailable",
        "skipped_missing_tools_catalog",
        "user_prompt_no_tool_matches",
    }
    for name in scenario.raw["expected_tool_names_in_stdout"]:
        assert name in result.stdout_text
    for marker in scenario.raw["expected_injection_markers"]:
        assert marker in result.stdout_text


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


def test_hook_inject_gitnexus_stays_query_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Other tools must not pick up spurious required fields from semble enrichment."""
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
    )
    assert "gitnexus_cypher" in result.stdout_text
    gitnexus_block_start = result.stdout_text.index("name='gitnexus_cypher'")
    gitnexus_block = result.stdout_text[gitnexus_block_start : gitnexus_block_start + 400]
    assert "'query'" in gitnexus_block
    assert "'repo'" not in gitnexus_block
