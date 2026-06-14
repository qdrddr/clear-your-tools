"""Embedded SQLite stats persistence for the LLM proxy."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from uuid_extensions import uuid7str

from cyt.common.pricing import StatsCosts
from cyt.common.token_usage import PRUNING_STAT_STAGES, TIKTOKEN_CL100K, StageTokenUsage
from cyt.config import (
    merge_model_entry,
    provider_name_from_nick,
    provider_nick_for_dns,
    stats_provider_for_entry,
)

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS proxy_request (
    id TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL,
    tools_in INTEGER NOT NULL,
    tool_count_in INTEGER NOT NULL,
    tool_properties_count_in INTEGER NOT NULL,
    tools_out INTEGER NOT NULL,
    tool_count_out INTEGER NOT NULL,
    tool_properties_count_out INTEGER NOT NULL,
    tools_pruned INTEGER NOT NULL,
    tool_count_pruned INTEGER NOT NULL,
    tool_properties_count_pruned INTEGER NOT NULL,
    ts_ms INTEGER NOT NULL,
    prune_status TEXT NOT NULL,
    pipeline TEXT NOT NULL,
    query TEXT,
    error TEXT,
    tools_accepted_json TEXT,
    tools_final_json TEXT,
    skills_in INTEGER NOT NULL DEFAULT 0,
    has_error INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS model_request (
    id TEXT PRIMARY KEY,
    proxy_request_id TEXT NOT NULL REFERENCES proxy_request(id),
    model_name TEXT,
    provider_dns_name TEXT,
    provider TEXT,
    is_upstream INTEGER NOT NULL,
    stage TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
    id TEXT PRIMARY KEY,
    model_request_id TEXT NOT NULL REFERENCES model_request(id),
    type TEXT NOT NULL,
    tokens INTEGER NOT NULL,
    is_saved INTEGER NOT NULL,
    tokenizer_used TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_model_request_proxy ON model_request(proxy_request_id);
CREATE INDEX IF NOT EXISTS idx_tokens_model_request ON tokens(model_request_id);
CREATE INDEX IF NOT EXISTS idx_proxy_request_ts ON proxy_request(ts_ms);
"""

EXECUTED_STAGE_FINGERPRINT_STAGES = frozenset({"llm", "rerank", "bm25", "skills"})
MAX_STATS_BACKUPS = 10


def expand_db_path(path: str) -> str:
    return str(Path(path).expanduser())


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    _configure_connection(conn)
    return conn


def _configure_connection(conn: sqlite3.Connection) -> None:
    """Use TRUNCATE journaling so rollback journals do not linger on disk."""
    conn.execute("PRAGMA journal_mode=TRUNCATE")


