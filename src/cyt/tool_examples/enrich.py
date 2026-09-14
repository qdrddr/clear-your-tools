"""Attach query-relevant successful tool call examples to pruned tools."""

from __future__ import annotations

import copy
from typing import Any

from cyt.hook.workspace_config import hook_workspace_from_config
from cyt.tiers.config import resolve_project_root_path
from cyt.tiers.tool_token_materialization import input_schema_from_tool
from cyt.tool_examples.config import ToolExamplesConfig, examples_active, tool_examples_config
from cyt.tool_examples.hash_utils import content_hash
from cyt.tool_examples.identity import resolve_mcp_server_and_tool
from cyt.tool_examples.ranking import rank_full_call_examples
from cyt.tool_examples.store import ToolCapture, ToolExamplesStore
from cyt.tools.injection_schema import entangle_examples_with_schema


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


def _property_name_from_path(json_path: str) -> str:
    marker = ".properties."
    if marker in json_path:
        tail = json_path.rsplit(marker, 1)[-1]
        return tail.split(".")[-1]
    return json_path.rsplit(".", 1)[-1]


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
                config=config,
            )
            for tool in tools
        ]
    finally:
        store.close()


def _attach_call_examples(
    out: dict[str, Any],
    *,
    query: str,
    captures: list[ToolCapture],
    cfg: ToolExamplesConfig,
    config: dict[str, Any],
) -> None:
    if not cfg.full_call_examples:
        return
    call_items = [(capture.input_json, capture.last_seen_ms) for capture in captures]
    ranked_calls = rank_full_call_examples(
        query,
        call_items,
        max_count=cfg.max_full_call_examples,
        config=config,
        ranking=cfg.ranking,
    )
    if not ranked_calls:
        return
    schema = input_schema_from_tool(out)
    entangled = entangle_examples_with_schema(ranked_calls, schema) if schema else ranked_calls
    if not entangled:
        return
    out["cyt_injection_examples"] = entangled
    out["cyt_injection_examples_max_chars"] = cfg.max_value_chars


def _enrich_single_tool(
    tool: dict[str, Any],
    *,
    query: str,
    project_id: int,
    store: ToolExamplesStore,
    cfg: ToolExamplesConfig,
    config: dict[str, Any],
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
        schema_hash=None if cfg.cross_schema_fallback else schema_hash,
        limit=cfg.max_captures_per_tool,
    )
    if not captures and not cfg.cross_schema_fallback:
        captures = store.list_captures(
            project_id,
            mcp_server,
            tool_name,
            limit=cfg.max_captures_per_tool,
        )
    if not captures:
        return out
    _attach_call_examples(out, query=query, captures=captures, cfg=cfg, config=config)
    return out
