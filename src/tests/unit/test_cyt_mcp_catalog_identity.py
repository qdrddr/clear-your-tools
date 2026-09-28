"""Unit tests: cyt-mcp catalog tools missing server_key/tool_name must not break hooks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cyt.cyt_mcp.catalog import (
    _drop_cyt_mcp_tools_missing_identity,
    _normalize_tools_list,
    _ready_catalog_tools,
    clear_cyt_mcp_catalog_cache,
)
from cyt.injection.session_log_build import build_tool_catalog_log_entry
from cyt.injection.tool_catalog_emit import append_tool_catalog_to_details
from cyt.tools.hook import handle_user_prompt_tools
from cyt.tools.master_catalog import (
    clear_master_catalog_cache,
    get_master_tool_catalog,
    rebuild_master_catalog,
)
from tests.support.cyt_mcp_catalog_resilience_fixtures import (
    capture_registry_registrations,
    cyt_mcp_hook_config,
    load_polluted_usr_tools_catalog,
    load_resilience_scenario,
    load_ws_tools_catalog,
    materialize_workspace,
    patch_daemon_catalog_status,
    patch_resilience_mcp_server_keys,
    register_ws_catalog,
    reset_catalog_state,
    write_usr_scope_disk_catalog,
)


@pytest.fixture(autouse=True)
def _reset_catalog_caches() -> None:
    reset_catalog_state()


def test_drop_cyt_mcp_tools_missing_identity_removes_test_pollution() -> None:
    scenario = load_resilience_scenario("usr_disk_merge_excludes_pollution")
    invalid = set(scenario.raw["invalid_identity_tool_names"])

    dropped = _drop_cyt_mcp_tools_missing_identity(
        [
            {"name": "alpha", "input_schema": {}},
            {"name": "beta", "input_schema": {}},
            {
                "name": "semble_search",
                "server_key": "semble",
                "tool_name": "search",
                "input_schema": {"type": "object"},
            },
        ],
    )
    names = {str(tool.get("name") or "") for tool in dropped}
    assert invalid.isdisjoint(names)
    assert names == {"semble_search"}


def test_normalize_tools_list_excludes_invalid_identity_tools() -> None:
    normalized = _normalize_tools_list(load_polluted_usr_tools_catalog())
    names = {str(tool.get("name") or "") for tool in normalized}
    assert "alpha" not in names
    assert "beta" not in names
    assert "context7_resolve-library-id" in names
    assert "context7_query-docs" in names
    for tool in normalized:
        assert str(tool.get("server_key") or "").strip()
        assert str(tool.get("tool_name") or "").strip()


def test_build_tool_catalog_log_entry_rejects_unfiltered_invalid_tools() -> None:
    with pytest.raises(ValueError, match="missing explicit server_key/tool_name mapping"):
        build_tool_catalog_log_entry(
            "cyt_mcp",
            [{"name": "alpha", "input_schema": {}}],
        )


def test_emit_tool_catalog_session_log_succeeds_for_identity_safe_catalog() -> None:
    catalog = _normalize_tools_list(load_ws_tools_catalog())
    for tool in catalog:
        tool["cyt_catalog_source"] = "cyt_mcp"
    details: dict[str, Any] = {}
    append_tool_catalog_to_details(
        details,
        catalog,
        payload={},
        tools_inject_enabled=True,
    )
    session_log = details.get("session_log")
    assert isinstance(session_log, list)
    catalog_entries = [entry for entry in session_log if entry.get("kind") == "tool_catalog"]
    assert len(catalog_entries) == 1
    assert catalog_entries[0]["catalog"] == "cyt_mcp"
    assert catalog_entries[0]["tools"]


def test_master_catalog_excludes_polluted_usr_disk_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_resilience_scenario("usr_disk_merge_excludes_pollution")
    workspace = materialize_workspace(tmp_path)
    config = cyt_mcp_hook_config(workspace, db_path=tmp_path / "tiers.db")

    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())
    monkeypatch.setattr(
        "cyt.hook.catalog_registry._sync_live_registrations_from_daemon",
        lambda: 0,
    )
    monkeypatch.setattr(
        "cyt.tools.master_catalog._hydrate_master_from_disk_if_empty",
        lambda *_args, **_kwargs: None,
    )
    clear_master_catalog_cache()
    clear_cyt_mcp_catalog_cache()
    register_ws_catalog(workspace, load_ws_tools_catalog())
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())
    write_usr_scope_disk_catalog(
        monkeypatch,
        tmp_path / "cyt-mcp-catalog",
        usr_tools=load_polluted_usr_tools_catalog(),
    )

    rebuild_master_catalog(config, blocking=True, cold_start=True)
    catalog = get_master_tool_catalog(config, blocking=False)
    assert catalog is not None
    names = {str(tool.get("name") or "") for tool in catalog}
    for invalid_name in scenario.raw["invalid_identity_tool_names"]:
        assert invalid_name not in names
    assert names == set(scenario.raw["expected_merged_tool_names"])


def test_handle_user_prompt_tools_survives_polluted_master_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_resilience_scenario("hook_inject_polluted_usr_disk")
    workspace = materialize_workspace(tmp_path)
    config = cyt_mcp_hook_config(workspace, db_path=tmp_path / "tiers.db")

    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())
    monkeypatch.setattr(
        "cyt.hook.catalog_registry._sync_live_registrations_from_daemon",
        lambda: 0,
    )
    monkeypatch.setattr(
        "cyt.tools.master_catalog._hydrate_master_from_disk_if_empty",
        lambda *_args, **_kwargs: None,
    )
    clear_master_catalog_cache()
    clear_cyt_mcp_catalog_cache()
    register_ws_catalog(workspace, load_ws_tools_catalog())
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())
    write_usr_scope_disk_catalog(
        monkeypatch,
        tmp_path / "cyt-mcp-catalog",
        usr_tools=load_polluted_usr_tools_catalog(),
    )
    rebuild_master_catalog(config, blocking=True, cold_start=True)

    payload = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": scenario.raw["prompt"],
        "cwd": str(workspace),
        "workspace_roots": [str(workspace)],
        "model": "claude-sonnet-4-20250514",
    }
    outcome, details, injection = handle_user_prompt_tools(payload, config)
    assert outcome not in {
        "skipped_cyt_mcp_unavailable",
        "skipped_missing_tools_catalog",
    }
    session_log = details.get("session_log")
    assert isinstance(session_log, list)
    assert any(entry.get("kind") == "tool_catalog" for entry in session_log)
    for tool_name in scenario.raw["expected_tool_names_in_stdout"]:
        assert tool_name in injection


def test_master_catalog_excludes_restart_tool_from_disk_merge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_resilience_scenario("disk_merge_excludes_restart_tool")
    workspace = materialize_workspace(tmp_path)
    config = cyt_mcp_hook_config(workspace, db_path=tmp_path / "tiers.db")

    patch_resilience_mcp_server_keys(monkeypatch)
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())
    monkeypatch.setattr(
        "cyt.hook.catalog_registry._sync_live_registrations_from_daemon",
        lambda: 0,
    )
    monkeypatch.setattr(
        "cyt.tools.master_catalog._hydrate_master_from_disk_if_empty",
        lambda *_args, **_kwargs: None,
    )
    clear_master_catalog_cache()
    clear_cyt_mcp_catalog_cache()
    register_ws_catalog(workspace, load_ws_tools_catalog())
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())
    from tests.support.cyt_mcp_catalog_resilience_fixtures import (
        write_restart_polluted_usr_scope_disk_catalog,
    )

    write_restart_polluted_usr_scope_disk_catalog(monkeypatch, tmp_path / "cyt-mcp-catalog")

    rebuild_master_catalog(config, blocking=True, cold_start=True)
    catalog = get_master_tool_catalog(config, blocking=False)
    assert catalog is not None
    names = {str(tool.get("name") or "") for tool in catalog}
    for invalid_name in scenario.raw["invalid_identity_tool_names"]:
        assert invalid_name not in names
    assert names == set(scenario.raw["expected_merged_tool_names"])


def test_ready_catalog_tools_filters_before_permissions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = materialize_workspace(tmp_path)
    config = cyt_mcp_hook_config(workspace, db_path=tmp_path / "tiers.db")
    tools = [
        {"name": "alpha", "input_schema": {}},
        {
            "name": "gitnexus_cypher",
            "server_key": "gitnexus",
            "tool_name": "cypher",
            "input_schema": {"type": "object"},
        },
    ]
    ready = _ready_catalog_tools(config, tools)
    names = {str(tool.get("name") or "") for tool in ready}
    assert "alpha" not in names
    assert "gitnexus_cypher" in names
