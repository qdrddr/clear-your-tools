"""Tests for tier v1 -> v2 SQLite migration."""

from __future__ import annotations

from pathlib import Path

from cyt.tiers.models import Tier, TierProject
from cyt.tiers.store import TierStore


def test_migrate_v1_scope_key_to_project_id(tmp_path: Path) -> None:
    db_path = tmp_path / "tier_state.db"
    legacy = __import__("sqlite3").connect(db_path)
    legacy.executescript(
        """
        CREATE TABLE epoch_state (
            scope_key TEXT PRIMARY KEY,
            epoch_id INTEGER NOT NULL,
            epoch_start_ms INTEGER NOT NULL,
            last_request_ms INTEGER NOT NULL,
            session_id INTEGER NOT NULL
        );
        CREATE TABLE entity_tier (
            scope_key TEXT NOT NULL,
            kind TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            stable_tier INTEGER NOT NULL,
            effective_tier INTEGER NOT NULL,
            overlap_tier INTEGER,
            tier_since_epoch INTEGER NOT NULL DEFAULT 0,
            temp_promotion_until_ms INTEGER,
            wake_lease_until_session INTEGER NOT NULL DEFAULT 0,
            sleep_cooldown_until_session INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (scope_key, kind, entity_id)
        );
        CREATE TABLE entity_stats (
            scope_key TEXT NOT NULL,
            kind TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            pipeline TEXT NOT NULL DEFAULT 'default',
            candidates REAL NOT NULL DEFAULT 0,
            injected REAL NOT NULL DEFAULT 0,
            used REAL NOT NULL DEFAULT 0,
            used_without_injection REAL NOT NULL DEFAULT 0,
            optional_used REAL NOT NULL DEFAULT 0,
            shadow_hits REAL NOT NULL DEFAULT 0,
            shadow_evaluations REAL NOT NULL DEFAULT 0,
            last_seen_ms INTEGER NOT NULL DEFAULT 0,
            requests_since_decay INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (scope_key, kind, entity_id, pipeline)
        );
        CREATE TABLE epoch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_key TEXT NOT NULL,
            epoch_id INTEGER NOT NULL,
            ts_ms INTEGER NOT NULL,
            transitions_json TEXT NOT NULL
        );
        """,
    )
    workspace = str(tmp_path / "project")
    Path(workspace).mkdir()
    scope_key = f"user::{workspace}"
    legacy.execute(
        "INSERT INTO entity_tier VALUES (?, 'tool', 'cyt_mcp:search', 3, 3, NULL, 0, NULL, 0, 0)",
        (scope_key,),
    )
    legacy.execute(
        "INSERT INTO entity_stats VALUES (?, 'tool', 'cyt_mcp:search', 'default', 1, 2, 3, 0, 0, 0, 0, 0, 0)",
        (scope_key,),
    )
    legacy.commit()
    legacy.close()

    store = TierStore.open(str(db_path))
    try:
        version = int(store._conn.execute("PRAGMA user_version").fetchone()[0])
        assert version == 2
        project_id = store.get_or_create_project(workspace)
        project = TierProject(project_id=project_id, root_path=Path(workspace).resolve())
        loaded = store.load_entity_states(project)
        assert loaded[("tool", "cyt_mcp:search")].stable_tier == Tier.HOT
        assert loaded[("tool", "cyt_mcp:search")].stats.used == 3.0
    finally:
        store.close()
