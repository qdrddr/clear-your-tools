"""SQLite persistence for tier state."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from cyt.tiers.models import (
    EffectiveStats,
    EntityTierState,
    EpochState,
    Tier,
    TierProject,
    TierTransition,
)

_SCHEMA_VERSION = 2

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
    session_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_stats (
    project_id INTEGER NOT NULL REFERENCES tier_project(project_id),
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
    wake_lease_until_session INTEGER NOT NULL DEFAULT 0,
    sleep_cooldown_until_session INTEGER NOT NULL DEFAULT 0,
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
        self._conn.execute("PRAGMA journal_mode=TRUNCATE")
        self._ensure_schema()

    @classmethod
    def open(cls, db_path: str) -> TierStore:
        return cls(db_path)

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
                self._conn.executescript(_SCHEMA_V2)
                self._conn.commit()
                return
            if self._table_has_column("entity_tier", "scope_key"):
                self._migrate_v1_to_v2()
                return
            self._conn.executescript(_SCHEMA_V2)
            self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            self._conn.commit()

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
                    "INSERT INTO epoch_state(project_id, epoch_id, epoch_start_ms, last_request_ms, session_id) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(project_id) DO UPDATE SET "
                    "epoch_id=excluded.epoch_id, epoch_start_ms=excluded.epoch_start_ms, "
                    "last_request_ms=excluded.last_request_ms, session_id=excluded.session_id",
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
                    "overlap_tier, tier_since_epoch, temp_promotion_until_ms, wake_lease_until_session, "
                    "sleep_cooldown_until_session) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
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
                "SELECT epoch_id, epoch_start_ms, last_request_ms, session_id "
                "FROM epoch_state WHERE project_id = ?",
                (project.project_id,),
            ).fetchone()
        if row is None:
            now = int(time.time() * 1000)
            return EpochState(epoch_id=1, epoch_start_ms=now, last_request_ms=now, session_id=1)
        return EpochState(
            epoch_id=int(row[0]),
            epoch_start_ms=int(row[1]),
            last_request_ms=int(row[2]),
            session_id=int(row[3]),
        )

    def save_epoch_state(self, project: TierProject, epoch: EpochState) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO epoch_state(project_id, epoch_id, epoch_start_ms, last_request_ms, session_id) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(project_id) DO UPDATE SET "
                "epoch_id=excluded.epoch_id, epoch_start_ms=excluded.epoch_start_ms, "
                "last_request_ms=excluded.last_request_ms, session_id=excluded.session_id",
                (
                    project.project_id,
                    epoch.epoch_id,
                    epoch.epoch_start_ms,
                    epoch.last_request_ms,
                    epoch.session_id,
                ),
            )
            self._conn.commit()

    def load_entity_states(self, project: TierProject) -> dict[tuple[str, str], EntityTierState]:
        with self._lock:
            tier_rows = self._conn.execute(
                "SELECT kind, entity_id, stable_tier, effective_tier, overlap_tier, "
                "tier_since_epoch, temp_promotion_until_ms, wake_lease_until_session, "
                "sleep_cooldown_until_session FROM entity_tier WHERE project_id = ?",
                (project.project_id,),
            ).fetchall()
            stat_rows = self._conn.execute(
                "SELECT kind, entity_id, pipeline, candidates, injected, used, "
                "used_without_injection, optional_used, shadow_hits, shadow_evaluations, "
                "last_seen_ms, requests_since_decay FROM entity_stats WHERE project_id = ?",
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
                used_without_injection=float(row[6]),
                optional_used=float(row[7]),
                shadow_hits=float(row[8]),
                shadow_evaluations=float(row[9]),
                last_seen_ms=int(row[10]),
                requests_since_decay=int(row[11]),
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
                wake_lease_until_session=int(row[7]),
                sleep_cooldown_until_session=int(row[8]),
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
        with self._lock:
            self._conn.execute(
                "INSERT INTO entity_tier(project_id, kind, entity_id, stable_tier, effective_tier, "
                "overlap_tier, tier_since_epoch, temp_promotion_until_ms, wake_lease_until_session, "
                "sleep_cooldown_until_session) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(project_id, kind, entity_id) DO UPDATE SET "
                "stable_tier=excluded.stable_tier, effective_tier=excluded.effective_tier, "
                "overlap_tier=excluded.overlap_tier, tier_since_epoch=excluded.tier_since_epoch, "
                "temp_promotion_until_ms=excluded.temp_promotion_until_ms, "
                "wake_lease_until_session=excluded.wake_lease_until_session, "
                "sleep_cooldown_until_session=excluded.sleep_cooldown_until_session",
                (
                    project.project_id,
                    state.kind,
                    state.entity_id,
                    int(state.stable_tier),
                    int(state.effective_tier),
                    int(state.overlap_tier) if state.overlap_tier is not None else None,
                    state.tier_since_epoch,
                    state.temp_promotion_until_ms,
                    state.wake_lease_until_session,
                    state.sleep_cooldown_until_session,
                ),
            )
            stats = state.stats
            self._conn.execute(
                "INSERT INTO entity_stats(project_id, kind, entity_id, pipeline, candidates, injected, "
                "used, used_without_injection, optional_used, shadow_hits, shadow_evaluations, "
                "last_seen_ms, requests_since_decay) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(project_id, kind, entity_id, pipeline) DO UPDATE SET "
                "candidates=excluded.candidates, injected=excluded.injected, used=excluded.used, "
                "used_without_injection=excluded.used_without_injection, "
                "optional_used=excluded.optional_used, shadow_hits=excluded.shadow_hits, "
                "shadow_evaluations=excluded.shadow_evaluations, last_seen_ms=excluded.last_seen_ms, "
                "requests_since_decay=excluded.requests_since_decay",
                (
                    project.project_id,
                    state.kind,
                    state.entity_id,
                    state.pipeline,
                    stats.candidates,
                    stats.injected,
                    stats.used,
                    stats.used_without_injection,
                    stats.optional_used,
                    stats.shadow_hits,
                    stats.shadow_evaluations,
                    stats.last_seen_ms,
                    stats.requests_since_decay,
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
