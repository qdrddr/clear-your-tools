"""SQLite persistence for tier state."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from cyt.common.paths import is_default_user_cyt_db, is_ephemeral_workspace_path
from cyt.tiers.models import (
    EffectiveStats,
    EntityTierState,
    EpochState,
    Tier,
    TierProject,
    TierTransition,
)

_SCHEMA_VERSION = 5
_BUSY_TIMEOUT_MS = 30_000

_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS tier_project (
    project_id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_path TEXT NOT NULL UNIQUE,
    created_ms INTEGER NOT NULL,
    last_seen_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS epoch_state (
    project_id INTEGER PRIMARY KEY REFERENCES tier_project(project_id),
    epoch_id INTEGER NOT NULL,
    epoch_start_ms INTEGER NOT NULL,
    last_request_ms INTEGER NOT NULL,
    wake_cycle_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_stats (
    project_id INTEGER NOT NULL REFERENCES tier_project(project_id),
    kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    pipeline TEXT NOT NULL DEFAULT 'default',
    candidates REAL NOT NULL DEFAULT 0,
    injected REAL NOT NULL DEFAULT 0,
    used REAL NOT NULL DEFAULT 0,
    attempts REAL NOT NULL DEFAULT 0,
    used_without_injection REAL NOT NULL DEFAULT 0,
    optional_used REAL NOT NULL DEFAULT 0,
    shadow_hits REAL NOT NULL DEFAULT 0,
    shadow_evaluations REAL NOT NULL DEFAULT 0,
    last_seen_ms INTEGER NOT NULL DEFAULT 0,
    requests_since_decay INTEGER NOT NULL DEFAULT 0,
    epoch_used REAL NOT NULL DEFAULT 0,
    epoch_attempts REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, kind, entity_id, pipeline)
);

CREATE TABLE IF NOT EXISTS entity_tier (
    project_id INTEGER NOT NULL REFERENCES tier_project(project_id),
    kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    stable_tier INTEGER NOT NULL,
    effective_tier INTEGER NOT NULL,
    overlap_tier INTEGER,
    tier_since_epoch INTEGER NOT NULL DEFAULT 0,
    temp_promotion_until_ms INTEGER,
    wake_lease_until_cycle INTEGER NOT NULL DEFAULT 0,
    sleep_cooldown_until_cycle INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, kind, entity_id)
);

CREATE TABLE IF NOT EXISTS epoch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES tier_project(project_id),
    epoch_id INTEGER NOT NULL,
    ts_ms INTEGER NOT NULL,
    transitions_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entity_tier_project ON entity_tier(project_id);
CREATE INDEX IF NOT EXISTS idx_entity_stats_project ON entity_stats(project_id, kind, entity_id);
CREATE INDEX IF NOT EXISTS idx_epoch_log_project ON epoch_log(project_id);
"""


def _require_lastrowid(cur: sqlite3.Cursor) -> int:
    lastrowid = cur.lastrowid
    if lastrowid is None:
        raise RuntimeError("SQLite INSERT did not return lastrowid")
    return int(lastrowid)


class TierStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = str(Path(db_path).expanduser())
        self._lock = threading.RLock()
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        # WAL allows concurrent readers (e.g. tiers stats CLI) while the hook daemon writes.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()

    @classmethod
    def open(cls, db_path: str) -> TierStore:
        store = cls(db_path)
        if is_default_user_cyt_db(db_path, "tier_state.db"):
            store.purge_ephemeral_projects()
            store.purge_stale_skill_doc_entities()
        return store

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _table_has_column(self, table: str, column: str) -> bool:
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(str(row[1]) == column for row in rows)

    def _ensure_schema(self) -> None:
        with self._lock:
            version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
            if version >= _SCHEMA_VERSION:
                migrated = self._migrate_v3_to_v4()
                migrated = self._migrate_v4_to_v5() or migrated
                if migrated:
                    self._conn.commit()
                return
            if self._table_has_column("entity_tier", "scope_key"):
                self._migrate_v1_to_v2()
                version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
            if version < _SCHEMA_VERSION:
                if not self._table_exists("entity_stats"):
                    self._conn.executescript(_SCHEMA_V2)
                elif not self._table_has_column("entity_stats", "attempts"):
                    self._conn.execute(
                        "ALTER TABLE entity_stats ADD COLUMN attempts REAL NOT NULL DEFAULT 0",
                    )
                if version < 4:
                    self._migrate_v3_to_v4()
                if version < 5:
                    self._migrate_v4_to_v5()
                self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
                self._conn.commit()
                return
            self._conn.executescript(_SCHEMA_V2)
            self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            self._conn.commit()

    def _migrate_v3_to_v4(self) -> bool:
        migrated = False
        if self._table_exists("epoch_state") and self._table_has_column(
            "epoch_state",
            "session_id",
        ):
            self._conn.execute(
                "ALTER TABLE epoch_state RENAME COLUMN session_id TO wake_cycle_id",
            )
            migrated = True
        if self._table_exists("entity_tier") and self._table_has_column(
            "entity_tier",
            "wake_lease_until_session",
        ):
            self._conn.execute(
                "ALTER TABLE entity_tier RENAME COLUMN wake_lease_until_session "
                "TO wake_lease_until_cycle",
            )
            self._conn.execute(
                "ALTER TABLE entity_tier RENAME COLUMN sleep_cooldown_until_session "
                "TO sleep_cooldown_until_cycle",
            )
            migrated = True
        return migrated

    def _migrate_v4_to_v5(self) -> bool:
        migrated = False
        if self._table_exists("entity_stats") and not self._table_has_column(
            "entity_stats",
            "epoch_used",
        ):
            self._conn.execute(
                "ALTER TABLE entity_stats ADD COLUMN epoch_used REAL NOT NULL DEFAULT 0",
            )
            migrated = True
        if self._table_exists("entity_stats") and not self._table_has_column(
            "entity_stats",
            "epoch_attempts",
        ):
            self._conn.execute(
                "ALTER TABLE entity_stats ADD COLUMN epoch_attempts REAL NOT NULL DEFAULT 0",
            )
            migrated = True
        if migrated:
            self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        return migrated

    def _migrate_v1_to_v2(self) -> None:  # noqa: C901
        now_ms = int(time.time() * 1000)
        v1_tables: list[str] = []
        for table in ("epoch_state", "entity_stats", "entity_tier", "epoch_log"):
            if self._table_exists(table) and self._table_has_column(table, "scope_key"):
                legacy = f"{table}_v1"
                self._conn.execute(f"ALTER TABLE {table} RENAME TO {legacy}")
                v1_tables.append(legacy)

        self._conn.executescript(_SCHEMA_V2)

        scope_keys: set[str] = set()
        for legacy in v1_tables:
            rows = self._conn.execute(f"SELECT DISTINCT scope_key FROM {legacy}").fetchall()
            scope_keys.update(str(row[0]) for row in rows if row[0])

        scope_to_project: dict[str, int] = {}
        for scope_key in scope_keys:
            root_path = _workspace_from_v1_scope_key(scope_key)
            if root_path is None:
                continue
            canonical = str(Path(root_path).expanduser().resolve())
            row = self._conn.execute(
                "SELECT project_id FROM tier_project WHERE root_path = ?",
                (canonical,),
            ).fetchone()
            if row is not None:
                project_id = int(row[0])
            else:
                cur = self._conn.execute(
                    "INSERT INTO tier_project(root_path, created_ms, last_seen_ms) VALUES (?, ?, ?)",
                    (canonical, now_ms, now_ms),
                )
                project_id = _require_lastrowid(cur)
            scope_to_project[scope_key] = project_id

        if "epoch_state_v1" in v1_tables:
            for row in self._conn.execute(
                "SELECT scope_key, epoch_id, epoch_start_ms, last_request_ms, session_id FROM epoch_state_v1",
            ).fetchall():
                mapped_project_id = scope_to_project.get(str(row[0]))
                if mapped_project_id is None:
                    continue
                self._conn.execute(
                    "INSERT INTO epoch_state(project_id, epoch_id, epoch_start_ms, "
                    "last_request_ms, wake_cycle_id) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(project_id) DO UPDATE SET "
                    "epoch_id=excluded.epoch_id, epoch_start_ms=excluded.epoch_start_ms, "
                    "last_request_ms=excluded.last_request_ms, "
                    "wake_cycle_id=excluded.wake_cycle_id",
                    (mapped_project_id, row[1], row[2], row[3], row[4]),
                )

        if "entity_tier_v1" in v1_tables:
            for row in self._conn.execute(
                "SELECT scope_key, kind, entity_id, stable_tier, effective_tier, overlap_tier, "
                "tier_since_epoch, temp_promotion_until_ms, wake_lease_until_session, "
                "sleep_cooldown_until_session FROM entity_tier_v1",
            ).fetchall():
                mapped_project_id = scope_to_project.get(str(row[0]))
                if mapped_project_id is None:
                    continue
                self._conn.execute(
                    "INSERT INTO entity_tier(project_id, kind, entity_id, stable_tier, effective_tier, "
                    "overlap_tier, tier_since_epoch, temp_promotion_until_ms, wake_lease_until_cycle, "
                    "sleep_cooldown_until_cycle) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(project_id, kind, entity_id) DO NOTHING",
                    (
                        mapped_project_id,
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        row[5],
                        row[6],
                        row[7],
                        row[8],
                        row[9],
                    ),
                )

        if "entity_stats_v1" in v1_tables:
            for row in self._conn.execute(
                "SELECT scope_key, kind, entity_id, pipeline, candidates, injected, used, "
                "used_without_injection, optional_used, shadow_hits, shadow_evaluations, "
                "last_seen_ms, requests_since_decay FROM entity_stats_v1",
            ).fetchall():
                mapped_project_id = scope_to_project.get(str(row[0]))
                if mapped_project_id is None:
                    continue
                self._conn.execute(
                    "INSERT INTO entity_stats(project_id, kind, entity_id, pipeline, candidates, injected, "
                    "used, used_without_injection, optional_used, shadow_hits, shadow_evaluations, "
                    "last_seen_ms, requests_since_decay) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(project_id, kind, entity_id, pipeline) DO NOTHING",
                    (
                        mapped_project_id,
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        row[5],
                        row[6],
                        row[7],
                        row[8],
                        row[9],
                        row[10],
                        row[11],
                        row[12],
                    ),
                )

        if "epoch_log_v1" in v1_tables:
            for row in self._conn.execute(
                "SELECT scope_key, epoch_id, ts_ms, transitions_json FROM epoch_log_v1",
            ).fetchall():
                mapped_project_id = scope_to_project.get(str(row[0]))
                if mapped_project_id is None:
                    continue
                self._conn.execute(
                    "INSERT INTO epoch_log(project_id, epoch_id, ts_ms, transitions_json) VALUES (?, ?, ?, ?)",
                    (mapped_project_id, row[1], row[2], row[3]),
                )

        for legacy in v1_tables:
            self._conn.execute(f"DROP TABLE {legacy}")

        self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        self._conn.commit()

    def _table_exists(self, table: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None

    def list_projects(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT project_id, root_path, created_ms, last_seen_ms "
                "FROM tier_project ORDER BY last_seen_ms DESC",
            ).fetchall()
        return [
            {
                "project_id": int(project_id),
                "root_path": str(root_path),
                "created_ms": int(created_ms),
                "last_seen_ms": int(last_seen_ms),
            }
            for project_id, root_path, created_ms, last_seen_ms in rows
        ]

    def get_or_create_project(self, root_path: str) -> int:
        canonical = str(Path(root_path).expanduser().resolve())
        now_ms = int(time.time() * 1000)
        with self._lock:
            row = self._conn.execute(
                "SELECT project_id FROM tier_project WHERE root_path = ?",
                (canonical,),
            ).fetchone()
            if row is not None:
                project_id = int(row[0])
                self._conn.execute(
                    "UPDATE tier_project SET last_seen_ms = ? WHERE project_id = ?",
                    (now_ms, project_id),
                )
                self._conn.commit()
                return project_id
            cur = self._conn.execute(
                "INSERT INTO tier_project(root_path, created_ms, last_seen_ms) VALUES (?, ?, ?)",
                (canonical, now_ms, now_ms),
            )
            self._conn.commit()
            return _require_lastrowid(cur)

    def load_epoch_state(self, project: TierProject) -> EpochState:
        with self._lock:
            row = self._conn.execute(
                "SELECT epoch_id, epoch_start_ms, last_request_ms, wake_cycle_id "
                "FROM epoch_state WHERE project_id = ?",
                (project.project_id,),
            ).fetchone()
        if row is None:
            now = int(time.time() * 1000)
            return EpochState(epoch_id=1, epoch_start_ms=now, last_request_ms=now, wake_cycle_id=1)
        return EpochState(
            epoch_id=int(row[0]),
            epoch_start_ms=int(row[1]),
            last_request_ms=int(row[2]),
            wake_cycle_id=int(row[3]),
        )

    def save_epoch_state(self, project: TierProject, epoch: EpochState) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO epoch_state(project_id, epoch_id, epoch_start_ms, last_request_ms, "
                "wake_cycle_id) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(project_id) DO UPDATE SET "
                "epoch_id=excluded.epoch_id, epoch_start_ms=excluded.epoch_start_ms, "
                "last_request_ms=excluded.last_request_ms, wake_cycle_id=excluded.wake_cycle_id",
                (
                    project.project_id,
                    epoch.epoch_id,
                    epoch.epoch_start_ms,
                    epoch.last_request_ms,
                    epoch.wake_cycle_id,
                ),
            )
            self._conn.commit()

    def load_entity_states(self, project: TierProject) -> dict[tuple[str, str], EntityTierState]:
        with self._lock:
            tier_rows = self._conn.execute(
                "SELECT kind, entity_id, stable_tier, effective_tier, overlap_tier, "
                "tier_since_epoch, temp_promotion_until_ms, wake_lease_until_cycle, "
                "sleep_cooldown_until_cycle FROM entity_tier WHERE project_id = ?",
                (project.project_id,),
            ).fetchall()
            has_epoch_stats = self._table_has_column("entity_stats", "epoch_used")
            stat_select = (
                "SELECT kind, entity_id, pipeline, candidates, injected, used, attempts, "
                "used_without_injection, optional_used, shadow_hits, shadow_evaluations, "
                "last_seen_ms, requests_since_decay"
            )
            if has_epoch_stats:
                stat_select += ", epoch_used, epoch_attempts"
            stat_select += " FROM entity_stats WHERE project_id = ?"
            stat_rows = self._conn.execute(
                stat_select,
                (project.project_id,),
            ).fetchall()
        stats_by_entity: dict[tuple[str, str], EffectiveStats] = {}
        pipeline_by_entity: dict[tuple[str, str], str] = {}
        for row in stat_rows:
            key = (str(row[0]), str(row[1]))
            pipeline_by_entity[key] = str(row[2])
            stats_by_entity[key] = EffectiveStats(
                candidates=float(row[3]),
                injected=float(row[4]),
                used=float(row[5]),
                attempts=float(row[6]),
                used_without_injection=float(row[7]),
                optional_used=float(row[8]),
                shadow_hits=float(row[9]),
                shadow_evaluations=float(row[10]),
                last_seen_ms=int(row[11]),
                requests_since_decay=int(row[12]),
                epoch_used=float(row[13]) if has_epoch_stats and len(row) > 13 else 0.0,
                epoch_attempts=float(row[14]) if has_epoch_stats and len(row) > 14 else 0.0,
            )
        out: dict[tuple[str, str], EntityTierState] = {}
        for row in tier_rows:
            key = (str(row[0]), str(row[1]))
            overlap = row[4]
            out[key] = EntityTierState(
                entity_id=str(row[1]),
                kind=str(row[0]),
                stable_tier=Tier(int(row[2])),
                effective_tier=Tier(int(row[3])),
                overlap_tier=Tier(int(overlap)) if overlap is not None else None,
                tier_since_epoch=int(row[5]),
                temp_promotion_until_ms=int(row[6]) if row[6] is not None else None,
                wake_lease_until_cycle=int(row[7]),
                sleep_cooldown_until_cycle=int(row[8]),
                pipeline=pipeline_by_entity.get(key, "default"),
                stats=stats_by_entity.get(key, EffectiveStats()),
            )
        return out

    def delete_entity_state(self, project: TierProject, *, kind: str, entity_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM entity_tier WHERE project_id = ? AND kind = ? AND entity_id = ?",
                (project.project_id, kind, entity_id),
            )
            self._conn.execute(
                "DELETE FROM entity_stats WHERE project_id = ? AND kind = ? AND entity_id = ?",
                (project.project_id, kind, entity_id),
            )
            self._conn.commit()

    def upsert_entity_state(self, project: TierProject, state: EntityTierState) -> None:
        from cyt.tiers.adapters.skills import is_ephemeral_skill_path

        if is_ephemeral_skill_path(state.entity_id):
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO entity_tier(project_id, kind, entity_id, stable_tier, effective_tier, "
                "overlap_tier, tier_since_epoch, temp_promotion_until_ms, wake_lease_until_cycle, "
                "sleep_cooldown_until_cycle) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(project_id, kind, entity_id) DO UPDATE SET "
                "stable_tier=excluded.stable_tier, effective_tier=excluded.effective_tier, "
                "overlap_tier=excluded.overlap_tier, tier_since_epoch=excluded.tier_since_epoch, "
                "temp_promotion_until_ms=excluded.temp_promotion_until_ms, "
                "wake_lease_until_cycle=excluded.wake_lease_until_cycle, "
                "sleep_cooldown_until_cycle=excluded.sleep_cooldown_until_cycle",
                (
                    project.project_id,
                    state.kind,
                    state.entity_id,
                    int(state.stable_tier),
                    int(state.effective_tier),
                    int(state.overlap_tier) if state.overlap_tier is not None else None,
                    state.tier_since_epoch,
                    state.temp_promotion_until_ms,
                    state.wake_lease_until_cycle,
                    state.sleep_cooldown_until_cycle,
                ),
            )
            stats = state.stats
            self._conn.execute(
                "INSERT INTO entity_stats(project_id, kind, entity_id, pipeline, candidates, injected, "
                "used, attempts, used_without_injection, optional_used, shadow_hits, shadow_evaluations, "
                "last_seen_ms, requests_since_decay, epoch_used, epoch_attempts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(project_id, kind, entity_id, pipeline) DO UPDATE SET "
                "candidates=excluded.candidates, injected=excluded.injected, used=excluded.used, "
                "attempts=excluded.attempts, "
                "used_without_injection=excluded.used_without_injection, "
                "optional_used=excluded.optional_used, shadow_hits=excluded.shadow_hits, "
                "shadow_evaluations=excluded.shadow_evaluations, last_seen_ms=excluded.last_seen_ms, "
                "requests_since_decay=excluded.requests_since_decay, "
                "epoch_used=excluded.epoch_used, epoch_attempts=excluded.epoch_attempts",
                (
                    project.project_id,
                    state.kind,
                    state.entity_id,
                    state.pipeline,
                    stats.candidates,
                    stats.injected,
                    stats.used,
                    stats.attempts,
                    stats.used_without_injection,
                    stats.optional_used,
                    stats.shadow_hits,
                    stats.shadow_evaluations,
                    stats.last_seen_ms,
                    stats.requests_since_decay,
                    stats.epoch_used,
                    stats.epoch_attempts,
                ),
            )
            self._conn.commit()

    def append_epoch_log(
        self,
        project: TierProject,
        *,
        epoch_id: int,
        transitions: list[TierTransition],
    ) -> None:
        payload = [
            {
                "kind": t.kind,
                "entity_id": t.entity_id,
                "from": int(t.from_tier),
                "to": int(t.to_tier),
                "reason": t.reason,
                "temporary": t.temporary,
            }
            for t in transitions
        ]
        with self._lock:
            self._conn.execute(
                "INSERT INTO epoch_log(project_id, epoch_id, ts_ms, transitions_json) VALUES (?, ?, ?, ?)",
                (project.project_id, epoch_id, int(time.time() * 1000), json.dumps(payload)),
            )
            self._conn.commit()

    def purge_ephemeral_projects(self) -> int:
        """Delete tier rows for pytest/macOS temp workspace roots and temp entity ids."""
        from cyt.tiers.adapters.skills import is_ephemeral_skill_path

        removed_projects = 0
        with self._lock:
            project_rows = self._conn.execute(
                "SELECT project_id, root_path FROM tier_project",
            ).fetchall()
            for project_id, root_path in project_rows:
                if not is_ephemeral_workspace_path(str(root_path)):
                    continue
                pid = int(project_id)
                self._conn.execute("DELETE FROM entity_tier WHERE project_id = ?", (pid,))
                self._conn.execute("DELETE FROM entity_stats WHERE project_id = ?", (pid,))
                self._conn.execute("DELETE FROM epoch_state WHERE project_id = ?", (pid,))
                self._conn.execute("DELETE FROM epoch_log WHERE project_id = ?", (pid,))
                self._conn.execute("DELETE FROM tier_project WHERE project_id = ?", (pid,))
                removed_projects += 1

            for table in ("entity_tier", "entity_stats"):
                rows = self._conn.execute(
                    f"SELECT project_id, kind, entity_id FROM {table}",
                ).fetchall()
                for project_id, kind, entity_id in rows:
                    if not is_ephemeral_skill_path(str(entity_id)):
                        continue
                    self._conn.execute(
                        f"DELETE FROM {table} WHERE project_id = ? AND kind = ? AND entity_id = ?",
                        (int(project_id), str(kind), str(entity_id)),
                    )
            self._conn.commit()
        return removed_projects

    def _delete_skill_entity(self, project_id: int, entity_id: str) -> None:
        from cyt.tiers.models import EntityKind

        self._conn.execute(
            "DELETE FROM entity_tier WHERE project_id = ? AND kind = ? AND entity_id = ?",
            (project_id, EntityKind.SKILL, entity_id),
        )
        self._conn.execute(
            "DELETE FROM entity_stats WHERE project_id = ? AND kind = ? AND entity_id = ?",
            (project_id, EntityKind.SKILL, entity_id),
        )

    def _merge_skill_entity_stats_rows(
        self,
        project_id: int,
        *,
        source_id: str,
        target_id: str,
    ) -> None:
        from cyt.tiers.models import EntityKind

        source = self._conn.execute(
            "SELECT candidates, injected, used, attempts, used_without_injection, optional_used, "
            "shadow_hits, shadow_evaluations, last_seen_ms, requests_since_decay, "
            "epoch_used, epoch_attempts "
            "FROM entity_stats WHERE project_id = ? AND kind = ? AND entity_id = ?",
            (project_id, EntityKind.SKILL, source_id),
        ).fetchone()
        if source is None:
            return
        target_row = self._conn.execute(
            "SELECT last_seen_ms FROM entity_stats WHERE project_id = ? AND kind = ? AND entity_id = ?",
            (project_id, EntityKind.SKILL, target_id),
        ).fetchone()
        merged_last_seen = int(source[8])
        if target_row is not None:
            merged_last_seen = max(merged_last_seen, int(target_row[0]))
        self._conn.execute(
            "UPDATE entity_stats SET "
            "candidates = candidates + ?, injected = injected + ?, used = used + ?, "
            "attempts = attempts + ?, used_without_injection = used_without_injection + ?, "
            "optional_used = optional_used + ?, shadow_hits = shadow_hits + ?, "
            "shadow_evaluations = shadow_evaluations + ?, last_seen_ms = ?, "
            "requests_since_decay = requests_since_decay + ?, "
            "epoch_used = epoch_used + ?, epoch_attempts = epoch_attempts + ? "
            "WHERE project_id = ? AND kind = ? AND entity_id = ?",
            (
                source[0],
                source[1],
                source[2],
                source[3],
                source[4],
                source[5],
                source[6],
                source[7],
                merged_last_seen,
                source[9],
                source[10],
                source[11],
                project_id,
                EntityKind.SKILL,
                target_id,
            ),
        )

    def purge_stale_skill_doc_entities(self) -> int:
        """Remove generic skill:doc placeholders and merge duplicates onto path entities."""
        from cyt.tiers.adapters.skills import (
            _NON_SKILL_SKILL_ENTITY_IDS,
            _SKILL_DOC_ENTITY_PREFIX,
            find_canonical_skill_path_for_doc_id,
            is_stale_skill_doc_entity,
            skill_doc_id_from_entity_id,
        )
        from cyt.tiers.models import EntityKind

        removed = 0
        with self._lock:
            project_rows = self._conn.execute("SELECT project_id FROM tier_project").fetchall()
            for (project_id,) in project_rows:
                pid = int(project_id)
                rows = self._conn.execute(
                    "SELECT entity_id FROM entity_tier WHERE project_id = ? AND kind = ?",
                    (pid, EntityKind.SKILL),
                ).fetchall()
                entity_ids = {str(row[0]) for row in rows}

                for doc_entity in sorted(entity_ids):
                    if not doc_entity.startswith(_SKILL_DOC_ENTITY_PREFIX):
                        continue
                    if not is_stale_skill_doc_entity(doc_entity, entity_ids):
                        continue
                    doc_id = skill_doc_id_from_entity_id(doc_entity)
                    canonical = (
                        find_canonical_skill_path_for_doc_id(doc_id or "", entity_ids)
                        if doc_id
                        else None
                    )
                    if canonical:
                        self._merge_skill_entity_stats_rows(
                            pid,
                            source_id=doc_entity,
                            target_id=canonical,
                        )
                    self._delete_skill_entity(pid, doc_entity)
                    entity_ids.discard(doc_entity)
                    removed += 1

                for artifact in _NON_SKILL_SKILL_ENTITY_IDS:
                    if artifact not in entity_ids:
                        continue
                    self._delete_skill_entity(pid, artifact)
                    entity_ids.discard(artifact)
                    removed += 1

            self._conn.commit()
        return removed

    _STAT_COUNTER_COLUMNS = (
        "candidates",
        "injected",
        "used",
        "attempts",
        "used_without_injection",
        "optional_used",
        "shadow_hits",
        "shadow_evaluations",
        "epoch_used",
        "epoch_attempts",
    )

    def apply_stat_decay(self, *, request_half_life: float) -> int:
        """Apply request-based decay to persisted entity_stats (same math as runtime)."""
        from cyt.tiers.scores import decay_factor

        updated = 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT project_id, kind, entity_id, pipeline, candidates, injected, used, "
                "attempts, used_without_injection, optional_used, shadow_hits, "
                "shadow_evaluations, requests_since_decay, epoch_used, epoch_attempts "
                "FROM entity_stats WHERE requests_since_decay > 0",
            ).fetchall()
            for row in rows:
                requests_since = int(row[12])
                factor = decay_factor(
                    requests_since=requests_since,
                    half_life=request_half_life,
                )
                if factor >= 1.0:
                    continue
                values = [float(row[idx]) * factor for idx in range(4, 12)]
                values.extend([float(row[13]) * factor, float(row[14]) * factor])
                self._conn.execute(
                    "UPDATE entity_stats SET "
                    "candidates=?, injected=?, used=?, attempts=?, "
                    "used_without_injection=?, optional_used=?, shadow_hits=?, "
                    "shadow_evaluations=?, requests_since_decay=0, "
                    "epoch_used=?, epoch_attempts=? "
                    "WHERE project_id=? AND kind=? AND entity_id=? AND pipeline=?",
                    (
                        *values,
                        int(row[0]),
                        str(row[1]),
                        str(row[2]),
                        str(row[3]),
                    ),
                )
                updated += 1
            self._conn.commit()
        return updated

    @staticmethod
    def _counters_below_floor(values: tuple[float, ...], *, counter_floor: float) -> bool:
        for value in values:
            if float(value) >= counter_floor:
                return False
        return True

    def prune_dormant_entities(
        self,
        *,
        idle_cutoff_ms: int,
        counter_floor: float,
        max_effective_tier: int = int(Tier.COLD),
    ) -> int:
        """Delete cold entities idle beyond cutoff with negligible counters."""
        removed = 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.project_id, s.kind, s.entity_id, s.pipeline, "
                "s.candidates, s.injected, s.used, s.attempts, "
                "s.used_without_injection, s.optional_used, s.shadow_hits, "
                "s.shadow_evaluations, s.last_seen_ms, s.epoch_used, s.epoch_attempts, "
                "t.effective_tier "
                "FROM entity_stats s "
                "LEFT JOIN entity_tier t "
                "ON s.project_id = t.project_id AND s.kind = t.kind AND s.entity_id = t.entity_id",
            ).fetchall()
            for row in rows:
                last_seen_ms = int(row[12])
                effective_tier = int(row[15]) if row[15] is not None else int(Tier.ACTIVE)
                if last_seen_ms >= idle_cutoff_ms:
                    continue
                if effective_tier <= int(Tier.DORMANT):
                    continue
                if effective_tier > max_effective_tier:
                    continue
                counters = (
                    float(row[4]),
                    float(row[5]),
                    float(row[6]),
                    float(row[7]),
                    float(row[8]),
                    float(row[9]),
                    float(row[10]),
                    float(row[11]),
                    float(row[13]),
                    float(row[14]),
                )
                if not self._counters_below_floor(counters, counter_floor=counter_floor):
                    continue
                pid = int(row[0])
                kind = str(row[1])
                entity_id = str(row[2])
                self._conn.execute(
                    "DELETE FROM entity_tier WHERE project_id=? AND kind=? AND entity_id=?",
                    (pid, kind, entity_id),
                )
                self._conn.execute(
                    "DELETE FROM entity_stats WHERE project_id=? AND kind=? AND entity_id=?",
                    (pid, kind, entity_id),
                )
                removed += 1
            self._conn.commit()
        return removed

    def purge_stale_catalog_tool_entities(
        self,
        *,
        allowed_sources: frozenset[str],
        catalog_entity_ids: frozenset[str] | None = None,
    ) -> int:
        """Delete tool rows outside allowed catalog sources or the tracked hook catalog."""
        from cyt.tiers.adapters.tools import entity_id_catalog_source
        from cyt.tiers.models import EntityKind

        removed = 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT project_id, entity_id FROM entity_stats WHERE kind = ?",
                (EntityKind.TOOL,),
            ).fetchall()
            for project_id, entity_id in rows:
                eid = str(entity_id)
                source = entity_id_catalog_source(eid)
                if source not in allowed_sources:
                    stale = True
                elif catalog_entity_ids is not None and eid not in catalog_entity_ids:
                    stale = True
                else:
                    stale = False
                if not stale:
                    continue
                pid = int(project_id)
                self._conn.execute(
                    "DELETE FROM entity_tier WHERE project_id=? AND kind=? AND entity_id=?",
                    (pid, EntityKind.TOOL, eid),
                )
                self._conn.execute(
                    "DELETE FROM entity_stats WHERE project_id=? AND kind=? AND entity_id=?",
                    (pid, EntityKind.TOOL, eid),
                )
                removed += 1
            self._conn.commit()
        return removed

    def prune_epoch_log(
        self,
        *,
        max_age_ms: int,
        max_entries: int,
    ) -> int:
        """Delete epoch_log rows older than max age and beyond per-project entry cap."""
        removed = 0
        with self._lock:
            if max_age_ms > 0:
                cur = self._conn.execute(
                    "DELETE FROM epoch_log WHERE ts_ms < ?",
                    (max_age_ms,),
                )
                removed += int(cur.rowcount)

            project_rows = self._conn.execute(
                "SELECT DISTINCT project_id FROM epoch_log",
            ).fetchall()
            for (project_id,) in project_rows:
                pid = int(project_id)
                count = self._conn.execute(
                    "SELECT COUNT(*) FROM epoch_log WHERE project_id = ?",
                    (pid,),
                ).fetchone()[0]
                excess = int(count) - max(max_entries, 0)
                if excess <= 0:
                    continue
                old_rows = self._conn.execute(
                    "SELECT id FROM epoch_log WHERE project_id = ? ORDER BY ts_ms ASC LIMIT ?",
                    (pid, excess),
                ).fetchall()
                for (row_id,) in old_rows:
                    self._conn.execute("DELETE FROM epoch_log WHERE id = ?", (int(row_id),))
                    removed += 1
            self._conn.commit()
        return removed

    def vacuum(self) -> None:
        """Rebuild the database file and reclaim space freed by deletes."""
        with self._lock:
            self._conn.execute("VACUUM")

    def count_catalog_tool_entities(self, *, allowed_sources: frozenset[str]) -> int:
        """Count persisted tool rows whose catalog source is in *allowed_sources*."""
        from cyt.tiers.adapters.tools import entity_id_catalog_source
        from cyt.tiers.models import EntityKind

        with self._lock:
            rows = self._conn.execute(
                "SELECT entity_id FROM entity_stats WHERE kind = ?",
                (EntityKind.TOOL,),
            ).fetchall()
        count = 0
        for (entity_id,) in rows:
            if entity_id_catalog_source(str(entity_id)) in allowed_sources:
                count += 1
        return count

    def count_stats_needing_decay(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM entity_stats WHERE requests_since_decay > 0",
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def count_dormant_entities(
        self,
        *,
        idle_cutoff_ms: int,
        counter_floor: float,
        max_effective_tier: int = int(Tier.COLD),
    ) -> int:
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.candidates, s.injected, s.used, s.attempts, "
                "s.used_without_injection, s.optional_used, s.shadow_hits, "
                "s.shadow_evaluations, s.last_seen_ms, s.epoch_used, s.epoch_attempts, "
                "t.effective_tier "
                "FROM entity_stats s "
                "LEFT JOIN entity_tier t "
                "ON s.project_id = t.project_id AND s.kind = t.kind AND s.entity_id = t.entity_id",
            ).fetchall()
        count = 0
        for row in rows:
            last_seen_ms = int(row[8])
            effective_tier = int(row[11]) if row[11] is not None else int(Tier.ACTIVE)
            if last_seen_ms >= idle_cutoff_ms:
                continue
            if effective_tier > max_effective_tier:
                continue
            counters = (
                float(row[0]),
                float(row[1]),
                float(row[2]),
                float(row[3]),
                float(row[4]),
                float(row[5]),
                float(row[6]),
                float(row[7]),
                float(row[9]),
                float(row[10]),
            )
            if self._counters_below_floor(counters, counter_floor=counter_floor):
                count += 1
        return count

    def count_epoch_log_prunable(self, *, max_age_ms: int, max_entries: int) -> int:
        with self._lock:
            age_count = self._conn.execute(
                "SELECT COUNT(*) FROM epoch_log WHERE ts_ms < ?",
                (max_age_ms,),
            ).fetchone()[0]
            excess = 0
            project_rows = self._conn.execute(
                "SELECT project_id, COUNT(*) FROM epoch_log GROUP BY project_id",
            ).fetchall()
            for _project_id, count in project_rows:
                over = int(count) - max(max_entries, 0)
                if over > 0:
                    excess += over
        return int(age_count) + excess

    def status_summary(self, project: TierProject) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind, effective_tier, COUNT(*) FROM entity_tier "
                "WHERE project_id = ? GROUP BY kind, effective_tier ORDER BY kind, effective_tier",
                (project.project_id,),
            ).fetchall()
        histogram: dict[str, dict[str, int]] = {}
        for kind, tier, count in rows:
            histogram.setdefault(str(kind), {})[f"T{int(tier)}"] = int(count)
        return {
            "project_id": project.project_id,
            "root_path": str(project.root_path),
            "histogram": histogram,
        }


def _workspace_from_v1_scope_key(scope_key: str) -> str | None:
    if "::" in scope_key:
        workspace = scope_key.split("::", 1)[1].strip()
        return workspace or None
    return None
