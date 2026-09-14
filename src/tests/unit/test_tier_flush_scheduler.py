"""Tests for deferred tier statistics disk persistence."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from cyt.tiers.config import tier_disk_flush_seconds
from cyt.tiers.flush_scheduler import (
    reset_tier_flush_scheduler_for_tests,
    start_tier_flush_scheduler,
    stop_tier_flush_scheduler,
)
from cyt.tiers.manager import TierManager, _managers, flush_all_tier_managers, get_tier_manager


@pytest.fixture(autouse=True)
def _isolated_tier_flush_scheduler() -> Iterator[None]:
    reset_tier_flush_scheduler_for_tests()
    _managers.clear()
    yield
    stop_tier_flush_scheduler()
    reset_tier_flush_scheduler_for_tests()
    _managers.clear()


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


def _tier_config(db_path: Path, *, disk_flush_seconds: float) -> dict:
    return {
        "tools": {
            "tiers": {
                "mode": "shadow",
                "database": {
                    "path": str(db_path),
                    "disk_flush_seconds": disk_flush_seconds,
                },
            },
        },
    }


def test_tier_disk_flush_seconds_defaults_to_900() -> None:
    from cyt.config import load_config

    assert tier_disk_flush_seconds(load_config()) == 900.0


def test_deferred_flush_does_not_write_until_flush(project_root: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "tier_state.db"
    config = _tier_config(db_path, disk_flush_seconds=900)
    tool = {"name": "search", "cyt_catalog_source": "cyt_mcp"}

    manager = TierManager(project_root, str(db_path))
    try:
        # Failed attempts do not create temp promotions, so deferred flush applies.
        manager.record_tool_attempt(tool, config=config, success=False)
        assert manager._pending_flush is True
        reloaded = TierManager(project_root, str(db_path))
        try:
            assert reloaded._states.get(("tool", "cyt_mcp:search")) is None
        finally:
            reloaded.close()
        assert manager.flush_pending() is True
        persisted = TierManager(project_root, str(db_path))
        try:
            state = persisted._states.get(("tool", "cyt_mcp:search"))
            assert state is not None
            assert state.stats.attempts >= 1.0
            assert state.stats.used == 0.0
        finally:
            persisted.close()
    finally:
        manager.close()


def test_temp_promotion_flushes_immediately_with_deferred_disk(
    project_root: Path,
    tmp_path: Path,
) -> None:
    from cyt.tiers.models import Tier

    db_path = tmp_path / "tier_state.db"
    config = _tier_config(db_path, disk_flush_seconds=900)
    tool = {"name": "gitnexus_query", "cyt_catalog_source": "cyt_mcp"}

    manager = TierManager(project_root, str(db_path))
    try:
        manager.record_tool_attempt(tool, config=config, success=True)
        assert manager._pending_flush is False
        reloaded = TierManager(project_root, str(db_path))
        try:
            state = reloaded._states.get(("tool", "cyt_mcp:gitnexus_query"))
            assert state is not None
            assert state.stats.used >= 1.0
            assert state.effective_tier == Tier.HOT
            assert state.temp_promotion_until_ms is not None
        finally:
            reloaded.close()
    finally:
        manager.close()


def test_sync_mode_flushes_immediately(project_root: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "tier_state.db"
    config = _tier_config(db_path, disk_flush_seconds=0)
    tool = {"name": "search", "cyt_catalog_source": "cyt_mcp"}

    manager = TierManager(project_root, str(db_path))
    try:
        manager.record_tool_attempt(tool, config=config, success=True)
        assert manager._pending_flush is False
        reloaded = TierManager(project_root, str(db_path))
        try:
            state = reloaded._states.get(("tool", "cyt_mcp:search"))
            assert state is not None
            assert state.stats.used >= 1.0
        finally:
            reloaded.close()
    finally:
        manager.close()


def test_scheduler_flushes_pending_stats(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "tier_state.db"
    config = _tier_config(db_path, disk_flush_seconds=0.05)
    tool = {"name": "search", "cyt_catalog_source": "cyt_mcp"}

    monkeypatch.setattr("cyt.tiers.flush_scheduler.tier_disk_flush_seconds", lambda _cfg: 0.05)

    manager = get_tier_manager(config, workspace=project_root)
    start_tier_flush_scheduler(config)
    manager.record_tool_attempt(tool, config=config, success=True)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        reloaded = TierManager(project_root, str(db_path))
        try:
            state = reloaded._states.get(("tool", "cyt_mcp:search"))
            if state is not None and state.stats.used >= 1.0:
                break
        finally:
            reloaded.close()
        time.sleep(0.05)
    else:
        flushed = flush_all_tier_managers(force=False)
        assert flushed == 1
        reloaded = TierManager(project_root, str(db_path))
        try:
            state = reloaded._states.get(("tool", "cyt_mcp:search"))
            assert state is not None
            assert state.stats.used >= 1.0
        finally:
            reloaded.close()


def test_close_force_flushes_pending(project_root: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "tier_state.db"
    config = _tier_config(db_path, disk_flush_seconds=900)
    tool = {"name": "search", "cyt_catalog_source": "cyt_mcp"}

    manager = TierManager(project_root, str(db_path))
    manager.record_tool_attempt(tool, config=config, success=True)
    manager.close()

    reloaded = TierManager(project_root, str(db_path))
    try:
        state = reloaded._states.get(("tool", "cyt_mcp:search"))
        assert state is not None
        assert state.stats.used >= 1.0
    finally:
        reloaded.close()
