"""Integration tests for consolidated cache layout and inject preview warm paths."""

from __future__ import annotations

import pytest

from cyt.cache import warm_caches
from cyt.tools.inject_cli import main as inject_main
from tests.support.cache_layout_consolidation_fixtures import (
    CacheLayoutPack,
    assert_flat_cache_root,
    load_cache_layout_scenario,
    scoped_hook_config,
    seed_skill_disk_cache,
    seed_tool_disk_cache,
)
from tests.support.cyt_mcp_catalog_resilience_fixtures import reset_catalog_state
from tests.support.empty_home_cache_bootstrap_fixtures import (
    simulate_cyt_mcp_push,
    simulate_daemon_warm,
)
from tests.support.inject_preview_fixtures import json_payload_from_stdout

pytestmark = pytest.mark.integration


def test_tool_and_skill_disk_caches_use_flat_layout(
    isolated_cache_layout_pack: CacheLayoutPack,
) -> None:
    pack = isolated_cache_layout_pack
    seed_tool_disk_cache(pack)
    entry_dir = seed_skill_disk_cache(pack)

    assert_flat_cache_root(pack.tools_cache_dir)
    assert_flat_cache_root(pack.skills_cache_dir)
    assert entry_dir.parent.resolve() == pack.skills_cache_dir.resolve()


def test_warm_after_push_hydrates_without_legacy_entries_layout(
    isolated_cache_layout_pack: CacheLayoutPack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_cache_layout_scenario("warm_after_push_uses_consolidated_paths")
    pack = isolated_cache_layout_pack
    reset_catalog_state()
    simulate_cyt_mcp_push(pack)
    simulate_daemon_warm(pack)
    config = scoped_hook_config(pack)

    warm_caches(config)
    seed_tool_disk_cache(pack, tools=pack.tools[:2])

    assert scenario.id == "warm_after_push_uses_consolidated_paths"
    assert_flat_cache_root(pack.tools_cache_dir)


def test_inject_preview_cli_after_push_uses_consolidated_cache(
    isolated_cache_layout_pack: CacheLayoutPack,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pack = isolated_cache_layout_pack
    reset_catalog_state()
    simulate_cyt_mcp_push(pack)
    simulate_daemon_warm(pack)
    monkeypatch.chdir(pack.workspace)

    code = inject_main(
        [
            "preview",
            "find BM25 ranking implementation",
            "--workspace",
            str(pack.workspace),
            "--source",
            "cyt_mcp",
            "--json",
        ],
    )
    captured = capsys.readouterr()

    assert code == 0, captured.err
    payload = json_payload_from_stdout(captured.out)
    pruned_names = {tool["name"] for tool in payload["tools"]["cyt_mcp"]}
    assert pruned_names
    assert "No tools in master hook catalog" not in captured.err
