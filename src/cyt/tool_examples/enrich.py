"""Append query-relevant examples to pruned tool property descriptions."""

from __future__ import annotations

import copy
import json
from typing import Any

from cyt.hook.workspace_config import hook_workspace_from_config
from cyt.tiers.config import resolve_project_root_path
from cyt.tool_examples.config import ToolExamplesConfig, examples_active, tool_examples_config
from cyt.tool_examples.hash_utils import content_hash
from cyt.tool_examples.identity import resolve_mcp_server_and_tool
from cyt.tool_examples.query import rank_by_query
from cyt.tool_examples.store import ToolCapture, ToolExampleRow, ToolExamplesStore


def _schema_from_tool(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("input_schema") or tool.get("inputSchema") or tool.get("parameters")
    return schema if isinstance(schema, dict) else {}


def _collect_property_nodes(
    schema: dict[str, Any],
    prefix: str = "inputSchema.properties",
) -> list[tuple[str, dict[str, Any]]]:
    nodes: list[tuple[str, dict[str, Any]]] = []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return nodes
    for key, spec in properties.items():
        if not isinstance(spec, dict):
            continue
        path = f"{prefix}.{key}"
        nodes.append((path, spec))
        if spec.get("type") == "object":
            nodes.extend(_collect_property_nodes(spec, path))
        elif spec.get("type") == "array":
            items = spec.get("items")
            if isinstance(items, dict) and items.get("type") == "object":
                nodes.extend(_collect_property_nodes(items, f"{path}.items[].properties"))
    return nodes


def _append_examples(description: str, values: list[str], *, max_value_chars: int) -> str:
    if not values:
        return description
    clipped = []
    for value in values:
        text = value
        if len(text) > 2 and text[0] == text[-1] and text[0] in ('"', "'"):
            text = text[1:-1]
        if len(text) > max_value_chars:
            text = text[: max_value_chars - 3] + "..."
        clipped.append(f"'{text}'")
    suffix = f" Examples: {', '.join(clipped)}."
    base = (description or "").rstrip()
    if not base:
        return suffix.lstrip()
    if base.endswith("."):
        return f"{base}{suffix}"
    return f"{base}.{suffix}"


def _rank_example_values(
    query: str,
    rows: list[ToolExampleRow],
    *,
    max_count: int,
) -> list[str]:
    if not rows:
        return []
    items = [(row.value, row.value, row.timestamp_ms) for row in rows]
    ranked = rank_by_query(query, items)
    seen: set[str] = set()
    out: list[str] = []
    for item in ranked:
        value = str(item.item)
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
        if len(out) >= max_count:
            break
    return out


def enrich_tools_with_examples(
    tools: list[dict[str, Any]],
    query: str,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    if not tools or not query or not examples_active(config):
        return tools
    workspace = hook_workspace_from_config(config)
    project_root = resolve_project_root_path(workspace=workspace)
    if project_root is None:
        return tools
    cfg = tool_examples_config(config)
    store = ToolExamplesStore.open(cfg.db_path)
    try:
        project_id = store.get_or_create_project(str(project_root))
        return [
            _enrich_single_tool(
                tool,
                query=query,
                project_id=project_id,
                store=store,
                cfg=cfg,
            )
            for tool in tools
        ]
    finally:
        store.close()


def _write_schema_back_to_tool(out: dict[str, Any], schema: dict[str, Any]) -> None:
    if "input_schema" in out:
        out["input_schema"] = schema
    elif "inputSchema" in out:
        out["inputSchema"] = schema
    elif "parameters" in out:
        out["parameters"] = schema


def _append_full_call_examples(
    out: dict[str, Any],
    *,
    query: str,
    captures: list[ToolCapture],
    cfg: ToolExamplesConfig,
) -> None:
    if not cfg.full_call_examples:
        return
    call_items = [
        (
            capture.input_json,
            json.dumps(capture.input_json, ensure_ascii=False),
            capture.last_seen_ms,
        )
        for capture in captures
    ]
    ranked_calls = rank_by_query(query, call_items)[: cfg.max_full_call_examples]
    if not ranked_calls:
        return
    snippets = []
    for item in ranked_calls:
        text = json.dumps(item.item, ensure_ascii=False, separators=(",", ":"))
        if len(text) > cfg.max_value_chars:
            text = text[: cfg.max_value_chars - 3] + "..."
        snippets.append(text)
    desc = str(out.get("description") or "").rstrip()
    call_suffix = f" Full-call examples: {'; '.join(snippets)}."
    out["description"] = f"{desc}{call_suffix}" if desc else call_suffix.lstrip()


def _enrich_schema_properties(
    schema: dict[str, Any],
    *,
    query: str,
    schema_ids: list[int],
    store: ToolExamplesStore,
    cfg: ToolExamplesConfig,
) -> None:
    for path, spec in _collect_property_nodes(schema):
        rows = store.list_examples_for_path(schema_ids, path, limit=cfg.max_per_path)
        values = _rank_example_values(query, rows, max_count=cfg.max_per_property)
        if not values:
            continue
        current = str(spec.get("description") or "")
        spec["description"] = _append_examples(current, values, max_value_chars=cfg.max_value_chars)


def _enrich_single_tool(
    tool: dict[str, Any],
    *,
    query: str,
    project_id: int,
    store: ToolExamplesStore,
    cfg: ToolExamplesConfig,
) -> dict[str, Any]:
    out = copy.deepcopy(tool)
    schema = _schema_from_tool(out)
    if not schema:
        return out
    mcp_server, tool_name = resolve_mcp_server_and_tool(out)
    schema_hash = content_hash(schema)
    captures = store.list_captures(
        project_id,
        mcp_server,
        tool_name,
        schema_hash=schema_hash if not cfg.cross_schema_fallback else None,
        limit=cfg.max_captures_per_tool,
    )
    if not captures and cfg.cross_schema_fallback:
        captures = store.list_captures(
            project_id,
            mcp_server,
            tool_name,
            limit=cfg.max_captures_per_tool,
        )
    if not captures:
        return out
    schema_ids = [capture.schema_id for capture in captures]
    _enrich_schema_properties(
        schema,
        query=query,
        schema_ids=schema_ids,
        store=store,
        cfg=cfg,
    )
    _append_full_call_examples(out, query=query, captures=captures, cfg=cfg)
    _write_schema_back_to_tool(out, schema)
    return out
