"""Unit tests for cache layout consolidation and related latency/stability fixes."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.cache.cli import run_cache_clear
from cyt.hook import catalog_registry as registry_mod
from cyt.hook.catalog_registry import (
    clear_catalog_registry,
    load_catalog_registry_from_disk,
    prune_expired_registrations,
)
from cyt.tiers.manager import TierManager
from cyt.tools.inject_cli import _preview_coordinated_prune, run_inject_preview
from cyt_core.types.prune import PruneResult
from tests.support.cache_layout_consolidation_fixtures import (
    CacheLayoutPack,
    bundled_defaults_match_consolidated_layout,
    phase_timing_marker,
    seed_skill_disk_cache,
    seed_tool_disk_cache,
)
from tests.support.inject_preview_fixtures import load_inject_preview_scenario


def test_bundled_defaults_use_consolidated_cache_paths() -> None:
    bundled_defaults_match_consolidated_layout()


def test_prune_expired_registrations_does_not_schedule_snapshot_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_catalog_registry(purge_disk_snapshot=False)
    scheduled: list[bool] = []

    monkeypatch.setattr(
        registry_mod,
        "_schedule_snapshot_write",
        lambda: scheduled.append(True),
    )

    key = ("cursor", "workspace", "", "ws")
    with registry_mod._registry_lock:
        registry_mod._registrations[key] = registry_mod._CatalogRegistration(
            agent="cursor",
            scope="workspace",
            workspace_root="",
            catalog_layer="ws",
            last_seen_at=time.monotonic() - registry_mod.REGISTRY_TTL_SECONDS - 1,
        )

    removed = prune_expired_registrations()
    assert removed == 1
    assert scheduled == []


def test_begin_request_cycle_marks_dirty_without_sync_epoch_save(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    manager = TierManager(tmp_path, str(tmp_path / "tier_state.db"))
    config = {
        "tools": {"tiers": {"mode": "shadow"}},
        "skills": {"tiers": {"mode": "shadow"}},
    }

    with patch.object(manager._store, "save_epoch_state") as save_epoch:
        manager.begin_request_cycle(config)

    save_epoch.assert_not_called()
    assert manager._pending_flush is True


def test_preview_coordinated_prune_uses_hook_bridge(
    disk_catalog_cache_layout_pack: CacheLayoutPack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = disk_catalog_cache_layout_pack
    calls: list[str] = []

    def fake_coordinated(
        query: str,
        config: dict,
        *,
        payload: dict | None = None,
        skills_allowed: bool = True,
        tools_allowed: bool = True,
        **kwargs: object,
    ) -> tuple:
        calls.append(query)
        result = PruneResult(
            tools=[{"name": "demo_tool", "input_schema": {}}],
            status="ok",
            query=query,
            tools_in=1,
            mcp_tools_in=1,
            tools_out=1,
            error=None,
        )
        return None, None, [], {"cyt_mcp": result}, {"total_ms": 12, "phases": []}

    monkeypatch.setattr(
        "cyt.pruning.hook_bridge.run_hook_coordinated_prune",
        fake_coordinated,
    )

    pruned, _meta, _typed, timing = _preview_coordinated_prune(
        query="find BM25 ranking",
        config={"pruning": {"tools": {"enabled": True}}},
        workspace=pack.workspace,
        sources_filter={"cyt_mcp"},
    )

    assert calls == ["find BM25 ranking"]
    assert "cyt_mcp" in pruned
    assert timing["total_ms"] == 12


def test_inject_preview_verbose_emits_phase_timing(
    disk_catalog_cache_layout_pack: CacheLayoutPack,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pack = disk_catalog_cache_layout_pack
    scenario = load_inject_preview_scenario()
    monkeypatch.chdir(pack.workspace)

    def fake_coordinated(
        query: str,
        config: dict,
        *,
        payload: dict | None = None,
        skills_allowed: bool = True,
        tools_allowed: bool = True,
        **kwargs: object,
    ) -> tuple:
        result = PruneResult(
            tools=pack.tools[:1],
            status="ok",
            query=query,
            tools_in=len(pack.tools),
            mcp_tools_in=len(pack.tools),
            tools_out=1,
            error=None,
        )
        return (
            None,
            None,
            [],
            {"cyt_mcp": result},
            {
                "total_ms": 99,
                "phases": [{"name": "preview-prune", "elapsed_ms": 99}],
            },
        )

    monkeypatch.setattr(
        "cyt.pruning.hook_bridge.run_hook_coordinated_prune",
        fake_coordinated,
    )

    args = argparse.Namespace(
        query=scenario.query,
        workspace=pack.workspace,
        json=False,
        definitions=False,
        source=["cyt_mcp"],
        session=None,
        verbose=True,
    )
    code = run_inject_preview(args)
    captured = capsys.readouterr()

    assert code == 0, captured.err
    assert phase_timing_marker() in captured.err


def test_cache_clear_tools_removes_consolidated_tools_dir(
    isolated_cache_layout_pack: CacheLayoutPack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = isolated_cache_layout_pack
    seed_tool_disk_cache(pack)
    assert pack.tools_cache_dir.is_dir()

    code = run_cache_clear(
        argparse.Namespace(tools=True, skills=False, bm25=False, catalog=False, all=False),
    )
    assert code == 0
    assert not pack.tools_cache_dir.exists()


def test_legacy_registry_snapshot_compacted_on_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws_root = tmp_path / "project"
    ws_root.mkdir()
    snapshot_dir = tmp_path / "catalog-registry"
    snapshot_dir.mkdir()
    snapshot_file = snapshot_dir / "registrations.json"
    snapshot_file.write_text(
        json.dumps(
            [
                {
                    "agent": "cursor",
                    "scope": "global",
                    "workspace_root": None,
                    "tools": [{"name": "legacy_tool", "input_schema": {}}],
                    "content_hash": "deadbeef",
                    "instance_id": "pid:old",
                    "registered_at": 1.0,
                    "last_seen_at": 1.0,
                    "stale": False,
                },
            ],
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_mod, "REGISTRY_SNAPSHOT_DIR", snapshot_dir)
    monkeypatch.setattr(registry_mod, "REGISTRY_SNAPSHOT_FILE", snapshot_file)

    assert load_catalog_registry_from_disk(mark_stale=True) == 0
    assert json.loads(snapshot_file.read_text(encoding="utf-8")) == []


def test_skill_entry_dir_uses_flat_cache_skills_root(
    isolated_cache_layout_pack: CacheLayoutPack,
) -> None:
    entry_dir = seed_skill_disk_cache(isolated_cache_layout_pack)
    assert entry_dir.parent.resolve() == isolated_cache_layout_pack.skills_cache_dir.resolve()


def test_tool_disk_cache_uses_flat_content_hash_dir(
    isolated_cache_layout_pack: CacheLayoutPack,
) -> None:
    root = seed_tool_disk_cache(isolated_cache_layout_pack)
    hash_dirs = [child for child in root.iterdir() if child.is_dir()]
    assert hash_dirs
    assert not (root / "entries").exists()
