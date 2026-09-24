"""Unit tests for empty ~/.config/cyt home cache bootstrap and inject preview."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from cyt.config import cache_skills_dir
from cyt.hook.catalog_registry import catalog_for_hook, clear_catalog_registry
from cyt.tools.inject_cli import run_inject_preview
from cyt.tools.master_catalog import get_master_tool_catalog
from tests.support.cyt_mcp_catalog_resilience_fixtures import write_registry_disk_snapshot
from tests.support.empty_home_cache_bootstrap_fixtures import (
    EmptyHomeFixturePack,
    assert_empty_cyt_cache,
    disk_cache_hit,
    isolated_empty_home_pack,
    registry_has_workspace_registration,
    scoped_hook_config,
    seed_fixture_skill,
    simulate_cyt_mcp_push,
    simulate_daemon_warm,
    simulate_proxy_registry_load,
    skills_enabled_hook_config,
)


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
) -> None:
    pack = isolated_empty_home_pack
    simulate_daemon_warm(pack)

    catalog = get_master_tool_catalog(scoped_hook_config(pack), blocking=True) or []
    assert catalog == []


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
