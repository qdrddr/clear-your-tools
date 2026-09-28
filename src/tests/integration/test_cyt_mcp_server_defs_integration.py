"""Integration tests: workspace MCP server defs JSON contract and cyt-mcp startup inputs."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from cyt.cyt_mcp.catalog import clear_cyt_mcp_catalog_cache
from cyt.tiers.cli import main as tiers_main
from cyt.tiers.manager import _managers
from cyt.tools.master_catalog import (
    clear_master_catalog_cache,
    get_master_tool_catalog,
    rebuild_master_catalog,
)
from cyt_mcp.config import load_aggregator_config
from tests.support.cyt_mcp_catalog_resilience_fixtures import (
    capture_registry_registrations,
    load_resilience_scenario,
    load_ws_tools_catalog,
    materialize_workspace,
    patch_daemon_catalog_status,
    patch_resilience_mcp_server_keys,
    patch_tiers_stats_config,
    register_dual_layer_catalog,
    register_ws_catalog,
    reset_catalog_state,
    write_restart_polluted_usr_scope_disk_catalog,
)
from tests.support.cyt_mcp_server_defs_fixtures import (
    assert_mcp_server_defs_is_json,
    load_server_defs_scenario,
    materialize_workspace_with_mcp_config,
)


@pytest.fixture(autouse=True)
def _isolate_catalog_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("cyt.hook.active_workspace.touch_active_workspace", lambda *_a, **_k: None)
    monkeypatch.setenv("CYT_HOOK_QUIET", "1")
    patch_resilience_mcp_server_keys(monkeypatch)
    reset_catalog_state()
    _managers.clear()
    yield
    reset_catalog_state()
    _managers.clear()


def test_workspace_setup_migration_produces_json_backend_defs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cyt.tools.cyt_mcp_setup as cyt_mcp_setup
    from cyt.hook.install_scope import CytInstallScope

    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    project_mcp = workspace / ".cursor" / "mcp.json"
    project_mcp.parent.mkdir(parents=True)
    project_mcp.write_text(
        json.dumps({"mcpServers": {"backend-a": {"command": "echo"}}}),
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    scope = CytInstallScope(workspace_root=workspace)

    cyt_mcp_setup.setup_cyt_mcp_workspace_for_agent(
        "cursor",
        scope=scope,
        migrate_backends=True,
        verify_only=True,
    )

    scenario = load_server_defs_scenario("workspace_cursor_json_contract")
    defs_path = workspace / scenario.raw["backend_defs_relpath"]
    payload = assert_mcp_server_defs_is_json(defs_path)
    assert "backend-a" in payload["mcpServers"]


def test_yaml_cursor_defs_allow_aggregator_load_without_json_parse_failure(
    tmp_path: Path,
) -> None:
    scenario = load_server_defs_scenario("yaml_cursor_defs_recovery")
    workspace = materialize_workspace_with_mcp_config(tmp_path, cursor_defs="yaml")
    defs_path = workspace / scenario.raw["backend_defs_relpath"]
    with pytest.raises(json.JSONDecodeError):
        json.loads(defs_path.read_text(encoding="utf-8"))

    config = load_aggregator_config(
        agent="cursor",
        aggregator_path=workspace / ".agents/cyt/config/mcp-config.yaml",
        workspace_folder=workspace,
    )
    assert set(config.mcp_servers) == set(scenario.raw["expected_server_keys"])


def test_master_catalog_excludes_restart_tool_from_disk_merge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_resilience_scenario("disk_merge_excludes_restart_tool")
    workspace = materialize_workspace(tmp_path)
    config = patch_tiers_stats_config(monkeypatch, workspace, db_path=tmp_path / "tiers.db")
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
    write_restart_polluted_usr_scope_disk_catalog(monkeypatch, tmp_path / "cyt-mcp-catalog")

    rebuild_master_catalog(config, blocking=True, cold_start=True)
    catalog = get_master_tool_catalog(config, blocking=False)
    assert catalog is not None
    names = {tool["name"] for tool in catalog}
    assert names == set(scenario.raw["expected_merged_tool_names"])
    for invalid_name in scenario.raw["invalid_identity_tool_names"]:
        assert invalid_name not in names


def test_tiers_stats_excludes_restart_tool_from_user_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenario = load_resilience_scenario("disk_merge_excludes_restart_tool")
    workspace = materialize_workspace(tmp_path)
    register_dual_layer_catalog(workspace)
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())
    patch_tiers_stats_config(monkeypatch, workspace, db_path=tmp_path / "tiers.db")
    write_restart_polluted_usr_scope_disk_catalog(monkeypatch, tmp_path / "cyt-mcp-catalog")

    code = tiers_main(["stats", "--workspace", str(workspace), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    troubleshooting = payload["overview"]["troubleshooting"]
    assert troubleshooting["catalog_user_tool_count"] == scenario.raw["expected_user_tool_count"]
    assert (
        troubleshooting["catalog_workspace_tool_count"]
        == scenario.raw["expected_workspace_tool_count"]
    )
    assert troubleshooting["catalog_tool_count"] == scenario.raw["expected_total_tools"]
