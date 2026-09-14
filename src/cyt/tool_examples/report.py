"""Build and format RRF-scored tool example injection reports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from cyt.tool_examples.config import tool_examples_config
from cyt.tool_examples.enrich import (
    _collect_property_nodes,
    _property_name_from_path,
    _schema_from_tool,
)
from cyt.tool_examples.hash_utils import content_hash
from cyt.tool_examples.identity import resolve_mcp_server_and_tool
from cyt.tool_examples.ranking import RankedExampleValue, rank_example_values
from cyt.tool_examples.store import ToolExamplesStore


@dataclass(frozen=True)
class ScoredExampleLine:
    value: str
    display_value: str
    bm25_score: float
    recency_bonus: float
    usage_score: float
    rrf_score: float
    total_score: float
    channel_ranks: dict[str, int]


@dataclass(frozen=True)
class PropertyScoreReport:
    full_path: str
    json_path: str
    examples: tuple[ScoredExampleLine, ...]


@dataclass(frozen=True)
class ToolScoreReport:
    mcp_server: str
    tool_name: str
    wire_name: str
    properties: tuple[PropertyScoreReport, ...]


@dataclass(frozen=True)
class QueryScoreReport:
    query_id: str
    query: str
    inject: dict[str, Any]
    tools: tuple[ToolScoreReport, ...]


def _display_value(raw: str) -> str:
    text = raw
    if len(text) > 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        return text[1:-1]
    return text


def _flattened_key(server_key: str, tool_name: str, json_path: str) -> str:
    return f"{server_key}.{tool_name}.{json_path}"


def _rows_to_scored_lines(
    ranked: list[RankedExampleValue],
) -> tuple[ScoredExampleLine, ...]:
    out: list[ScoredExampleLine] = []
    for row in ranked:
        out.append(
            ScoredExampleLine(
                value=row.value,
                display_value=_display_value(row.value),
                bm25_score=row.bm25_score,
                recency_bonus=row.recency_bonus,
                usage_score=row.usage_score,
                rrf_score=row.rrf_score,
                total_score=row.rrf_score,
                channel_ranks=dict(row.channel_ranks),
            ),
        )
    return tuple(out)


def build_query_score_report(
    *,
    query_id: str,
    query: str,
    tools: list[dict[str, Any]],
    config: dict[str, Any],
    store: ToolExamplesStore,
    project_id: int,
) -> QueryScoreReport:
    cfg = tool_examples_config(config)
    inject = {
        "max_per_property": cfg.max_per_property,
        "max_value_chars": cfg.max_value_chars,
        "full_call_examples": cfg.full_call_examples,
        "cross_schema_fallback": cfg.cross_schema_fallback,
        "ranking": {
            "pipeline": cfg.ranking.pipeline,
            "rrf_k": cfg.ranking.rrf_k,
            "diversity_threshold": cfg.ranking.diversity_threshold,
        },
    }
    tool_reports: list[ToolScoreReport] = []
    for tool in tools:
        schema = _schema_from_tool(tool)
        if not schema:
            continue
        mcp_server, bare_tool = resolve_mcp_server_and_tool(tool)
        wire_name = str(tool.get("name") or f"{mcp_server}_{bare_tool}")
        schema_hash = content_hash(schema)
        captures = store.list_captures(
            project_id,
            mcp_server,
            bare_tool,
            schema_hash=schema_hash if not cfg.cross_schema_fallback else None,
            limit=cfg.max_captures_per_tool,
        )
        if not captures and cfg.cross_schema_fallback:
            captures = store.list_captures(
                project_id,
                mcp_server,
                bare_tool,
                limit=cfg.max_captures_per_tool,
            )
        if not captures:
            tool_reports.append(
                ToolScoreReport(
                    mcp_server=mcp_server,
                    tool_name=bare_tool,
                    wire_name=wire_name,
                    properties=(),
                ),
            )
            continue
        schema_ids = [capture.schema_id for capture in captures]
        property_reports: list[PropertyScoreReport] = []
        for json_path, spec in _collect_property_nodes(schema):
            rows = store.list_aggregated_examples_for_path(
                schema_ids,
                json_path,
                limit=cfg.max_per_path,
            )
            if not rows:
                continue
            property_name = _property_name_from_path(json_path)
            property_description = str(spec.get("description") or "")
            ranked = rank_example_values(
                query,
                rows,
                max_count=cfg.max_per_property,
                config=config,
                ranking=cfg.ranking,
                property_name=property_name,
                property_description=property_description,
            )
            examples = _rows_to_scored_lines(ranked)
            if not examples:
                continue
            property_reports.append(
                PropertyScoreReport(
                    full_path=_flattened_key(mcp_server, bare_tool, json_path),
                    json_path=json_path,
                    examples=examples,
                ),
            )
        tool_reports.append(
            ToolScoreReport(
                mcp_server=mcp_server,
                tool_name=bare_tool,
                wire_name=wire_name,
                properties=tuple(property_reports),
            ),
        )
    return QueryScoreReport(
        query_id=query_id,
        query=query,
        inject=inject,
        tools=tuple(tool_reports),
    )


def format_query_score_report(report: QueryScoreReport) -> str:
    lines: list[str] = [
        f"Query ID: {report.query_id}",
        f"Query: {report.query}",
        (
            "Inject: "
            f"max_per_property={report.inject.get('max_per_property')} "
            f"max_value_chars={report.inject.get('max_value_chars')}"
        ),
        "",
    ]
    if not report.tools:
        lines.append("(no tools)")
        return "\n".join(lines)
    for tool in report.tools:
        lines.append(f"MCP server: {tool.mcp_server}")
        lines.append(f"Tool name: {tool.tool_name}")
        lines.append(f"Wire name: {tool.wire_name}")
        if not tool.properties:
            lines.append("  (no matching example paths)")
            lines.append("")
            continue
        for prop in tool.properties:
            lines.append(f"  Path: {prop.full_path}")
            for example in prop.examples:
                lines.append(
                    "    "
                    f"rrf={example.rrf_score:.4f} "
                    f"bm25={example.bm25_score:.4f} "
                    f"usage={example.usage_score:.4f} "
                    f"recency={example.recency_bonus:.4f} "
                    f"value={example.display_value!r}",
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_query_score_report_json(report: QueryScoreReport) -> str:
    payload = {
        "query_id": report.query_id,
        "query": report.query,
        "inject": report.inject,
        "tools": [
            {
                "mcp_server": tool.mcp_server,
                "tool_name": tool.tool_name,
                "wire_name": tool.wire_name,
                "properties": [
                    {
                        "path": prop.full_path,
                        "json_path": prop.json_path,
                        "examples": [
                            {
                                "value": ex.display_value,
                                "raw_value": ex.value,
                                "bm25_score": ex.bm25_score,
                                "recency_bonus": ex.recency_bonus,
                                "usage_score": ex.usage_score,
                                "rrf_score": ex.rrf_score,
                                "total_score": ex.total_score,
                                "channel_ranks": ex.channel_ranks,
                            }
                            for ex in prop.examples
                        ],
                    }
                    for prop in tool.properties
                ],
            }
            for tool in report.tools
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
