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
    TierScope,
    TierTransition,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS epoch_state (
    scope_key TEXT PRIMARY KEY,
    epoch_id INTEGER NOT NULL,
    epoch_start_ms INTEGER NOT NULL,
    last_request_ms INTEGER NOT NULL,
    session_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_stats (
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

CREATE TABLE IF NOT EXISTS entity_tier (
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

CREATE TABLE IF NOT EXISTS epoch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_key TEXT NOT NULL,
    epoch_id INTEGER NOT NULL,
    ts_ms INTEGER NOT NULL,
    transitions_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entity_tier_scope ON entity_tier(scope_key);
CREATE INDEX IF NOT EXISTS idx_entity_stats_scope ON entity_stats(scope_key, kind, entity_id);
"""


class TierStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = str(Path(db_path).expanduser())
        self._lock = threading.RLock()
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=TRUNCATE")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    @classmethod
    def open(cls, db_path: str) -> TierStore:
        return cls(db_path)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def load_epoch_state(self, scope: TierScope) -> EpochState:
        with self._lock:
            row = self._conn.execute(
                "SELECT epoch_id, epoch_start_ms, last_request_ms, session_id "
                "FROM epoch_state WHERE scope_key = ?",
                (scope.scope_key,),
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

    def save_epoch_state(self, scope: TierScope, epoch: EpochState) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO epoch_state(scope_key, epoch_id, epoch_start_ms, last_request_ms, session_id) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(scope_key) DO UPDATE SET "
                "epoch_id=excluded.epoch_id, epoch_start_ms=excluded.epoch_start_ms, "
                "last_request_ms=excluded.last_request_ms, session_id=excluded.session_id",
                (
                    scope.scope_key,
                    epoch.epoch_id,
                    epoch.epoch_start_ms,
                    epoch.last_request_ms,
                    epoch.session_id,
                ),
            )
            self._conn.commit()

    def load_entity_states(self, scope: TierScope) -> dict[tuple[str, str], EntityTierState]:
        with self._lock:
            tier_rows = self._conn.execute(
                "SELECT kind, entity_id, stable_tier, effective_tier, overlap_tier, "
                "tier_since_epoch, temp_promotion_until_ms, wake_lease_until_session, "
                "sleep_cooldown_until_session FROM entity_tier WHERE scope_key = ?",
                (scope.scope_key,),
            ).fetchall()
            stat_rows = self._conn.execute(
                "SELECT kind, entity_id, pipeline, candidates, injected, used, "
                "used_without_injection, optional_used, shadow_hits, shadow_evaluations, "
                "last_seen_ms, requests_since_decay FROM entity_stats WHERE scope_key = ?",
                (scope.scope_key,),
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

    def upsert_entity_state(self, scope: TierScope, state: EntityTierState) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO entity_tier(scope_key, kind, entity_id, stable_tier, effective_tier, "
                "overlap_tier, tier_since_epoch, temp_promotion_until_ms, wake_lease_until_session, "
                "sleep_cooldown_until_session) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(scope_key, kind, entity_id) DO UPDATE SET "
                "stable_tier=excluded.stable_tier, effective_tier=excluded.effective_tier, "
                "overlap_tier=excluded.overlap_tier, tier_since_epoch=excluded.tier_since_epoch, "
                "temp_promotion_until_ms=excluded.temp_promotion_until_ms, "
                "wake_lease_until_session=excluded.wake_lease_until_session, "
                "sleep_cooldown_until_session=excluded.sleep_cooldown_until_session",
                (
                    scope.scope_key,
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
                "INSERT INTO entity_stats(scope_key, kind, entity_id, pipeline, candidates, injected, "
                "used, used_without_injection, optional_used, shadow_hits, shadow_evaluations, "
                "last_seen_ms, requests_since_decay) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(scope_key, kind, entity_id, pipeline) DO UPDATE SET "
                "candidates=excluded.candidates, injected=excluded.injected, used=excluded.used, "
                "used_without_injection=excluded.used_without_injection, "
                "optional_used=excluded.optional_used, shadow_hits=excluded.shadow_hits, "
                "shadow_evaluations=excluded.shadow_evaluations, last_seen_ms=excluded.last_seen_ms, "
                "requests_since_decay=excluded.requests_since_decay",
                (
                    scope.scope_key,
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
        scope: TierScope,
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
                "INSERT INTO epoch_log(scope_key, epoch_id, ts_ms, transitions_json) VALUES (?, ?, ?, ?)",
                (scope.scope_key, epoch_id, int(time.time() * 1000), json.dumps(payload)),
            )
            self._conn.commit()

    def status_summary(self, scope: TierScope) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind, effective_tier, COUNT(*) FROM entity_tier "
                "WHERE scope_key = ? GROUP BY kind, effective_tier ORDER BY kind, effective_tier",
                (scope.scope_key,),
            ).fetchall()
        histogram: dict[str, dict[str, int]] = {}
        for kind, tier, count in rows:
            histogram.setdefault(str(kind), {})[f"T{int(tier)}"] = int(count)
        return {"scope": scope.scope_key, "histogram": histogram}
