"""System vs non-system tool policies for catalog pruning (rerank / llm)."""

from __future__ import annotations

import copy
from typing import Any, Literal

from build_index import collect_enums

SystemToolPolicy = Literal["always_include", "prune_optional", "prune_all"]
MCPToolPolicy = Literal["always_include", "prune_optional", "prune_all"]

SYSTEM_TOOL_POLICY: SystemToolPolicy = "prune_optional"
MCP_TOOL_POLICY: MCPToolPolicy = "prune_all"

CatalogDict = dict[str, Any]
PinnedCatalog = dict[str, Any]


def is_non_system_tool_id(tool_id: str) -> bool:
    return tool_id.startswith("mcp__")


def is_system_tool_id(tool_id: str) -> bool:
    return not is_non_system_tool_id(tool_id)


def chunk_tool_id(item: dict[str, Any]) -> str:
    raw = item.get("id") or item.get("name") or ""
    return str(raw)


def is_non_system_chunk(item: dict[str, Any]) -> bool:
    return is_non_system_tool_id(chunk_tool_id(item))


def is_system_chunk(item: dict[str, Any]) -> bool:
    return is_system_tool_id(chunk_tool_id(item))


def is_system_root_chunk(item: dict[str, Any]) -> bool:
    """Tool root schema (required properties only) after decomposition."""
    if not is_system_chunk(item):
        return False
    content = item.get("content")
    if not isinstance(content, dict) or "inputSchema" not in content:
        return False
    # Optional property chunks reuse id/name/inputSchema but omit tool description.
    return "description" in content


def is_system_optional_chunk(item: dict[str, Any]) -> bool:
    return is_system_chunk(item) and not is_system_root_chunk(item)


def collect_enum_values_from_chunks(chunks: list[dict[str, Any]]) -> frozenset[str]:
    values: set[str] = set()
    for item in chunks:
        content = item.get("content")
        if content is not None:
            for val in collect_enums(content):
                values.add(str(val))
    return frozenset(values)


def _enum_md_matches_values(md_item: dict[str, Any], enum_values: frozenset[str]) -> bool:
    if not enum_values:
        return False
    content = md_item.get("content")
    return str(content) in enum_values


def _copy_list(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [copy.deepcopy(x) for x in items if isinstance(x, dict)]


def partition_catalog(
    data: CatalogDict,
    policy: SystemToolPolicy = SYSTEM_TOOL_POLICY,
) -> tuple[CatalogDict, PinnedCatalog]:
    """Split catalog into processable (rerank/llm) and pinned (restored after pruning)."""
    if policy == "prune_all":
        return copy.deepcopy(data), {}

    json_items = data.get("json")
    md_items = data.get("md")
    json_list = json_items if isinstance(json_items, list) else []
    md_list = md_items if isinstance(md_items, list) else []

    processable: CatalogDict = {
        k: copy.deepcopy(v)
        for k, v in data.items()
        if k not in ("json", "md")
    }
    pinned: PinnedCatalog = {
        "json": [],
        "md": [],
        "system_required_enum_values": [],
    }

    if policy == "always_include":
        processable["json"] = [copy.deepcopy(x) for x in json_list if is_non_system_chunk(x)]
        processable["md"] = _copy_list(md_list)
        pinned["system_required_enum_values"] = []
        return processable, pinned

    # prune_optional
    pinned_json: list[dict[str, Any]] = []
    processable_json: list[dict[str, Any]] = []
    system_required_enums: set[str] = set()

    for item in json_list:
        if not isinstance(item, dict):
            continue
        if is_non_system_chunk(item):
            processable_json.append(copy.deepcopy(item))
        elif is_system_root_chunk(item):
            pinned_json.append(copy.deepcopy(item))
            system_required_enums.update(collect_enum_values_from_chunks([item]))
        elif is_system_optional_chunk(item):
            processable_json.append(copy.deepcopy(item))

    processable_md: list[dict[str, Any]] = []
    pinned_md: list[dict[str, Any]] = []
    req_frozen = frozenset(system_required_enums)

    for md_item in md_list:
        if not isinstance(md_item, dict):
            continue
        copy_item = copy.deepcopy(md_item)
        if _enum_md_matches_values(copy_item, req_frozen):
            pinned_md.append(copy_item)
        else:
            processable_md.append(copy_item)

    processable["json"] = processable_json
    processable["md"] = processable_md
    pinned["json"] = pinned_json
    pinned["md"] = pinned_md
    pinned["system_required_enum_values"] = sorted(system_required_enums)
    return processable, pinned


def merge_catalog(processed: CatalogDict, pinned: PinnedCatalog) -> CatalogDict:
    """Restore pinned chunks and metadata after rerank/llm."""
    merged = copy.deepcopy(processed)
    pinned_json = pinned.get("json")
    pinned_md = pinned.get("md")
    if isinstance(pinned_json, list):
        merged.setdefault("json", [])
        if isinstance(merged["json"], list):
            merged["json"] = list(merged["json"]) + copy.deepcopy(pinned_json)
    if isinstance(pinned_md, list):
        merged.setdefault("md", [])
        if isinstance(merged["md"], list):
            merged["md"] = list(merged["md"]) + copy.deepcopy(pinned_md)
    if pinned.get("system_required_enum_values") is not None:
        merged["system_required_enum_values"] = list(pinned["system_required_enum_values"])
    return merged


def stash_system_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deep-copy Anthropic tools whose name does not start with mcp__."""
    return [
        copy.deepcopy(t)
        for t in tools
        if isinstance(t, dict) and not is_non_system_tool_id(str(t.get("name", "")))
    ]


def restore_system_tools(stash: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return copy.deepcopy(stash)


def anthropic_tool_is_system(tool: dict[str, Any]) -> bool:
    return is_system_tool_id(str(tool.get("name", "")))


def split_anthropic_tools(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    non_system: list[dict[str, Any]] = []
    system: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if anthropic_tool_is_system(tool):
            system.append(tool)
        else:
            non_system.append(tool)
    return non_system, system


def entries_for_policy(
    all_entries: list[dict[str, Any]],
    policy: SystemToolPolicy = SYSTEM_TOOL_POLICY,
) -> list[dict[str, Any]]:
    """Catalog build_index entries to decompose for the pruning pipeline."""
    if policy == "always_include":
        return [e for e in all_entries if is_non_system_tool_id(str(e.get("id", "")))]
    return all_entries


def system_required_enum_values(data: CatalogDict) -> frozenset[str]:
    raw = data.get("system_required_enum_values")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(x) for x in raw)
