"""Unit tests for empty ~/.config/cyt home cache bootstrap and inject preview."""

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.config import cache_skills_dir
from cyt.cyt_mcp.catalog import (
    DEFAULT_COLD_START_REGISTRY_WAIT_SECONDS,
    STEADY_STATE_REGISTRY_WAIT_SECONDS,
    _registry_wait_seconds,
)
from cyt.hook.catalog_registry import catalog_for_hook, clear_catalog_registry
from cyt.tools.inject_cli import run_inject_preview
from cyt.tools.master_catalog import get_master_tool_catalog
from cyt.tools.registry import load_tool_catalog
from tests.support.cyt_mcp_catalog_resilience_fixtures import (
    register_ws_catalog,
    write_registry_disk_snapshot,
)
from tests.support.empty_home_cache_bootstrap_fixtures import (
    EmptyHomeFixturePack,
    assert_empty_cyt_cache,
    clear_in_memory_hook_catalog_caches,
    disk_cache_hit,
    registry_has_workspace_registration,
    scoped_hook_config,
    seed_fixture_skill,
    simulate_cyt_mcp_push,
    simulate_daemon_warm,
    simulate_proxy_registry_load,
    skills_enabled_hook_config,
)


def _patch_fast_registry_wait(
    monkeypatch: pytest.MonkeyPatch,
    pack: EmptyHomeFixturePack,
) -> dict:
    config = scoped_hook_config(pack)
    hook = config.setdefault("pruning", {}).setdefault("tools", {}).setdefault("hook", {})
    cyt_mcp = hook.setdefault("cyt_mcp", {})
    cache = cyt_mcp.setdefault("cache", {})
    cache["registry_wait_seconds"] = 0.05
    monkeypatch.setattr("cyt.config.load_config", lambda *args, **kwargs: config)
    return config


def _preview_args(pack: EmptyHomeFixturePack) -> argparse.Namespace:
    return argparse.Namespace(
        query=pack.bm25_prompt,
        workspace=pack.workspace,
        json=False,
        definitions=False,
        source=["cyt_mcp"],
        session=None,
    )


