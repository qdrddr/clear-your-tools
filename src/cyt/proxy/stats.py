"""Embedded libSQL stats persistence for the LLM proxy."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import libsql_experimental as libsql
from uuid_extensions import uuid7str

from cyt.common.token_usage import PRUNING_STAT_STAGES, TIKTOKEN_CL100K, StageTokenUsage

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
    tools_final_json TEXT
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


def expand_db_path(path: str) -> str:
    return str(Path(path).expanduser())


def new_uuid7() -> str:
    """Generate a UUID7 string (time-ordered)."""
    return cast(str, uuid7str())


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
            provider = entry.get("provider")
            domain_match = entry.get("domain_match")
            dns = domain_match[0] if isinstance(domain_match, list) and domain_match else None
            return (
                str(provider) if provider else None,
                str(dns) if dns else None,
            )
    return None, None


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
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    @classmethod
    def init(cls, path: str) -> StatsDB:
        db_path = expand_db_path(path)
        parent = Path(db_path).parent
        parent.mkdir(parents=True, exist_ok=True)
        conn = libsql.connect(db_path)
        conn.executescript(_SCHEMA)
        conn.commit()
        logger.info("stats database initialized: %s", db_path)
        return cls(conn)

    @classmethod
    def open(cls, path: str) -> StatsDB:
        """Open existing DB or initialize a new one if the file is missing."""
        db_path = expand_db_path(path)
        if not Path(db_path).exists():
            return cls.init(path)
        conn = libsql.connect(db_path)
        return cls(conn)

    @classmethod
    def open_for_query(cls, path: str) -> StatsDB | None:
        """Open DB for read-only queries; return None when the file does not exist."""
        db_path = expand_db_path(path)
        if not Path(db_path).exists():
            return None
        conn = libsql.connect(db_path)
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
        self._conn.execute(
            """
            INSERT INTO proxy_request (
                id, endpoint, tools_in, tool_count_in, tool_properties_count_in,
                tools_out, tool_count_out, tool_properties_count_out,
                tools_pruned, tool_count_pruned, tool_properties_count_pruned,
                ts_ms, prune_status, pipeline, query, error,
                tools_accepted_json, tools_final_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            AND stage IN ('llm', 'rerank', 'bm25', 'upstream')
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
            WHERE {period_clause}m.stage IN ('llm', 'rerank', 'bm25')
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
        }

    def query_summary(self, period: str = "all") -> dict[str, int]:
        return self.query_totals(period)

    def query_events(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT id, endpoint, tools_in, tools_out, tools_pruned,
                   tool_count_in, tool_count_out, tool_count_pruned,
                   tool_properties_count_in, tool_properties_count_out,
                   tool_properties_count_pruned, ts_ms, prune_status, pipeline, error
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
    }


def format_totals(totals: dict[str, int], costs: Any | None = None) -> str:
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

        net_savings_tokens, net_savings_pct = compute_net_savings_tokens(
            tools_saved,
            tools_accepted,
            costs,
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
                "net savings (input tokens):",
                f"  cost:         {format_usd(costs.net_savings_usd)}",
                f"  tokens:     {net_savings_tokens} ({net_savings_pct:.1f}%)",
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
