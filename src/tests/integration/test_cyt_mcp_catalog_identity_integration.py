"""Integration tests: polluted cyt-mcp catalog cache must not break hook injection.

Opt in with::

    uv run pytest src/tests/integration/test_cyt_mcp_catalog_identity_integration.py --run-integration -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt.cyt_mcp.catalog import clear_cyt_mcp_catalog_cache, get_cyt_mcp_catalog
from cyt.injection.tool_catalog_emit import append_tool_catalog_to_details
from cyt.skills.cli import run_hook_payload
from cyt.tools.master_catalog import (
    clear_master_catalog_cache,
    get_master_tool_catalog,
    rebuild_master_catalog,
)
from tests.support.cyt_mcp_catalog_resilience_fixtures import (
    capture_registry_registrations,
    cyt_mcp_hook_config,
    load_resilience_scenario,
    load_ws_tools_catalog,
    materialize_workspace,
    patch_daemon_catalog_status,
    register_ws_catalog,
    reset_catalog_state,
    write_polluted_usr_scope_disk_catalog,
)


@pytest.fixture(autouse=True)
def _reset_catalog_state() -> None:
    reset_catalog_state()


def _bootstrap_polluted_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict]:
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
    write_polluted_usr_scope_disk_catalog(monkeypatch, tmp_path / "cyt-mcp-catalog")
    rebuild_master_catalog(config, blocking=True, cold_start=True)
    return workspace, config


@pytest.mark.integration
def test_polluted_disk_catalog_to_hook_inject_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_resilience_scenario("hook_inject_polluted_usr_disk")
    workspace, config = _bootstrap_polluted_workspace(tmp_path, monkeypatch)

    catalog = get_cyt_mcp_catalog(config, blocking=True, cold_start=True)
    assert catalog is not None
    for invalid_name in scenario.raw["invalid_identity_tool_names"]:
        assert invalid_name not in {tool.get("name") for tool in catalog}

    details: dict = {}
    append_tool_catalog_to_details(
        details,
        catalog,
        payload={},
        tools_inject_enabled=True,
    )
    assert any(entry.get("kind") == "tool_catalog" for entry in details.get("session_log", []))

    payload = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": scenario.raw["prompt"],
        "cwd": str(workspace),
        "workspace_roots": [str(workspace)],
        "model": "claude-sonnet-4-20250514",
    }
    result = run_hook_payload(payload, config)
    assert result.outcome not in {
        "skipped_cyt_mcp_unavailable",
        "skipped_missing_tools_catalog",
        "user_prompt_no_tool_matches",
    }
    for tool_name in scenario.raw["expected_tool_names_in_stdout"]:
        assert tool_name in result.stdout_text


@pytest.mark.integration
def test_polluted_master_catalog_matches_clean_merge_expectations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_resilience_scenario("usr_disk_merge_excludes_pollution")
    _workspace, config = _bootstrap_polluted_workspace(tmp_path, monkeypatch)

    catalog = get_master_tool_catalog(config, blocking=False)
    assert catalog is not None
    names = {str(tool.get("name") or "") for tool in catalog}
    assert names == set(scenario.raw["expected_merged_tool_names"])
    for invalid_name in scenario.raw["invalid_identity_tool_names"]:
        assert invalid_name not in names