def _ensure_proxy_request_columns(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(proxy_request)").fetchall()
    columns = {str(row[1]) for row in rows}
    changed = False
    if "skills_in" not in columns:
        conn.execute(
            "ALTER TABLE proxy_request ADD COLUMN skills_in INTEGER NOT NULL DEFAULT 0",
        )
        changed = True
    if "has_error" not in columns:
        conn.execute(
            "ALTER TABLE proxy_request ADD COLUMN has_error INTEGER NOT NULL DEFAULT 0",
        )
        conn.execute("UPDATE proxy_request SET has_error = 1 WHERE error IS NOT NULL")
        changed = True
    if changed:
        conn.commit()


def executed_stages_fingerprint(conn: sqlite3.Connection, proxy_id: str) -> str:
    """Sorted comma-joined pruning stages present on a proxy (llm/rerank/bm25/skills)."""
    placeholders = ",".join("?" for _ in EXECUTED_STAGE_FINGERPRINT_STAGES)
    rows = conn.execute(
        f"""
        SELECT DISTINCT stage FROM model_request
        WHERE proxy_request_id = ?
        AND stage IN ({placeholders})
        ORDER BY stage
        """,
        (proxy_id, *sorted(EXECUTED_STAGE_FINGERPRINT_STAGES)),
    ).fetchall()
    return ",".join(str(row[0]) for row in rows)


def _local_day_start_ms(day: date) -> int:
    return int(datetime.combine(day, datetime.min.time()).timestamp() * 1000)


def _local_day_from_ts_ms(ts_ms: int) -> date:
    return datetime.fromtimestamp(ts_ms / 1000).date()


def new_uuid7() -> str:
    """Generate a UUID7 string (time-ordered)."""
    return str(uuid7str())


def provider_dns_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    return parsed.hostname


def lookup_model_provider(
    model_name: str | None,
    config: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    """Return (provider, provider_dns_name) for a model name from config.yaml."""
    if not model_name or not config:
        return None, None
    models = config.get("models", {})
    llm_models = models.get("llm", {}).get("remote", [])
    if not isinstance(llm_models, list):
        return None, None
    for entry in llm_models:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") == model_name:
            enriched = merge_model_entry(config, entry)
            provider = stats_provider_for_entry(config, enriched)
            domain_match = enriched.get("domain_match")
            dns = domain_match[0] if isinstance(domain_match, list) and domain_match else None
            return (
                provider,
                str(dns) if dns else None,
            )
    return None, None


def lookup_provider_from_dns(
    provider_dns_name: str | None,
    config: dict[str, Any] | None,
) -> str | None:
    """Resolve provider name for stats from DNS via the provider registry."""
    if not provider_dns_name or not config:
        return None
    provider_nick = provider_nick_for_dns(config, provider_dns_name)
    return provider_name_from_nick(config, provider_nick)


@dataclass
class TokenRecord:
    type: str
    tokens: int
    is_saved: bool = False
    tokenizer_used: str = TIKTOKEN_CL100K


@dataclass
class ModelRequestRecord:
    stage: str
    model_name: str | None = None
    provider_dns_name: str | None = None
    provider: str | None = None
    is_upstream: bool = False
    token_rows: list[TokenRecord] = field(default_factory=list)


@dataclass
class ProxyRequestRecord:
    endpoint: str
    tools_in: int
    tool_count_in: int
    tool_properties_count_in: int
    tools_out: int
    tool_count_out: int
    tool_properties_count_out: int
    prune_status: str
    pipeline: list[str]
    upstream_model_name: str | None = None
    upstream_provider_dns: str | None = None
    upstream_provider: str | None = None
    query: str | None = None
    error: str | None = None
    tools_accepted_json: str | None = None
    tools_final_json: str | None = None
    pruning_stages: dict[str, StageTokenUsage] = field(default_factory=dict)

    @property
    def tools_pruned(self) -> int:
        return max(0, self.tools_in - self.tools_out)

    @property
    def tool_count_pruned(self) -> int:
        return max(0, self.tool_count_in - self.tool_count_out)

    @property
    def tool_properties_count_pruned(self) -> int:
        return max(0, self.tool_properties_count_in - self.tool_properties_count_out)


class StatsDB:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    def init(cls, path: str) -> StatsDB:
        db_path = expand_db_path(path)
        parent = Path(db_path).parent
        parent.mkdir(parents=True, exist_ok=True)
        conn = _connect(db_path)
        conn.executescript(_SCHEMA)
        _ensure_proxy_request_columns(conn)
        conn.commit()
        logger.info("stats database initialized: %s", db_path)
        return cls(conn)

    @classmethod
    def open(cls, path: str) -> StatsDB:
        """Open existing DB or initialize a new one if the file is missing."""
        db_path = expand_db_path(path)
        if not Path(db_path).exists():
            return cls.init(path)
        conn = _connect(db_path)
        _ensure_proxy_request_columns(conn)
        return cls(conn)

    @classmethod
    def open_for_query(cls, path: str) -> StatsDB | None:
        """Open DB for read-only queries; return None when the file does not exist."""
        db_path = expand_db_path(path)
        if not Path(db_path).exists():
            return None
        conn = _connect(db_path)
        _ensure_proxy_request_columns(conn)
        return cls(conn)

    def close(self) -> None:
        self._conn.close()

    def _insert_model_request(
        self,
        proxy_id: str,
        record: ModelRequestRecord,
    ) -> str:
        model_id = new_uuid7()
        self._conn.execute(
            """
            INSERT INTO model_request
                (id, proxy_request_id, model_name, provider_dns_name, provider, is_upstream, stage)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model_id,
                proxy_id,
                record.model_name,
                record.provider_dns_name,
                record.provider,
                1 if record.is_upstream else 0,
                record.stage,
            ),
        )
        for row in record.token_rows:
            if row.tokens <= 0:
                continue
            self._conn.execute(
                """
                INSERT INTO tokens (id, model_request_id, type, tokens, is_saved, tokenizer_used)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    new_uuid7(),
                    model_id,
                    row.type,
                    row.tokens,
                    1 if row.is_saved else 0,
                    row.tokenizer_used,
                ),
            )
        return model_id

    def record_proxy_request(self, record: ProxyRequestRecord) -> str:
        proxy_id = new_uuid7()
        ts_ms = int(time.time() * 1000)
        has_error = 1 if record.error else 0
        self._conn.execute(
            """
            INSERT INTO proxy_request (
                id, endpoint, tools_in, tool_count_in, tool_properties_count_in,
                tools_out, tool_count_out, tool_properties_count_out,
                tools_pruned, tool_count_pruned, tool_properties_count_pruned,
                ts_ms, prune_status, pipeline, query, error,
                tools_accepted_json, tools_final_json, has_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proxy_id,
                record.endpoint,
                record.tools_in,
                record.tool_count_in,
                record.tool_properties_count_in,
                record.tools_out,
                record.tool_count_out,
                record.tool_properties_count_out,
                record.tools_pruned,
                record.tool_count_pruned,
                record.tool_properties_count_pruned,
                ts_ms,
                record.prune_status,
                json.dumps(record.pipeline),
                record.query,
                record.error,
                record.tools_accepted_json,
                record.tools_final_json,
                has_error,
            ),
        )

        upstream_id = self._insert_model_request(
            proxy_id,
            ModelRequestRecord(
                stage="upstream",
                model_name=record.upstream_model_name,
                provider_dns_name=record.upstream_provider_dns,
                provider=record.upstream_provider,
                is_upstream=True,
                token_rows=[
                    TokenRecord(type="input", tokens=record.tools_pruned, is_saved=True),
                ],
            ),
        )

        self._insert_model_request(
            proxy_id,
            ModelRequestRecord(
                stage="tools",
                is_upstream=False,
                token_rows=[
                    TokenRecord(type="input", tokens=record.tools_in, is_saved=False),
                    TokenRecord(type="output", tokens=record.tools_out, is_saved=False),
                ],
            ),
        )

        for stage in PRUNING_STAT_STAGES:
            usage = record.pruning_stages.get(stage)
            if usage is None:
                continue
            if usage.input_tokens == 0 and usage.output_tokens == 0:
                if stage != "bm25" or not usage.model_name:
                    continue
            token_rows: list[TokenRecord] = []
            if usage.input_tokens > 0:
                token_rows.append(
                    TokenRecord(
                        type="input",
                        tokens=usage.input_tokens,
                        tokenizer_used=usage.usage_source,
                    ),
                )
            if usage.output_tokens > 0:
                token_rows.append(
                    TokenRecord(
                        type="output",
                        tokens=usage.output_tokens,
                        tokenizer_used=usage.usage_source,
                    ),
                )
            if usage.reasoning_tokens and usage.reasoning_tokens > 0:
                token_rows.append(
                    TokenRecord(
                        type="reasoning",
                        tokens=usage.reasoning_tokens,
                        tokenizer_used=usage.usage_source,
                    ),
                )
            self._insert_model_request(
                proxy_id,
                ModelRequestRecord(
                    stage=stage,
                    model_name=usage.model_name,
                    provider_dns_name=usage.provider_dns_name,
                    provider=usage.provider,
                    is_upstream=False,
                    token_rows=token_rows,
                ),
            )

        self._conn.commit()
        logger.debug("recorded proxy_request id=%s upstream_id=%s", proxy_id, upstream_id)
        return proxy_id

    def record_skills_injection(
        self,
        *,
        query: str,
        model_name: str,
        skills_in: int,
        config: dict[str, Any] | None = None,
    ) -> str:
        provider, provider_dns = lookup_model_provider(model_name, config)
        proxy_id = new_uuid7()
        ts_ms = int(time.time() * 1000)
        self._conn.execute(
            """
            INSERT INTO proxy_request (
                id, endpoint, tools_in, tool_count_in, tool_properties_count_in,
                tools_out, tool_count_out, tool_properties_count_out,
                tools_pruned, tool_count_pruned, tool_properties_count_pruned,
                ts_ms, prune_status, pipeline, query, error,
                tools_accepted_json, tools_final_json, skills_in, has_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proxy_id,
                "skills",
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                ts_ms,
                "applied",
                json.dumps(["bm25"]),
                query,
                None,
                None,
                None,
                skills_in,
                0,
            ),
        )
        self._insert_model_request(
            proxy_id,
            ModelRequestRecord(
                stage="skills",
                model_name=model_name,
                provider_dns_name=provider_dns,
                provider=provider,
                is_upstream=True,
                token_rows=[
                    TokenRecord(type="input", tokens=skills_in, is_saved=False),
                ],
            ),
        )
        self._conn.commit()
        return proxy_id

    def query_skills_injection_tokens(
        self,
        period: str = "all",
    ) -> list[tuple[str | None, str | None, int]]:
        """Return (model_name, provider_dns_name, skills_in) for skills hook events."""
        cutoff = self._period_cutoff_ms(period)
        period_clause = "p.ts_ms >= ? AND " if cutoff is not None else ""
        params: tuple[Any, ...] = (cutoff,) if cutoff is not None else ()
        rows = self._conn.execute(
            f"""
            SELECT m.model_name, m.provider_dns_name, COALESCE(SUM(p.skills_in), 0)
            FROM proxy_request p
            JOIN model_request m ON m.proxy_request_id = p.id
            WHERE {period_clause}p.endpoint = 'skills'
            AND m.stage = 'skills'
            GROUP BY m.model_name, m.provider_dns_name
            """,
            params,
        ).fetchall()
        return [(row[0], row[1], int(row[2] or 0)) for row in rows]

    def _period_cutoff_ms(self, period: str) -> int | None:
        now = int(time.time() * 1000)
        windows = {
            "day": 86_400_000,
            "week": 604_800_000,
            "month": 2_592_000_000,
        }
        delta = windows.get(period)
        if delta is None:
            return None
        return now - delta

    def query_distinct_model_identities(
        self,
    ) -> list[tuple[str, str, str | None, str | None]]:
        """Return distinct (stage, model_name, provider_dns_name, provider) rows."""
        rows = self._conn.execute(
            """
            SELECT DISTINCT stage, model_name, provider_dns_name, provider
            FROM model_request
            WHERE model_name IS NOT NULL
            AND stage IN ('llm', 'rerank', 'bm25', 'skills', 'upstream')
            ORDER BY stage, model_name, provider_dns_name
            """,
        ).fetchall()
        return [
            (
                str(row[0]),
                str(row[1]),
                row[2] if row[2] is None else str(row[2]),
                row[3] if row[3] is None else str(row[3]),
            )
            for row in rows
        ]

    def query_stage_model_tokens(
        self,
        period: str = "all",
    ) -> list[tuple[str, str | None, str, int]]:
        """Return (stage, model_name, token_type, token_count) for pruning stages."""
        cutoff = self._period_cutoff_ms(period)
        period_clause = "p.ts_ms >= ? AND " if cutoff is not None else ""
        params: tuple[Any, ...] = (cutoff,) if cutoff is not None else ()
        rows = self._conn.execute(
            f"""
            SELECT m.stage, m.model_name, t.type, COALESCE(SUM(t.tokens), 0)
            FROM tokens t
            JOIN model_request m ON t.model_request_id = m.id
            JOIN proxy_request p ON m.proxy_request_id = p.id
            WHERE {period_clause}m.stage IN ('llm', 'rerank', 'bm25', 'skills')
            AND t.is_saved = 0
            GROUP BY m.stage, m.model_name, t.type
            """,
            params,
        ).fetchall()
        return [(str(row[0]), row[1], str(row[2]), int(row[3] or 0)) for row in rows]

    def query_upstream_saved_tokens(
        self,
        period: str = "all",
    ) -> list[tuple[str | None, str | None, int]]:
        """Return (model_name, provider_dns_name, token_count) for saved upstream tool tokens."""
        cutoff = self._period_cutoff_ms(period)
        period_clause = "p.ts_ms >= ? AND " if cutoff is not None else ""
        params: tuple[Any, ...] = (cutoff,) if cutoff is not None else ()
        rows = self._conn.execute(
            f"""
            SELECT m.model_name, m.provider_dns_name, COALESCE(SUM(t.tokens), 0)
            FROM tokens t
            JOIN model_request m ON t.model_request_id = m.id
            JOIN proxy_request p ON m.proxy_request_id = p.id
            WHERE {period_clause}m.stage = 'upstream'
            AND t.is_saved = 1
            GROUP BY m.model_name, m.provider_dns_name
            """,
            params,
        ).fetchall()
        return [(row[0], row[1], int(row[2] or 0)) for row in rows]

    def query_totals(self, period: str = "all") -> dict[str, int]:
        cutoff = self._period_cutoff_ms(period)
        where = "WHERE p.ts_ms >= ?" if cutoff is not None else ""
        params: tuple[Any, ...] = (cutoff,) if cutoff is not None else ()

        def sum_tokens(sql: str, extra_params: tuple[Any, ...] = ()) -> int:
            row = self._conn.execute(sql.format(where=where), params + extra_params).fetchone()
            return int(row[0] or 0) if row else 0

        return {
            "events": sum_tokens(
                "SELECT COUNT(*) FROM proxy_request p {where}",
            ),
            "tools_sent_upstream": sum_tokens(
                """
                SELECT COALESCE(SUM(t.tokens), 0)
                FROM tokens t
                JOIN model_request m ON t.model_request_id = m.id
                JOIN proxy_request p ON m.proxy_request_id = p.id
                {where}
                AND m.stage = 'tools' AND t.type = 'output' AND t.is_saved = 0
                """,
            ),
            "tools_saved": sum_tokens(
                """
                SELECT COALESCE(SUM(t.tokens), 0)
                FROM tokens t
                JOIN model_request m ON t.model_request_id = m.id
                JOIN proxy_request p ON m.proxy_request_id = p.id
                {where}
                AND t.is_saved = 1
                """,
            ),
            "tools_accepted": sum_tokens(
                """
                SELECT COALESCE(SUM(t.tokens), 0)
                FROM tokens t
                JOIN model_request m ON t.model_request_id = m.id
                JOIN proxy_request p ON m.proxy_request_id = p.id
                {where}
                AND m.stage = 'tools' AND t.type = 'input' AND t.is_saved = 0
                """,
            ),
            "llm_input": sum_tokens(
                """
                SELECT COALESCE(SUM(t.tokens), 0)
                FROM tokens t
                JOIN model_request m ON t.model_request_id = m.id
                JOIN proxy_request p ON m.proxy_request_id = p.id
                {where}
                AND m.stage = 'llm' AND t.type = 'input' AND t.is_saved = 0
                """,
            ),
            "llm_output": sum_tokens(
                """
                SELECT COALESCE(SUM(t.tokens), 0)
                FROM tokens t
                JOIN model_request m ON t.model_request_id = m.id
                JOIN proxy_request p ON m.proxy_request_id = p.id
                {where}
                AND m.stage = 'llm' AND t.type = 'output' AND t.is_saved = 0
                """,
            ),
            "rerank_input": sum_tokens(
                """
                SELECT COALESCE(SUM(t.tokens), 0)
                FROM tokens t
                JOIN model_request m ON t.model_request_id = m.id
                JOIN proxy_request p ON m.proxy_request_id = p.id
                {where}
                AND m.stage = 'rerank' AND t.type = 'input' AND t.is_saved = 0
                """,
            ),
            "rerank_output": sum_tokens(
                """
                SELECT COALESCE(SUM(t.tokens), 0)
                FROM tokens t
                JOIN model_request m ON t.model_request_id = m.id
                JOIN proxy_request p ON m.proxy_request_id = p.id
                {where}
                AND m.stage = 'rerank' AND t.type = 'output' AND t.is_saved = 0
                """,
            ),
            "skills_in": sum_tokens(
                """
                SELECT COALESCE(SUM(p.skills_in), 0)
                FROM proxy_request p
                """
                + (
                    f"{where} AND p.endpoint = 'skills'" if where else "WHERE p.endpoint = 'skills'"
                ),
            ),
        }

    def query_summary(self, period: str = "all") -> dict[str, int]:
        return self.query_totals(period)

    def query_events(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT id, endpoint, tools_in, tools_out, tools_pruned,
                   tool_count_in, tool_count_out, tool_count_pruned,
                   tool_properties_count_in, tool_properties_count_out,
                   tool_properties_count_pruned, ts_ms, prune_status, pipeline, error,
                   has_error
            FROM proxy_request
            ORDER BY ts_ms DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            proxy_id = row[0]
            stage_tokens = self._conn.execute(
                """
                SELECT m.stage, t.type, t.tokens, t.is_saved
                FROM tokens t
                JOIN model_request m ON t.model_request_id = m.id
                WHERE m.proxy_request_id = ?
                """,
                (proxy_id,),
            ).fetchall()
            events.append(
                {
                    "id": proxy_id,
                    "endpoint": row[1],
                    "tools_in": row[2],
                    "tools_out": row[3],
                    "tools_pruned": row[4],
                    "tool_count_in": row[5],
                    "tool_count_out": row[6],
                    "tool_count_pruned": row[7],
                    "tool_properties_count_in": row[8],
                    "tool_properties_count_out": row[9],
                    "tool_properties_count_pruned": row[10],
                    "ts_ms": row[11],
                    "prune_status": row[12],
                    "pipeline": json.loads(row[13]) if row[13] else [],
                    "error": row[14],
                    "has_error": bool(row[15]),
                    "tokens": [
                        {
                            "stage": st[0],
                            "type": st[1],
                            "tokens": st[2],
                            "is_saved": bool(st[3]),
                        }
                        for st in stage_tokens
                    ],
                },
            )
        return events

    @staticmethod
    def _is_backup_file(db_path: str, candidate: Path) -> bool:
        source = Path(expand_db_path(db_path))
        prefix = f"{source.stem}_"
        suffix = source.suffix
        if not candidate.is_file() or candidate.parent != source.parent:
            return False
        name = candidate.name
        if not name.startswith(prefix):
            return False
        if suffix and not name.endswith(suffix):
            return False
        stamp = name[len(prefix) : -len(suffix) if suffix else len(name)]
        return len(stamp) == 15 and stamp[8] == "_" and stamp[:8].isdigit() and stamp[9:].isdigit()

    @staticmethod
    def list_backups(db_path: str) -> list[Path]:
        """Return timestamped backup files for a stats DB, oldest first."""
        source = Path(expand_db_path(db_path))
        prefix = f"{source.stem}_"
        suffix = source.suffix
        backups = [
            candidate
            for candidate in source.parent.glob(f"{prefix}*{suffix}")
            if StatsDB._is_backup_file(db_path, candidate)
        ]
        return sorted(backups, key=lambda path: path.name)

    @staticmethod
    def find_today_backup(db_path: str, *, today: date | None = None) -> str | None:
        """Return path to a stats backup created today, if any."""
        if today is None:
            today = datetime.now().date()
        day_prefix = today.strftime("%Y%m%d")
        for candidate in reversed(StatsDB.list_backups(db_path)):
            stamp = candidate.name.split("_", 2)
            if len(stamp) >= 2 and stamp[1] == day_prefix:
                return str(candidate)
        return None

    @staticmethod
    def backup_database(db_path: str) -> str:
        """Copy the stats DB to a timestamped sibling file; return backup path."""
        source = Path(expand_db_path(db_path))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = source.with_name(f"{source.stem}_{stamp}{source.suffix}")
        shutil.copy2(source, dest)
        return str(dest)

    @staticmethod
    def prune_old_backups(
        db_path: str,
        *,
        max_backups: int = MAX_STATS_BACKUPS,
    ) -> list[str]:
        """Delete oldest stats backups when more than max_backups exist."""
        if max_backups < 0:
            raise ValueError("max_backups must be non-negative")
        backups = StatsDB.list_backups(db_path)
        removed: list[str] = []
        while len(backups) > max_backups:
            oldest = backups.pop(0)
            oldest.unlink()
            removed_path = str(oldest)
            removed.append(removed_path)
            logger.info("removed old stats backup: %s", removed_path)
        return removed

    def _delete_proxy_tree(self, proxy_ids: list[str]) -> None:
        if not proxy_ids:
            return
        placeholders = ",".join("?" for _ in proxy_ids)
        model_rows = self._conn.execute(
            f"SELECT id FROM model_request WHERE proxy_request_id IN ({placeholders})",
            proxy_ids,
        ).fetchall()
        model_ids = [str(row[0]) for row in model_rows]
        if model_ids:
            model_placeholders = ",".join("?" for _ in model_ids)
            self._conn.execute(
                f"DELETE FROM tokens WHERE model_request_id IN ({model_placeholders})",
                model_ids,
            )
            self._conn.execute(
                f"DELETE FROM model_request WHERE id IN ({model_placeholders})",
                model_ids,
            )
        self._conn.execute(
            f"DELETE FROM proxy_request WHERE id IN ({placeholders})",
            proxy_ids,
        )

    def _merge_proxy_group(
        self,
        members: list[tuple[Any, ...]],
        day_start_ms: int,
    ) -> None:
        proxy_ids = [str(row[0]) for row in members]
        endpoint = str(members[0][1])
        prune_status = str(members[0][12])
        pipeline = str(members[0][13])
        has_error = int(members[0][14] or 0)
        skills_in = sum(int(row[15] or 0) for row in members)

        model_map: dict[
            tuple[Any, ...],
            defaultdict[tuple[str, int, str], int],
        ] = {}

        for proxy_id in proxy_ids:
            model_rows = self._conn.execute(
                """
                SELECT id, model_name, provider_dns_name, provider, is_upstream, stage
                FROM model_request
                WHERE proxy_request_id = ?
                """,
                (proxy_id,),
            ).fetchall()
            for model_row in model_rows:
                model_id = str(model_row[0])
                model_key = (
                    model_row[1],
                    model_row[2],
                    model_row[3],
                    int(model_row[4]),
                    str(model_row[5]),
                )
                token_totals = model_map.setdefault(model_key, defaultdict(int))
                token_rows = self._conn.execute(
                    """
                    SELECT type, tokens, is_saved, tokenizer_used
                    FROM tokens
                    WHERE model_request_id = ?
                    """,
                    (model_id,),
                ).fetchall()
                for token_row in token_rows:
                    token_key = (
                        str(token_row[0]),
                        int(token_row[2]),
                        str(token_row[3]),
                    )
                    token_totals[token_key] += int(token_row[1] or 0)

        new_proxy_id = new_uuid7()
        self._conn.execute(
            """
            INSERT INTO proxy_request (
                id, endpoint, tools_in, tool_count_in, tool_properties_count_in,
                tools_out, tool_count_out, tool_properties_count_out,
                tools_pruned, tool_count_pruned, tool_properties_count_pruned,
                ts_ms, prune_status, pipeline, query, error,
                tools_accepted_json, tools_final_json, skills_in, has_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_proxy_id,
                endpoint,
                sum(int(row[2] or 0) for row in members),
                sum(int(row[3] or 0) for row in members),
                sum(int(row[4] or 0) for row in members),
                sum(int(row[5] or 0) for row in members),
                sum(int(row[6] or 0) for row in members),
                sum(int(row[7] or 0) for row in members),
                sum(int(row[8] or 0) for row in members),
                sum(int(row[9] or 0) for row in members),
                sum(int(row[10] or 0) for row in members),
                day_start_ms,
                prune_status,
                pipeline,
                None,
                None,
                None,
                None,
                skills_in,
                has_error,
            ),
        )

        for model_key, token_totals in model_map.items():
            model_name, provider_dns_name, provider, is_upstream, stage = model_key
            model_id = new_uuid7()
            self._conn.execute(
                """
                INSERT INTO model_request
                    (id, proxy_request_id, model_name, provider_dns_name, provider, is_upstream, stage)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model_id,
                    new_proxy_id,
                    model_name,
                    provider_dns_name,
                    provider,
                    is_upstream,
                    stage,
                ),
            )
            for (token_type, is_saved, tokenizer_used), token_count in token_totals.items():
                if token_count <= 0:
                    continue
                self._conn.execute(
                    """
                    INSERT INTO tokens (id, model_request_id, type, tokens, is_saved, tokenizer_used)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_uuid7(),
                        model_id,
                        token_type,
                        token_count,
                        is_saved,
                        tokenizer_used,
                    ),
                )

        self._delete_proxy_tree(proxy_ids)

    def rollup_historical(self, *, today: date | None = None) -> RollupResult:
        """Merge pre-today proxy_request rows that share rollup keys into daily aggregates."""
        if today is None:
            today = datetime.now().date()
        today_start_ms = _local_day_start_ms(today)

        rows = self._conn.execute(
            """
            SELECT id, endpoint, tools_in, tool_count_in, tool_properties_count_in,
                   tools_out, tool_count_out, tool_properties_count_out,
                   tools_pruned, tool_count_pruned, tool_properties_count_pruned,
                   ts_ms, prune_status, pipeline, has_error, skills_in
            FROM proxy_request
            WHERE ts_ms < ?
            """,
            (today_start_ms,),
        ).fetchall()

        groups: dict[tuple[Any, ...], list[tuple[Any, ...]]] = defaultdict(list)
        days_seen: set[date] = set()
        for row in rows:
            proxy_id = str(row[0])
            day = _local_day_from_ts_ms(int(row[11]))
            days_seen.add(day)
            fingerprint = executed_stages_fingerprint(self._conn, proxy_id)
            group_key = (
                day,
                str(row[1]),
                str(row[12]),
                str(row[13]),
                int(row[14] or 0),
                fingerprint,
            )
            groups[group_key].append(row)

        groups_merged = 0
        rows_removed = 0
        try:
            for group_key, members in groups.items():
                if len(members) <= 1:
                    continue
                day_start_ms = _local_day_start_ms(group_key[0])
                self._merge_proxy_group(members, day_start_ms)
                groups_merged += 1
                rows_removed += len(members) - 1
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        return RollupResult(
            days_processed=len(days_seen),
            groups_merged=groups_merged,
            rows_removed=rows_removed,
        )

    def vacuum(self) -> None:
        """Rebuild the database file and reclaim space freed by deletes."""
        self._conn.execute("VACUUM")


@dataclass
class RollupResult:
    days_processed: int
    groups_merged: int
    rows_removed: int
    backup_path: str | None = None


def empty_totals() -> dict[str, int]:
    return {
        "events": 0,
        "tools_sent_upstream": 0,
        "tools_saved": 0,
        "tools_accepted": 0,
        "llm_input": 0,
        "llm_output": 0,
        "rerank_input": 0,
        "rerank_output": 0,
        "skills_in": 0,
    }


def _terminal_color_enabled(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _green(text: str, *, enabled: bool) -> str:
    if not enabled:
        return text
    return f"\033[32m{text}\033[0m"


def format_totals(
    totals: dict[str, int],
    costs: StatsCosts | None = None,
    *,
    color: bool | None = None,
) -> str:
    tools_accepted = totals.get("tools_accepted", 0)
    tools_saved = totals.get("tools_saved", 0)
    tools_saved_pct = (100.0 * tools_saved / tools_accepted) if tools_accepted else 0.0
    lines = [
        f"events:              {totals.get('events', 0)}",
        "",
        "upstream tool tokens (sent after pruning):",
        f"  tools accepted:      {tools_accepted}",
        f"  tools sent:          {totals.get('tools_sent_upstream', 0)}",
        f"  tools saved:         {tools_saved}  ({tools_saved_pct:.1f}%)",
    ]
    if costs is not None:
        from cyt.common.pricing import compute_net_savings_tokens, format_usd

        skills_in = totals.get("skills_in", 0)
        net_savings_tokens, net_savings_pct = compute_net_savings_tokens(
            tools_saved,
            tools_accepted,
            costs,
            skills_in=skills_in,
        )
        use_color = _terminal_color_enabled(color)
        savings_tokens = _green(
            f"{net_savings_tokens} ({net_savings_pct:.1f}%)",
            enabled=use_color,
        )
        lines.extend(
            [
                "",
                "tool savings (upstream input rate):",
                f"  {format_usd(costs.tools_saved_usd)}",
                "",
                "pruning cost:",
                f"  llm input:           {totals.get('llm_input', 0)}  ({format_usd(costs.llm_input_usd)})",
                f"  llm output:          {totals.get('llm_output', 0)}  ({format_usd(costs.llm_output_usd)})",
                f"  rerank input:        {totals.get('rerank_input', 0)}  ({format_usd(costs.rerank_input_usd)})",
                f"  rerank output:       {totals.get('rerank_output', 0)}  ({format_usd(costs.rerank_output_usd)})",
                f"  total pruning:       {format_usd(costs.pruning_total_usd)}",
                "",
                "skills context added:",
                f"  tokens:              {skills_in}  ({format_usd(costs.skills_input_usd)})",
                "",
                "net savings (input tokens):",
                f"  cost:         {format_usd(costs.net_savings_usd)}",
                f"  tokens:     {savings_tokens}",
            ],
        )
    else:
        lines.extend(
            [
                "",
                "extra pruning cost (tokens):",
                f"  llm input:           {totals.get('llm_input', 0)}",
                f"  llm output:          {totals.get('llm_output', 0)}",
                f"  rerank input:        {totals.get('rerank_input', 0)}",
                f"  rerank output:       {totals.get('rerank_output', 0)}",
            ],
        )
    return "\n".join(lines)


def format_events(events: list[dict[str, Any]]) -> str:
    if not events:
        return "(no events)"
    lines: list[str] = []
    for ev in events:
        lines.append(
            f"{ev['id']}  {ev['prune_status']}  "
            f"tools {ev['tool_count_in']}→{ev['tool_count_out']}  "
            f"tokens {ev['tools_in']}→{ev['tools_out']} (saved {ev['tools_pruned']})  "
            f"props {ev['tool_properties_count_in']}→{ev['tool_properties_count_out']}",
        )
    return "\n".join(lines)
