"""Tier-scoped tool materialization and token counting for ``tiers stats``."""

from __future__ import annotations

import copy
from typing import Any

_CARRIED_TIER = "T4"
_carried_token_memo: dict[str, int] = {}

CYT_BACKEND_INPUT_SCHEMA = "cyt_backend_input_schema"


def clear_carried_token_memo() -> None:
    """Reset memo used by :func:`carried_tool_token_count` (tests)."""
    _carried_token_memo.clear()


def input_schema_from_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Return the tool input schema from common catalog field names."""
    for key in ("input_schema", "inputSchema", "parameters"):
        raw = tool.get(key)
        if isinstance(raw, dict) and raw:
            return dict(raw)
    return {}


def schema_for_tier(schema: dict[str, Any], tier_label: str) -> dict[str, Any]:
    """Trim *schema* to the portion materialized at *tier_label*."""
    tier = tier_label.strip().upper()
    if tier in {"T0", "T1"}:
        return {}
    if tier == "T2":
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return {}
        required_raw = schema.get("required")
        required = (
            [str(name) for name in required_raw if str(name) in properties]
            if isinstance(required_raw, list)
            else []
        )
        if not required:
            return {}
        trimmed_props = {name: properties[name] for name in required}
        return {
            "type": schema.get("type", "object"),
            "properties": trimmed_props,
            "required": required,
        }
    return dict(schema)


def _is_mcpc_tool(tool: dict[str, Any]) -> bool:
    source = str(tool.get("cyt_catalog_source") or "").strip().lower()
    if source == "mcpc":
        return True
    return bool(str(tool.get("mcpc_session") or "").strip())


def stamp_tool_dual_schema(tool: dict[str, Any], tier_label: str) -> dict[str, Any]:
    """Stamp ``cyt_backend_input_schema`` and tier-scoped ``input_schema`` on *tool*."""
    backend = input_schema_from_tool(tool)
    tool_copy = copy.deepcopy(tool)
    tool_copy[CYT_BACKEND_INPUT_SCHEMA] = copy.deepcopy(backend)
    tool_copy["input_schema"] = schema_for_tier(backend, tier_label)
    tool_copy.pop("inputSchema", None)
    tool_copy.pop("parameters", None)
    return tool_copy


def _tool_for_tier(tool: dict[str, Any], tier_label: str) -> dict[str, Any]:
    return stamp_tool_dual_schema(tool, tier_label)


def materialize_tool_text(tool: dict[str, Any], tier_label: str) -> str:
    """Format one tool the way hook injection would at *tier_label*."""
    tier = tier_label.strip().upper()
    if tier == "T0":
        return ""

    tool_copy = _tool_for_tier(tool, tier)
    tool_copy["cyt_injection_tier"] = tier.lower()
    include_description = True

    if _is_mcpc_tool(tool_copy):
        from cyt.tools.mcpc_inject import _format_mcpc_tool_item

        return _format_mcpc_tool_item(tool_copy, include_description=include_description)

    from cyt.tools.inject import format_tool_item

    return format_tool_item(tool_copy, include_tool_description=include_description)


def carried_tool_token_count(tool: dict[str, Any]) -> int:
    """Full-schema (T4) token count for *tool*, memoized by entity id."""
    from cyt.indexer.tokens import count_tokens
    from cyt.tiers.adapters.tools import tool_entity_id

    key = tool_entity_id(tool) or str(tool.get("name") or "").strip()

    text = materialize_tool_text(tool, _CARRIED_TIER)
    count = count_tokens(text) if text.strip() else 0

    if key:
        prior = _carried_token_memo.get(key)
        count = max(prior or 0, count)
        _carried_token_memo[key] = count
    return count


def effective_tool_token_count(tool: dict[str, Any], tier_label: str) -> int:
    """Token count for *tool* materialized at *tier_label*."""
    tier = tier_label.strip().upper()
    if tier == "T0":
        return 0

    from cyt.indexer.tokens import count_tokens

    text = materialize_tool_text(tool, tier)
    count = count_tokens(text) if text.strip() else 0
    if tier in {"T1", "T2", "T3", "T4"}:
        return min(count, carried_tool_token_count(tool))
    return count


def catalog_tools_by_entity_id(
    catalog_tools: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    from cyt.tiers.adapters.tools import tool_entity_id

    out: dict[str, dict[str, Any]] = {}
    if not catalog_tools:
        return out
    for tool in catalog_tools:
        entity_id = tool_entity_id(tool)
        if entity_id:
            out[entity_id] = tool
    return out


def catalog_tool_for_entity(
    entity_id: str,
    catalog_tools: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    return catalog_tools_by_entity_id(catalog_tools).get(entity_id)


def attach_tool_token_count(
    record: dict[str, Any],
    *,
    catalog_tools: list[dict[str, Any]] | None,
) -> None:
    """Set ``token_count`` on a tool status record when catalog metadata exists."""
    entity_id = str(record.get("entity_id") or "").strip()
    if not entity_id:
        return
    catalog_tool = catalog_tool_for_entity(entity_id, catalog_tools)
    if catalog_tool is None:
        return
    record["token_count"] = carried_tool_token_count(catalog_tool)
