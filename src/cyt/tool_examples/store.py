"""SQLite persistence for per-project tool example memory."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyt.tool_examples.flatten import FlattenedExample, flatten_args
from cyt.tool_examples.hash_utils import content_hash

_SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_example_project (
    project_id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_path TEXT NOT NULL UNIQUE,
    created_ms INTEGER NOT NULL,
    last_seen_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_input_schema (
    schema_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES tool_example_project(project_id),
    mcp_server TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    schema_json TEXT NOT NULL,
    input_json TEXT NOT NULL,
    schema_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    first_seen_ms INTEGER NOT NULL,
    last_seen_ms INTEGER NOT NULL,
    UNIQUE(project_id, mcp_server, tool_name, schema_hash, input_hash)
);

CREATE TABLE IF NOT EXISTS tool_example (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_id INTEGER NOT NULL REFERENCES tool_input_schema(schema_id) ON DELETE CASCADE,
    json_path TEXT NOT NULL,
    value TEXT NOT NULL,
    value_type TEXT NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    UNIQUE(schema_id, json_path, value)
);

CREATE INDEX IF NOT EXISTS idx_tool_input_schema_lookup
    ON tool_input_schema(project_id, mcp_server, tool_name, last_seen_ms DESC);

CREATE INDEX IF NOT EXISTS idx_tool_example_lookup
    ON tool_example(schema_id, json_path, timestamp_ms DESC);
"""


@dataclass(frozen=True)
class ToolCapture:
    schema_id: int
    project_id: int
    mcp_server: str
    tool_name: str
    schema_json: dict[str, Any]
    input_json: dict[str, Any]
    schema_hash: str
    input_hash: str
    first_seen_ms: int
    last_seen_ms: int


@dataclass(frozen=True)
class ToolExampleRow:
    schema_id: int
    json_path: str
    value: str
    value_type: str
    timestamp_ms: int


class ToolExamplesStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = str(Path(db_path).expanduser())
        self._lock = threading.RLock()
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=TRUNCATE")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._ensure_schema()

    @classmethod
    def open(cls, db_path: str) -> ToolExamplesStore:
        return cls(db_path)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _ensure_schema(self) -> None:
        with self._lock:
            version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
            if version >= _SCHEMA_VERSION:
                self._conn.executescript(_SCHEMA)
                self._conn.commit()
                return
            self._conn.executescript(_SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            self._conn.commit()

    def get_or_create_project(self, root_path: str) -> int:
        canonical = str(Path(root_path).expanduser().resolve())
        now_ms = int(time.time() * 1000)
        with self._lock:
            row = self._conn.execute(
                "SELECT project_id FROM tool_example_project WHERE root_path = ?",
                (canonical,),
            ).fetchone()
            if row is not None:
                self._conn.execute(
                    "UPDATE tool_example_project SET last_seen_ms = ? WHERE project_id = ?",
                    (now_ms, row[0]),
                )
                self._conn.commit()
                return int(row[0])
            cur = self._conn.execute(
                "INSERT INTO tool_example_project(root_path, created_ms, last_seen_ms) VALUES (?, ?, ?)",
                (canonical, now_ms, now_ms),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def upsert_capture(
        self,
        project_id: int,
        mcp_server: str,
        tool_name: str,
        schema: dict[str, Any],
        args: dict[str, Any],
        *,
        flattened: list[FlattenedExample] | None = None,
    ) -> int:
        now_ms = int(time.time() * 1000)
        schema_hash = content_hash(schema)
        input_hash = content_hash(args)
        schema_text = json.dumps(schema, separators=(",", ":"), ensure_ascii=False)
        input_text = json.dumps(args, separators=(",", ":"), ensure_ascii=False)
        with self._lock:
            row = self._conn.execute(
                "SELECT schema_id FROM tool_input_schema "
                "WHERE project_id = ? AND mcp_server = ? AND tool_name = ? "
                "AND schema_hash = ? AND input_hash = ?",
                (project_id, mcp_server, tool_name, schema_hash, input_hash),
            ).fetchone()
            if row is not None:
                schema_id = int(row[0])
                self._conn.execute(
                    "UPDATE tool_input_schema SET last_seen_ms = ? WHERE schema_id = ?",
                    (now_ms, schema_id),
                )
            else:
                cur = self._conn.execute(
                    "INSERT INTO tool_input_schema("
                    "project_id, mcp_server, tool_name, schema_json, input_json, "
                    "schema_hash, input_hash, first_seen_ms, last_seen_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        project_id,
                        mcp_server,
                        tool_name,
                        schema_text,
                        input_text,
                        schema_hash,
                        input_hash,
                        now_ms,
                        now_ms,
                    ),
                )
                schema_id = int(cur.lastrowid)
            pairs = flattened if flattened is not None else flatten_args(args)
            for item in pairs:
                self._conn.execute(
                    "INSERT INTO tool_example(schema_id, json_path, value, value_type, timestamp_ms) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(schema_id, json_path, value) DO UPDATE SET timestamp_ms = excluded.timestamp_ms",
                    (schema_id, item.json_path, item.value, item.value_type, now_ms),
                )
            self._conn.commit()
            return schema_id

    def get_capture(self, schema_id: int) -> ToolCapture | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT schema_id, project_id, mcp_server, tool_name, schema_json, input_json, "
                "schema_hash, input_hash, first_seen_ms, last_seen_ms "
                "FROM tool_input_schema WHERE schema_id = ?",
                (schema_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_capture(row)

    def list_captures(
        self,
        project_id: int,
        mcp_server: str,
        tool_name: str,
        *,
        schema_hash: str | None = None,
        limit: int = 50,
    ) -> list[ToolCapture]:
        query = (
            "SELECT schema_id, project_id, mcp_server, tool_name, schema_json, input_json, "
            "schema_hash, input_hash, first_seen_ms, last_seen_ms "
            "FROM tool_input_schema WHERE project_id = ? AND mcp_server = ? AND tool_name = ?"
        )
        params: list[Any] = [project_id, mcp_server, tool_name]
        if schema_hash is not None:
            query += " AND schema_hash = ?"
            params.append(schema_hash)
        query += " ORDER BY last_seen_ms DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_capture(row) for row in rows]

    def list_examples_for_path(
        self,
        schema_ids: list[int],
        json_path: str,
        *,
        limit: int = 20,
    ) -> list[ToolExampleRow]:
        if not schema_ids:
            return []
        placeholders = ",".join("?" for _ in schema_ids)
        query = (
            f"SELECT schema_id, json_path, value, value_type, timestamp_ms "
            f"FROM tool_example WHERE schema_id IN ({placeholders}) AND json_path = ? "
            f"ORDER BY timestamp_ms DESC LIMIT ?"
        )
        params: list[Any] = [*schema_ids, json_path, limit]
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [
            ToolExampleRow(
                schema_id=int(row[0]),
                json_path=str(row[1]),
                value=str(row[2]),
                value_type=str(row[3]),
                timestamp_ms=int(row[4]),
            )
            for row in rows
        ]

    def list_examples_for_paths(
        self,
        schema_ids: list[int],
        json_paths: list[str],
        *,
        limit_per_path: int = 20,
    ) -> dict[str, list[ToolExampleRow]]:
        return {
            path: self.list_examples_for_path(schema_ids, path, limit=limit_per_path)
            for path in json_paths
        }

    def record_examples(
        self,
        schema_id: int,
        flattened_pairs: list[FlattenedExample],
    ) -> None:
        """Upsert flattened example rows for a capture (internal API)."""
        now_ms = int(time.time() * 1000)
        with self._lock:
            for item in flattened_pairs:
                self._conn.execute(
                    "INSERT INTO tool_example(schema_id, json_path, value, value_type, timestamp_ms) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(schema_id, json_path, value) DO UPDATE SET timestamp_ms = excluded.timestamp_ms",
                    (schema_id, item.json_path, item.value, item.value_type, now_ms),
                )
            self._conn.commit()

    def prune_stale_examples(self, *, cutoff_ms: int, min_per_path: int) -> None:
        with self._lock:
            paths = self._conn.execute(
                "SELECT DISTINCT schema_id, json_path FROM tool_example",
            ).fetchall()
            for schema_id, json_path in paths:
                count = self._conn.execute(
                    "SELECT COUNT(*) FROM tool_example WHERE schema_id = ? AND json_path = ?",
                    (schema_id, json_path),
                ).fetchone()[0]
                if int(count) <= min_per_path:
                    continue
                self._conn.execute(
                    "DELETE FROM tool_example WHERE schema_id = ? AND json_path = ? AND timestamp_ms < ?",
                    (schema_id, json_path, cutoff_ms),
                )
            self._conn.commit()

    def enforce_example_path_limit(self, *, max_per_path: int, min_per_path: int) -> None:
        """Keep at most max_per_path distinct values per (schema_id, json_path)."""
        with self._lock:
            paths = self._conn.execute(
                "SELECT DISTINCT schema_id, json_path FROM tool_example",
            ).fetchall()
            for schema_id, json_path in paths:
                count = self._conn.execute(
                    "SELECT COUNT(*) FROM tool_example WHERE schema_id = ? AND json_path = ?",
                    (schema_id, json_path),
                ).fetchone()[0]
                total = int(count)
                if total <= max_per_path:
                    continue
                to_delete = total - max(max_per_path, min_per_path)
                rows = self._conn.execute(
                    "SELECT id FROM tool_example WHERE schema_id = ? AND json_path = ? "
                    "ORDER BY timestamp_ms ASC LIMIT ?",
                    (schema_id, json_path, to_delete),
                ).fetchall()
                for (row_id,) in rows:
                    self._conn.execute("DELETE FROM tool_example WHERE id = ?", (row_id,))
            self._conn.commit()

    def enforce_capture_retention(self, *, max_captures: int, min_captures: int) -> None:
        with self._lock:
            groups = self._conn.execute(
                "SELECT project_id, mcp_server, tool_name, COUNT(*) "
                "FROM tool_input_schema GROUP BY project_id, mcp_server, tool_name",
            ).fetchall()
            for project_id, mcp_server, tool_name, count in groups:
                total = int(count)
                if total <= max_captures:
                    continue
                to_delete = total - max(max_captures, min_captures)
                rows = self._conn.execute(
                    "SELECT schema_id FROM tool_input_schema "
                    "WHERE project_id = ? AND mcp_server = ? AND tool_name = ? "
                    "ORDER BY last_seen_ms ASC LIMIT ?",
                    (project_id, mcp_server, tool_name, to_delete),
                ).fetchall()
                for (schema_id,) in rows:
                    self._conn.execute(
                        "DELETE FROM tool_input_schema WHERE schema_id = ?",
                        (schema_id,),
                    )
            self._conn.commit()

    def cleanup_orphan_example_paths(self) -> None:
        from cyt.tool_examples.maintenance import valid_paths_for_capture

        with self._lock:
            captures = self._conn.execute(
                "SELECT schema_id, schema_json, input_json FROM tool_input_schema",
            ).fetchall()
            for schema_id, schema_text, input_text in captures:
                try:
                    schema = json.loads(schema_text)
                    args = json.loads(input_text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(schema, dict) or not isinstance(args, dict):
                    continue
                valid = valid_paths_for_capture(schema, args)
                rows = self._conn.execute(
                    "SELECT id, json_path FROM tool_example WHERE schema_id = ?",
                    (schema_id,),
                ).fetchall()
                for row_id, json_path in rows:
                    if str(json_path) not in valid:
                        self._conn.execute(
                            "DELETE FROM tool_example WHERE id = ?",
                            (row_id,),
                        )
            self._conn.commit()

    @staticmethod
    def _row_to_capture(row: tuple[Any, ...]) -> ToolCapture:
        schema_json = json.loads(str(row[4]))
        input_json = json.loads(str(row[5]))
        return ToolCapture(
            schema_id=int(row[0]),
            project_id=int(row[1]),
            mcp_server=str(row[2]),
            tool_name=str(row[3]),
            schema_json=schema_json if isinstance(schema_json, dict) else {},
            input_json=input_json if isinstance(input_json, dict) else {},
            schema_hash=str(row[6]),
            input_hash=str(row[7]),
            first_seen_ms=int(row[8]),
            last_seen_ms=int(row[9]),
        )
