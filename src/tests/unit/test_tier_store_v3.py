"""Tests for tier store schema v3 attempts column."""

from __future__ import annotations

from pathlib import Path

from cyt.tiers.models import EffectiveStats, EntityKind, EntityTierState, Tier, TierProject
from cyt.tiers.store import _SCHEMA_VERSION, TierStore


def test_fresh_store_has_attempts_column(tmp_path: Path) -> None:
    db_path = tmp_path / "tier_state.db"
    store = TierStore.open(str(db_path))
    try:
        version = int(store._conn.execute("PRAGMA user_version").fetchone()[0])
        assert version == _SCHEMA_VERSION
        assert store._table_has_column("entity_stats", "attempts")
    finally:
        store.close()


def test_v2_store_migrates_attempts_column(tmp_path: Path) -> None:
    db_path = tmp_path / "tier_state.db"
    store = TierStore.open(str(db_path))
    try:
        store._conn.execute("PRAGMA user_version = 2")
        store._conn.execute("ALTER TABLE entity_stats DROP COLUMN attempts")
        store._conn.commit()
    finally:
        store.close()

    store = TierStore.open(str(db_path))
    try:
        assert store._table_has_column("entity_stats", "attempts")
        project_id = store.get_or_create_project(str(tmp_path))
        project = TierProject(project_id=project_id, root_path=tmp_path.resolve())
        state = EntityTierState(
            entity_id="cyt_mcp:search",
            kind=EntityKind.TOOL,
            stable_tier=Tier.ACTIVE,
            effective_tier=Tier.ACTIVE,
            stats=EffectiveStats(attempts=2.0, used=1.0),
        )
        store.upsert_entity_state(project, state)
        loaded = store.load_entity_states(project)
        assert loaded[("tool", "cyt_mcp:search")].stats.attempts == 2.0
    finally:
        store.close()
