"""Session cache in ~/.config/cyt/cache.db."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import libsql_experimental as libsql

from cyt.config import skills_cache_db_path
from cyt.proxy.stats import expand_db_path

logger = logging.getLogger(__name__)

_DEFAULT_MAX_AGE_MS = 86_400_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    ts_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_ts ON session(ts_ms);
"""


class SessionCacheDB:
    def __init__(self, conn: libsql.Connection) -> None:
        self._conn = conn

    @classmethod
    def open(cls, config: dict | None = None) -> SessionCacheDB:
        db_path = expand_db_path(skills_cache_db_path(config))
        parent = Path(db_path).parent
        parent.mkdir(parents=True, exist_ok=True)
        conn = libsql.connect(db_path)
        conn.executescript(_SCHEMA)
        conn.commit()
        return cls(conn)

    def close(self) -> None:
        self._conn.close()

    def purge_stale(self, max_age_ms: int = _DEFAULT_MAX_AGE_MS) -> int:
        cutoff = int(time.time() * 1000) - max_age_ms
        cursor = self._conn.execute("DELETE FROM session WHERE ts_ms < ?", (cutoff,))
        self._conn.commit()
        return int(cursor.rowcount or 0)

    def upsert_session(self, session_id: str, model: str) -> None:
        ts_ms = int(time.time() * 1000)
        self._conn.execute(
            """
            INSERT INTO session (id, model, ts_ms)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                model = excluded.model,
                ts_ms = excluded.ts_ms
            """,
            (session_id, model, ts_ms),
        )
        self._conn.commit()

    def lookup_model(self, session_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT model FROM session WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return str(row[0])
