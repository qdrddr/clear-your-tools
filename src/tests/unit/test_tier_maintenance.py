"""Unit tests for tier state database maintenance."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from cyt.tiers.maintenance import (
    _catalog_snapshot_safe_for_stale_purge,
    maintenance_ran_today,
    maybe_run_tier_maintenance_on_stats_query,
    reset_tier_maintenance_scheduler_for_tests,
    run_tier_state_maintenance,
    schedule_tier_state_maintenance_if_due,
)
from cyt.tiers.models import EffectiveStats, EntityKind, Tier
from cyt.tiers.store import TierStore
from tests.support.db_maintenance_fixtures import (
    build_tier_retention_config,
    load_scenarios,
    seed_epoch_log_rows,
    seed_tier_entities,
)


def _open_tier_store(db: Path) -> TierStore:
    return TierStore(str(db))


def test_apply_stat_decay_matches_runtime_math(tmp_path: Path) -> None:
    db = tmp_path / "tier_state.db"
    store = _open_tier_store(db)
    try:
        project_id = store.get_or_create_project(str(tmp_path / "repo"))
        with store._lock:
            store._conn.execute(
                "INSERT INTO entity_stats("
                "project_id, kind, entity_id, pipeline, candidates, injected, used, "
                "attempts, used_without_injection, optional_used, shadow_hits, "
                "shadow_evaluations, last_seen_ms, requests_since_decay"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    project_id,
                    EntityKind.TOOL,
                    "cyt_mcp:Shell",
                    "default",
                    100.0,
                    40.0,
                    10.0,
                    12.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    int(time.time() * 1000),
                    150,
                ),
            )
            store._conn.execute(
                "INSERT INTO entity_tier("
                "project_id, kind, entity_id, stable_tier, effective_tier, tier_since_epoch"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    project_id,
                    EntityKind.TOOL,
                    "cyt_mcp:Shell",
                    int(Tier.ACTIVE),
                    int(Tier.ACTIVE),
                    0,
                ),
            )
            store._conn.commit()

        runtime = EffectiveStats(
            candidates=100.0,
            injected=40.0,
            used=10.0,
            attempts=12.0,
            requests_since_decay=150,
        )
        runtime.decay(half_life=100.0)
        updated = store.apply_stat_decay(request_half_life=100.0)
        assert updated == 1

        row = store._conn.execute(
            "SELECT candidates, injected, used, attempts, requests_since_decay "
            "FROM entity_stats WHERE entity_id = ?",
            ("cyt_mcp:Shell",),
        ).fetchone()
        assert row is not None
        assert row[4] == 0
        assert abs(float(row[0]) - runtime.candidates) < 1e-6
        assert abs(float(row[1]) - runtime.injected) < 1e-6
        assert abs(float(row[2]) - runtime.used) < 1e-6
        assert abs(float(row[3]) - runtime.attempts) < 1e-6
    finally:
        store.close()


def test_prune_epoch_log_by_age_and_count(tmp_path: Path) -> None:
    db = tmp_path / "tier_state.db"
    store = _open_tier_store(db)
    try:
        project_id = store.get_or_create_project(str(tmp_path / "repo"))
        now_ms = int(time.time() * 1000)
        with store._lock:
            for idx in range(5):
                store._conn.execute(
                    "INSERT INTO epoch_log(project_id, epoch_id, ts_ms, transitions_json) "
                    "VALUES (?, ?, ?, ?)",
                    (project_id, idx, now_ms - idx * 86400 * 1000, "[]"),
                )
            store._conn.commit()

        removed = store.prune_epoch_log(
            max_age_ms=now_ms - 2 * 86400 * 1000,
            max_entries=2,
        )
        assert removed >= 3
        remaining = store._conn.execute("SELECT COUNT(*) FROM epoch_log").fetchone()[0]
        assert int(remaining) <= 2
    finally:
        store.close()


def test_prune_dormant_entities(tmp_path: Path) -> None:
    db = tmp_path / "tier_state.db"
    store = _open_tier_store(db)
    try:
        project_id = store.get_or_create_project(str(tmp_path / "repo"))
        stale_ms = int(time.time() * 1000) - 200 * 86400 * 1000
        with store._lock:
            store._conn.execute(
                "INSERT INTO entity_stats("
                "project_id, kind, entity_id, pipeline, candidates, injected, used, "
                "attempts, used_without_injection, optional_used, shadow_hits, "
                "shadow_evaluations, last_seen_ms, requests_since_decay"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    project_id,
                    EntityKind.TOOL,
                    "cyt_mcp:old_tool",
                    "default",
                    0.005,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    stale_ms,
                    0,
                ),
            )
            store._conn.execute(
                "INSERT INTO entity_tier("
                "project_id, kind, entity_id, stable_tier, effective_tier, tier_since_epoch"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    project_id,
                    EntityKind.TOOL,
                    "cyt_mcp:old_tool",
                    int(Tier.COLD),
                    int(Tier.COLD),
                    0,
                ),
            )
            store._conn.commit()

        removed = store.prune_dormant_entities(
            idle_cutoff_ms=int(time.time() * 1000) - 180 * 86400 * 1000,
            counter_floor=0.01,
        )
        assert removed == 1
        count = store._conn.execute("SELECT COUNT(*) FROM entity_stats").fetchone()[0]
        assert int(count) == 0
    finally:
        store.close()


def test_run_tier_state_maintenance_dry_run(tmp_path: Path) -> None:
    db = tmp_path / "tier_state.db"
    config = {
        "tools": {
            "tiers": {
                "mode": "shadow",
                "database": {"path": str(db)},
                "retention": {"enabled": True},
            },
        },
    }
    result = run_tier_state_maintenance(config, dry_run=True)
    assert result.dry_run is True


def test_maybe_run_tier_maintenance_on_stats_query_once_per_day(
    tmp_path: Path,
    db_maintenance_marker: Path,
) -> None:
    db = tmp_path / "tier_state.db"
    workspace = tmp_path / "repo"
    workspace.mkdir()
    config = build_tier_retention_config(
        workspace,
        db,
        retention={"enabled": True, "vacuum_after_maintenance": False},
    )
    assert maintenance_ran_today(config) is False
    first = maybe_run_tier_maintenance_on_stats_query(config)
    assert first is not None
    assert maintenance_ran_today(config) is True
    second = maybe_run_tier_maintenance_on_stats_query(config)
    assert second is None


def test_schedule_tier_state_maintenance_if_due_respects_interval(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    db = tmp_path / "tier_state.db"
    config = build_tier_retention_config(
        workspace,
        db,
        retention={
            "enabled": True,
            "maintenance_interval_seconds": 3600,
            "vacuum_after_maintenance": False,
        },
    )
    reset_tier_maintenance_scheduler_for_tests()
    schedule_tier_state_maintenance_if_due(config)
    schedule_tier_state_maintenance_if_due(config)
    reset_tier_maintenance_scheduler_for_tests()


def test_fixture_driven_epoch_log_scenario(tmp_path: Path) -> None:
    scenario = load_scenarios()["tier_state"]["epoch_log_cap"]
    db = tmp_path / "tier_state.db"
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = _open_tier_store(db)
    try:
        seed_epoch_log_rows(
            store,
            project_root=workspace,
            count=int(scenario["epoch_log_count"]),
        )
    finally:
        store.close()

    config = build_tier_retention_config(workspace, db, retention=scenario["retention"])
    result = run_tier_state_maintenance(config, vacuum=False)
    assert result.deleted.get("epoch_log", 0) >= 1


def test_fixture_driven_decay_scenario(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cyt.tiers.adapters.tools.resolve_tracked_catalog_entity_ids",
        lambda _config, blocking=False: None,
    )
    scenario = load_scenarios()["tier_state"]["decay_and_prune"]
    db = tmp_path / "tier_state.db"
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = _open_tier_store(db)
    try:
        seed_tier_entities(store, project_root=workspace, entities=scenario["entities"])
    finally:
        store.close()

    config = build_tier_retention_config(workspace, db, retention=scenario["retention"])
    run_tier_state_maintenance(config, vacuum=False)

    verify = _open_tier_store(db)
    try:
        row = verify._conn.execute(
            "SELECT candidates, requests_since_decay FROM entity_stats WHERE entity_id = ?",
            (scenario["expected"]["decayed_entity_id"],),
        ).fetchone()
    finally:
        verify.close()
    assert row is not None
    assert int(row[1]) == 0


def test_catalog_snapshot_safe_for_stale_purge_rejects_incomplete_snapshot() -> None:
    assert not _catalog_snapshot_safe_for_stale_purge(
        catalog_entity_ids=frozenset(f"cyt_mcp:tool{i}" for i in range(5)),
        persisted_tool_count=202,
        catalog_rebuild_in_progress=False,
    )


def test_catalog_snapshot_safe_for_stale_purge_allows_legitimate_shrink() -> None:
    assert _catalog_snapshot_safe_for_stale_purge(
        catalog_entity_ids=frozenset(f"cyt_mcp:tool{i}" for i in range(80)),
        persisted_tool_count=202,
        catalog_rebuild_in_progress=False,
    )


def test_run_tier_state_maintenance_skips_purge_on_incomplete_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "tier_state.db"
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = _open_tier_store(db)
    try:
        seed_tier_entities(
            store,
            project_root=workspace,
            entities=[
                {
                    "entity_id": f"cyt_mcp:tool{i}",
                    "kind": "tool",
                    "effective_tier": "ACTIVE",
                    "candidates": 1.0,
                }
                for i in range(10)
            ],
        )
    finally:
        store.close()

    monkeypatch.setattr(
        "cyt.tiers.adapters.tools.resolve_tracked_catalog_entity_ids",
        lambda _config, blocking=False: frozenset({"cyt_mcp:tool0", "cyt_mcp:tool1"}),
    )
    config = build_tier_retention_config(workspace, db)
    result = run_tier_state_maintenance(config, vacuum=False)
    assert result.deleted.get("stale_catalog_tools", 0) == 0
    remaining = {
        row[0]
        for row in TierStore(str(db))._conn.execute(
            "SELECT entity_id FROM entity_stats WHERE kind = ?",
            (EntityKind.TOOL,),
        ).fetchall()
    }
    assert len(remaining) == 10
