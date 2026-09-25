"""Integration tests: usr + ws cyt-mcp catalog union in tiers stats."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from cyt.cyt_mcp.catalog import clear_cyt_mcp_catalog_cache
from cyt.tiers.cli import main as tiers_main
from cyt.tiers.manager import _managers
from cyt.tools.master_catalog import clear_master_catalog_cache, get_master_tool_catalog
from tests.support.cyt_mcp_catalog_resilience_fixtures import (
    capture_registry_registrations,
    load_resilience_scenario,
    load_ws_tools_catalog,
    materialize_workspace,
    patch_daemon_catalog_status,
    patch_tiers_stats_config,
    register_dual_layer_catalog,
    register_ws_catalog,
    reset_catalog_state,
    write_usr_scope_disk_catalog,
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


def test_tiers_stats_default_scope_all_and_dual_layer_total(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenario = load_resilience_scenario("usr_ws_union_tiers_stats")
    workspace = materialize_workspace(tmp_path)
    ws_tools, usr_tools = register_dual_layer_catalog(workspace)
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())
    patch_tiers_stats_config(monkeypatch, workspace, db_path=tmp_path / "tiers.db")

    code = tiers_main(["stats", "--workspace", str(workspace)])
    assert code == 0
    out = capsys.readouterr().out

    expected_total = int(scenario.raw["expected_total_tools"])
    assert "scope: all" in out
    assert f"Total  {expected_total}" in out
    assert len(ws_tools) + len(usr_tools) == expected_total


def test_tiers_stats_json_dual_layer_catalog_breakdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenario = load_resilience_scenario("usr_ws_union_tiers_stats")
    workspace = materialize_workspace(tmp_path)
    register_dual_layer_catalog(workspace)
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())
    patch_tiers_stats_config(monkeypatch, workspace, db_path=tmp_path / "tiers.db")

    code = tiers_main(["stats", "--workspace", str(workspace), "--json"])
    assert code == 0

    payload = json.loads(capsys.readouterr().out)
    troubleshooting = payload["overview"]["troubleshooting"]
    tier_total = payload["overview"]["tier_statistics"]["tools"]["totals"]["count"]

    assert payload.get("scope") == scenario.raw["expected_scope"]
    assert troubleshooting["catalog_user_tool_count"] == scenario.raw["expected_user_tool_count"]
    assert (
        troubleshooting["catalog_workspace_tool_count"]
        == scenario.raw["expected_workspace_tool_count"]
    )
    assert troubleshooting["catalog_tool_count"] == scenario.raw["expected_total_tools"]
    assert tier_total == scenario.raw["expected_total_tools"]


def test_tiers_stats_verbose_troubleshooting_shows_scope_breakdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenario = load_resilience_scenario("usr_ws_union_tiers_stats")
    workspace = materialize_workspace(tmp_path)
    register_dual_layer_catalog(workspace)
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())
    patch_tiers_stats_config(monkeypatch, workspace, db_path=tmp_path / "tiers.db")

    code = tiers_main(["stats", "--workspace", str(workspace), "--verbose"])
    assert code == 0
    out = capsys.readouterr().out

    assert "user=2" in out
    assert "workspace=3" in out
    assert "get-tool-definitions" in out
    assert int(scenario.raw["expected_total_tools"]) == 5


def test_tiers_stats_ws_only_registry_regression_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When usr layer is missing, tiers stats must not silently claim the usr union."""
    workspace = materialize_workspace(tmp_path)
    ws_tools = load_ws_tools_catalog()
    register_ws_catalog(workspace, ws_tools)
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())
    patch_tiers_stats_config(monkeypatch, workspace, db_path=tmp_path / "tiers.db")

    code = tiers_main(["stats", "--workspace", str(workspace), "--json"])
    assert code == 0

    payload = json.loads(capsys.readouterr().out)
    troubleshooting = payload["overview"]["troubleshooting"]
    assert troubleshooting["catalog_workspace_tool_count"] == len(ws_tools)
    assert troubleshooting["catalog_user_tool_count"] == 0
    assert troubleshooting["catalog_tool_count"] == len(ws_tools)


def test_master_catalog_merges_usr_disk_when_registry_has_ws_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_resilience_scenario("usr_disk_merge_ws_registry")
    workspace = materialize_workspace(tmp_path)
    config = patch_tiers_stats_config(monkeypatch, workspace, db_path=tmp_path / "tiers.db")
    register_ws_catalog(workspace, load_ws_tools_catalog())
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())
    clear_cyt_mcp_catalog_cache()
    clear_master_catalog_cache()
    write_usr_scope_disk_catalog(monkeypatch, tmp_path / "cyt-mcp-catalog")

    catalog = get_master_tool_catalog(config, blocking=True)
    assert catalog is not None
    names = {tool["name"] for tool in catalog}
    assert names == set(scenario.raw["expected_merged_tool_names"])


def test_tiers_stats_after_registry_clear_hydrates_dual_layer_total(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenario = load_resilience_scenario("usr_ws_union_tiers_stats")
    workspace = materialize_workspace(tmp_path)
    register_dual_layer_catalog(workspace)
    captured = capture_registry_registrations()
    reset_catalog_state(purge_disk_snapshot=False)
    clear_cyt_mcp_catalog_cache()
    clear_master_catalog_cache()
    patch_daemon_catalog_status(monkeypatch, captured)
    patch_tiers_stats_config(monkeypatch, workspace, db_path=tmp_path / "tiers.db")

    code = tiers_main(["stats", "--workspace", str(workspace), "--json"])
    assert code == 0

    payload = json.loads(capsys.readouterr().out)
    assert (
        payload["overview"]["troubleshooting"]["catalog_tool_count"]
        == scenario.raw["expected_total_tools"]
    )
