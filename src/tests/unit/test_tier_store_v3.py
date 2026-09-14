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


def test_open_purges_ephemeral_entity_rows_under_real_project(tmp_path: Path) -> None:
    db_path = tmp_path / "tier_state.db"
    ephemeral_id = (
        "/private/var/folders/xx/T/pytest-of-user/test0/.cursor/skills/demo/SKILL.md"
    )
    store = TierStore(str(db_path))
    try:
        project_id = store.get_or_create_project(str(tmp_path))
        store._conn.execute(
            "INSERT INTO entity_tier(project_id, kind, entity_id, stable_tier, effective_tier, "
            "tier_since_epoch, wake_lease_until_cycle, sleep_cooldown_until_cycle) "
            "VALUES (?, 'skill', ?, 2, 2, 1, 0, 0)",
            (project_id, ephemeral_id),
        )
        store._conn.commit()
    finally:
        store.close()

    reopened = TierStore.open(str(db_path))
    try:
        project = TierProject(project_id=project_id, root_path=tmp_path.resolve())
        assert reopened.load_entity_states(project) == {}
    finally:
        reopened.close()


def test_upsert_entity_state_skips_ephemeral_entity_id(tmp_path: Path) -> None:
    db_path = tmp_path / "tier_state.db"
    ephemeral_id = (
        "/private/var/folders/xx/T/pytest-of-user/test0/.cursor/skills/demo/SKILL.md"
    )
    store = TierStore.open(str(db_path))
    try:
        project_id = store.get_or_create_project(str(tmp_path))
        project = TierProject(project_id=project_id, root_path=tmp_path.resolve())
        store.upsert_entity_state(
            project,
            EntityTierState(
                entity_id=ephemeral_id,
                kind=EntityKind.SKILL,
                stable_tier=Tier.ACTIVE,
                effective_tier=Tier.ACTIVE,
            ),
        )
        assert store.load_entity_states(project) == {}
    finally:
        store.close()
