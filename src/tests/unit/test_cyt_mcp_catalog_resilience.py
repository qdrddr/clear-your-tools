"""Unit tests for cyt-mcp catalog resilience (reload, registry hydrate, push)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.cyt_mcp.catalog import clear_cyt_mcp_catalog_cache
from cyt.hook.catalog_registry import (
    catalog_for_hook,
    clear_catalog_registry,
    hydrate_catalog_registry_for_read,
)
from cyt.skills.cli import run_hook_payload
from cyt.tiers.cli import main as tiers_main
from cyt.tiers.manager import _managers
from cyt.tools.master_catalog import clear_master_catalog_cache, get_master_tool_catalog
from tests.support.cyt_mcp_catalog_resilience_fixtures import (
    capture_registry_registrations,
    cyt_mcp_hook_config,
    load_resilience_scenario,
    load_usr_tools_catalog,
    load_ws_tools_catalog,
    materialize_workspace,
    patch_daemon_catalog_status,
    register_dual_layer_catalog,
    register_ws_catalog,
    reset_catalog_state,
    write_registry_disk_snapshot,
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


def test_hydrate_catalog_registry_restores_live_daemon_registrations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_resilience_scenario("daemon_restart_registry_hydrate")
    workspace = materialize_workspace(tmp_path)
    tools = load_ws_tools_catalog()
    register_ws_catalog(workspace, tools)
    captured = capture_registry_registrations()
    clear_catalog_registry(purge_disk_snapshot=False)
    assert catalog_for_hook("cursor", workspace) == []

    patch_daemon_catalog_status(monkeypatch, captured)
    loaded = hydrate_catalog_registry_for_read()
    assert loaded >= len(captured)

    merged = catalog_for_hook("cursor", workspace, allow_stale=False)
    names = {tool["name"] for tool in merged}
    assert names == set(scenario.raw["expected_tool_names"])


def test_hydrate_catalog_registry_live_overlay_replaces_stale_disk_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = materialize_workspace(tmp_path)
    stale_tools = [{"name": "stale_only_tool", "input_schema": {}}]
    live_tools = load_ws_tools_catalog()
    snapshot_dir = tmp_path / "catalog-registry"
    snapshot_file = snapshot_dir / "registrations.json"
    write_registry_disk_snapshot(snapshot_file, workspace, stale_tools)
    monkeypatch.setattr("cyt.hook.catalog_registry.REGISTRY_SNAPSHOT_DIR", snapshot_dir)
    monkeypatch.setattr("cyt.hook.catalog_registry.REGISTRY_SNAPSHOT_FILE", snapshot_file)

    live_registration = {
        "agent": "cursor",
        "scope": "workspace",
        "workspace_root": str(workspace),
        "catalog_layer": "ws",
        "tools": live_tools,
        "content_hash": "live",
        "instance_id": "pid:live",
    }
    patch_daemon_catalog_status(monkeypatch, [live_registration])
    hydrate_catalog_registry_for_read()

    merged = catalog_for_hook("cursor", workspace, allow_stale=False)
    names = {tool["name"] for tool in merged}
    assert "stale_only_tool" not in names
    assert "gitnexus_cypher" in names


def test_fetch_catalog_from_registry_uses_hydrated_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt.cyt_mcp.catalog import _fetch_catalog_from_registry

    workspace = materialize_workspace(tmp_path)
    config = cyt_mcp_hook_config(workspace)
    tools = load_ws_tools_catalog()
    register_ws_catalog(workspace, tools)
    captured = capture_registry_registrations()
    clear_catalog_registry(purge_disk_snapshot=False)
    patch_daemon_catalog_status(monkeypatch, captured)

    fetched = _fetch_catalog_from_registry(config, allow_stale=True)
    assert {tool["name"] for tool in fetched} == {tool["name"] for tool in tools}


def test_master_catalog_blocking_read_after_registry_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_resilience_scenario("daemon_restart_registry_hydrate")
    workspace = materialize_workspace(tmp_path)
    config = cyt_mcp_hook_config(workspace)
    tools = load_ws_tools_catalog()
    register_ws_catalog(workspace, tools)
    captured = capture_registry_registrations()
    clear_catalog_registry(purge_disk_snapshot=False)
    clear_cyt_mcp_catalog_cache()
    clear_master_catalog_cache()
    patch_daemon_catalog_status(monkeypatch, captured)

    catalog = get_master_tool_catalog(config, blocking=True)
    assert catalog is not None
    assert len(catalog) >= int(scenario.raw["minimum_master_catalog_tools"])


def test_push_once_deferred_empty_catalog_not_already_covered_in_hook_daemon_push(
    tmp_path: Path,
) -> None:
    """Document regression: empty catalogs must not spam hook daemon register."""
    from cyt_mcp.config import sample_aggregator_config
    from cyt_mcp.hook_daemon_push import _push_once
    from cyt_mcp.runtime_cache import RuntimeToolCache

    cache = RuntimeToolCache()
    config = sample_aggregator_config(
        catalog_scope="workspace",
        workspace_root=tmp_path,
    )
    with (
        patch(
            "cyt_mcp.hook_daemon_push.resolve_hook_register_url",
            return_value="http://127.0.0.1:8836/hook/catalog/register",
        ),
        patch("cyt_mcp.hook_daemon_push._post_json") as fake_post,
    ):
        ok, _rev = _push_once(config, cache)
        assert ok is False
        fake_post.assert_not_called()


def test_tiers_stats_reports_registered_workspace_catalog_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenario = load_resilience_scenario("daemon_restart_registry_hydrate")
    workspace = materialize_workspace(tmp_path)
    config = cyt_mcp_hook_config(workspace, db_path=tmp_path / "tiers.db")
    register_ws_catalog(workspace, load_ws_tools_catalog())
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())

    monkeypatch.chdir(workspace)
    monkeypatch.setattr("cyt.config.load_config", lambda: config)
    monkeypatch.setattr("cyt.tiers.cli.load_config", lambda: config)
    code = tiers_main(["stats", "--workspace", str(workspace), "--json"])
    assert code == 0

    payload = json.loads(capsys.readouterr().out)
    minimum = int(scenario.raw["minimum_master_catalog_tools"])
    catalog_count = payload["overview"]["troubleshooting"]["catalog_tool_count"]
    tier_total = payload["overview"]["tier_statistics"]["tools"]["totals"]["count"]
    assert catalog_count >= minimum
    assert tier_total >= minimum


def test_hook_inject_includes_cyt_mcp_tools_when_registry_populated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_resilience_scenario("hook_inject_bm25_prompt")
    workspace = materialize_workspace(tmp_path)
    config = cyt_mcp_hook_config(workspace, db_path=tmp_path / "tiers.db")
    register_ws_catalog(workspace, load_ws_tools_catalog())
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())

    result = run_hook_payload(
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": scenario.raw["prompt"],
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


def test_catalog_for_hook_unions_usr_and_ws_layers(tmp_path: Path) -> None:
    workspace = materialize_workspace(tmp_path)
    ws_tools, usr_tools = register_dual_layer_catalog(workspace)

    merged = catalog_for_hook("cursor", workspace, allow_stale=False)
    names = {tool["name"] for tool in merged}
    assert len(merged) == len(ws_tools) + len(usr_tools)
    assert names == {tool["name"] for tool in ws_tools} | {tool["name"] for tool in usr_tools}


def test_catalog_for_hook_ws_only_excludes_user_tools(tmp_path: Path) -> None:
    workspace = materialize_workspace(tmp_path)
    ws_tools = load_ws_tools_catalog()
    register_ws_catalog(workspace, ws_tools)

    merged = catalog_for_hook("cursor", workspace, allow_stale=False)
    assert len(merged) == len(ws_tools)
    assert {tool["name"] for tool in merged} == {tool["name"] for tool in ws_tools}


def test_merge_missing_user_scope_catalog_tools_from_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt.cyt_mcp.catalog import (
        _CytMcpCacheKey,
        _fetch_catalog_from_registry,
        _global_scope_paths,
        _write_catalog_disk,
    )
    from cyt.cyt_mcp.catalog_disk import scope_config_fingerprint

    workspace = materialize_workspace(tmp_path)
    config = cyt_mcp_hook_config(workspace)
    ws_tools = load_ws_tools_catalog()
    usr_tools = load_usr_tools_catalog()
    register_ws_catalog(workspace, ws_tools)

    cache_dir = tmp_path / "cyt-mcp-catalog"
    cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("cyt.cyt_mcp.catalog_disk.cyt_mcp_catalog_cache_dir", lambda: cache_dir)

    global_agg, global_defs = _global_scope_paths("cursor")
    global_fp = scope_config_fingerprint(global_agg, global_defs)
    usr_key = _CytMcpCacheKey(agent="cursor", slug=global_fp, workspace="")
    _write_catalog_disk(usr_key, usr_tools)

    fetched = _fetch_catalog_from_registry(config, allow_stale=True)
    names = {tool["name"] for tool in fetched}
    assert len(fetched) == len(ws_tools) + len(usr_tools)
    assert {tool["name"] for tool in usr_tools}.issubset(names)


def test_tiers_stats_reports_user_and_workspace_catalog_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = materialize_workspace(tmp_path)
    config = cyt_mcp_hook_config(workspace, db_path=tmp_path / "tiers.db")
    ws_tools, usr_tools = register_dual_layer_catalog(workspace)
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())

    monkeypatch.chdir(workspace)
    monkeypatch.setattr("cyt.config.load_config", lambda: config)
    monkeypatch.setattr("cyt.tiers.cli.load_config", lambda: config)
    code = tiers_main(["stats", "--workspace", str(workspace), "--json"])
    assert code == 0

    payload = json.loads(capsys.readouterr().out)
    troubleshooting = payload["overview"]["troubleshooting"]
    assert payload.get("scope") == "all"
    assert troubleshooting["catalog_user_tool_count"] == len(usr_tools)
    assert troubleshooting["catalog_workspace_tool_count"] == len(ws_tools)
    assert troubleshooting["catalog_tool_count"] == len(ws_tools) + len(usr_tools)


def test_disk_snapshot_hydrate_restores_ws_tools_after_registry_clear(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = materialize_workspace(tmp_path)
    tools = load_ws_tools_catalog()
    snapshot_dir = tmp_path / "catalog-registry"
    snapshot_file = snapshot_dir / "registrations.json"
    write_registry_disk_snapshot(snapshot_file, workspace, tools)
    monkeypatch.setattr("cyt.hook.catalog_registry.REGISTRY_SNAPSHOT_DIR", snapshot_dir)
    monkeypatch.setattr("cyt.hook.catalog_registry.REGISTRY_SNAPSHOT_FILE", snapshot_file)

    from cyt.hook.catalog_registry import load_catalog_registry_from_disk

    loaded = load_catalog_registry_from_disk(mark_stale=True)
    assert loaded == 1
    merged = catalog_for_hook("cursor", workspace, allow_stale=True)
    assert {tool["name"] for tool in merged} == {tool["name"] for tool in tools}