def test_empty_home_inject_preview_fails_disk_cache_miss(
    isolated_empty_home_pack: EmptyHomeFixturePack,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pack = isolated_empty_home_pack
    code = run_inject_preview(_preview_args(pack))
    err = capsys.readouterr().err

    assert code == 1
    assert "No tools in master hook catalog" in err
    assert "disk_cache=miss" in err


def test_cyt_mcp_push_writes_disk_and_registry(
    isolated_empty_home_pack: EmptyHomeFixturePack,
) -> None:
    pack = isolated_empty_home_pack
    simulate_cyt_mcp_push(pack)

    assert disk_cache_hit(pack)
    assert registry_has_workspace_registration(pack)


def test_warm_caches_hydrates_master_after_push(
    isolated_empty_home_pack: EmptyHomeFixturePack,
) -> None:
    pack = isolated_empty_home_pack
    simulate_cyt_mcp_push(pack)
    simulate_daemon_warm(pack)

    catalog = get_master_tool_catalog(scoped_hook_config(pack), blocking=True) or []
    names = {tool["name"] for tool in catalog}

    assert len(catalog) >= pack.minimum_master_catalog_tools
    assert set(pack.expected_tool_names).issubset(names)


def test_inject_preview_succeeds_after_push_and_warm(
    isolated_empty_home_pack: EmptyHomeFixturePack,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pack = isolated_empty_home_pack
    simulate_cyt_mcp_push(pack)
    simulate_daemon_warm(pack)

    code = run_inject_preview(_preview_args(pack))
    captured = capsys.readouterr()

    assert code == 0, captured.err
    assert "No tools in master hook catalog" not in captured.err
    for marker in pack.expected_injection_markers:
        assert marker in captured.out
    injected_names = {
        name
        for name in pack.expected_tool_names
        if f"name='{name}'" in captured.out or name in captured.out
    }
    assert injected_names, "expected at least one BM25-relevant tool in injection output"
    assert injected_names.issubset(set(pack.expected_tool_names))


def test_warm_caches_alone_does_not_invent_tools(
    isolated_empty_home_pack: EmptyHomeFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = isolated_empty_home_pack
    _patch_fast_registry_wait(monkeypatch, pack)
    simulate_daemon_warm(pack)

    catalog = get_master_tool_catalog(scoped_hook_config(pack), blocking=True) or []
    assert catalog == []


def test_register_catalog_eager_hydrates_master_without_warm(
    isolated_empty_home_pack: EmptyHomeFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = isolated_empty_home_pack
    config = scoped_hook_config(pack)
    monkeypatch.setattr("cyt.config.load_config", lambda *args, **kwargs: config)
    clear_in_memory_hook_catalog_caches()

    register_ws_catalog(pack.workspace, pack.tools)

    catalog = get_master_tool_catalog(config, blocking=False) or []
    names = {tool["name"] for tool in catalog}
    assert len(catalog) >= pack.minimum_master_catalog_tools
    assert set(pack.expected_tool_names).issubset(names)
    assert disk_cache_hit(pack)


def test_load_tool_catalog_blocks_until_delayed_register(
    isolated_empty_home_pack: EmptyHomeFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = isolated_empty_home_pack
    config = scoped_hook_config(pack)
    hook = config.setdefault("pruning", {}).setdefault("tools", {}).setdefault("hook", {})
    hook.setdefault("cyt_mcp", {}).setdefault("cache", {})["registry_wait_seconds"] = 2.0
    monkeypatch.setattr("cyt.config.load_config", lambda *args, **kwargs: config)
    clear_in_memory_hook_catalog_caches()

    result_holder: list[list[dict] | None] = []

    def _load() -> None:
        result_holder.append(load_tool_catalog(config))

    thread = threading.Thread(target=_load, name="cold-start-load")
    thread.start()
    time.sleep(0.25)
    register_ws_catalog(pack.workspace, pack.tools)
    thread.join(timeout=5.0)

    assert not thread.is_alive()
    catalog = result_holder[0] or []
    names = {tool["name"] for tool in catalog}
    assert set(pack.expected_tool_names).issubset(names)


def test_load_tool_catalog_uses_blocking_when_master_empty() -> None:
    config = {
        "pruning": {
            "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
            "tools": {
                "enabled": True,
                "hook": {"tools_from": ["cyt_mcp"], "cyt_mcp": {"agent": "cursor"}},
            },
        },
    }
    with (
        patch("cyt.tools.registry.tools_hook_file_missing", return_value=False),
        patch("cyt.tools.registry.get_master_tool_catalog") as get_master,
    ):
        get_master.side_effect = [[], [{"name": "tool-a", "cyt_catalog_source": "cyt_mcp"}]]
        catalog = load_tool_catalog(config)
        assert catalog == [{"name": "tool-a", "cyt_catalog_source": "cyt_mcp"}]
        assert get_master.call_count == 2
        assert get_master.call_args_list[0].kwargs["blocking"] is False
        assert get_master.call_args_list[1].kwargs["blocking"] is True
        assert get_master.call_args_list[1].kwargs["cold_start"] is True


def test_registry_wait_seconds_cold_start_vs_steady_state() -> None:
    cfg: dict = {
        "pruning": {
            "tools": {
                "hook": {
                    "cyt_mcp": {
                        "cache": {
                            "registry_wait_seconds": DEFAULT_COLD_START_REGISTRY_WAIT_SECONDS,
                        },
                    },
                },
            },
        },
    }
    assert _registry_wait_seconds(cfg, cold_start=True) == DEFAULT_COLD_START_REGISTRY_WAIT_SECONDS
    assert _registry_wait_seconds(cfg, cold_start=False) == STEADY_STATE_REGISTRY_WAIT_SECONDS
    cfg["pruning"]["tools"]["hook"]["cyt_mcp"]["cache"]["registry_wait_seconds"] = 12.0
    assert _registry_wait_seconds(cfg, cold_start=True) == 12.0


def test_warm_caches_builds_skills_registry_on_disk(
    isolated_empty_home_pack: EmptyHomeFixturePack,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt.cache import warm_caches
    from cyt.skills.catalog import clear_registry_cache

    pack = isolated_empty_home_pack
    seed_fixture_skill(pack)
    skills_cache_dir = tmp_path / "skills-entries"
    skills_cache_dir.mkdir()
    config = skills_enabled_hook_config(pack, skills_cache_dir=skills_cache_dir)
    monkeypatch.setattr("cyt.config.load_config", lambda *args, **kwargs: config)

    clear_registry_cache()
    warm_caches(config)

    entries_root = cache_skills_dir(config)
    assert entries_root == skills_cache_dir
    assert any(entries_root.iterdir()), "expected skills cache entries after warm_caches"


def test_proxy_lifespan_loads_registry_snapshot(
    isolated_empty_home_pack: EmptyHomeFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = isolated_empty_home_pack
    clear_catalog_registry(purge_disk_snapshot=False)
    write_registry_disk_snapshot(
        pack.registry_snapshot_file,
        pack.workspace,
        pack.tools,
    )

    loaded = simulate_proxy_registry_load()
    assert loaded == 1

    merged = catalog_for_hook("cursor", pack.workspace, allow_stale=True)
    names = {tool["name"] for tool in merged}
    assert set(pack.expected_tool_names).issubset(names)


def test_assert_empty_cyt_cache_rejects_seeded_disk(
    isolated_empty_home_pack: EmptyHomeFixturePack,
) -> None:
    pack = isolated_empty_home_pack
    simulate_cyt_mcp_push(pack)

    with pytest.raises(AssertionError, match="expected empty cyt-mcp catalog cache dir"):
        assert_empty_cyt_cache(pack)
