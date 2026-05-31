"""System vs MCP tool policies for catalog pruning (rerank / llm)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from cyt.common.catalog_paths import (
    DECOMPOSED_PREFIX,
    DECOMPOSED_ROOT,
    collect_enums,
    get_root_tool_key,
    to_decomposed_key,
    tool_id_from_decomposed_rel,
)
from cyt.config import DEFAULT_MCP_TOOL_POLICY, DEFAULT_SYSTEM_TOOL_POLICY, load_config

# Keep in sync with rerank.RERANK_SCORE (avoid circular import: rerank imports tool_policies).
RERANK_SCORE: float = 0.003

if TYPE_CHECKING:
    from cyt.indexer.build import CatalogIndex

SystemToolPolicy = Literal["always_include", "prune_optional", "prune_all"]
MCPToolPolicy = Literal["always_include", "prune_optional", "prune_all"]
ToolPolicy = Literal["always_include", "prune_optional", "prune_all"]

system_tool_policy: SystemToolPolicy = DEFAULT_SYSTEM_TOOL_POLICY
mcp_tool_policy: MCPToolPolicy = DEFAULT_MCP_TOOL_POLICY
PER_TOOL_POLICIES: dict[str, ToolPolicy] = {}


CatalogDict = dict[str, Any]
PinnedCatalog = dict[str, Any]


def is_non_system_tool_id(tool_id: str) -> bool:
    return tool_id.startswith("mcp__")


def is_system_tool_id(tool_id: str) -> bool:
    return not is_non_system_tool_id(tool_id)


def chunk_tool_id(item: dict[str, Any]) -> str:
    raw = item.get("id") or item.get("name") or ""
    return str(raw)


def effective_policy(tool_id: str) -> ToolPolicy:
    if tool_id in PER_TOOL_POLICIES:
        return PER_TOOL_POLICIES[tool_id]
    if is_system_tool_id(tool_id):
        return system_tool_policy
    return mcp_tool_policy


def tool_pass_through(tool_id: str) -> bool:
    return effective_policy(tool_id) == "always_include"


def root_tool_id_from_chunk(item: dict[str, Any]) -> str:
    file_path = str(item.get("file_path", ""))
    root_key = get_root_tool_key(file_path)
    if root_key is not None:
        return tool_id_from_decomposed_rel(root_key)
    return chunk_tool_id(item)


def request_pass_through(tools: list[dict[str, Any]]) -> bool:
    named = [t for t in tools if isinstance(t, dict) and str(t.get("name", ""))]
    if not named:
        return True
    return all(tool_pass_through(str(t.get("name", ""))) for t in named)


def is_non_system_chunk(item: dict[str, Any]) -> bool:
    return is_non_system_tool_id(chunk_tool_id(item))


def is_system_chunk(item: dict[str, Any]) -> bool:
    return is_system_tool_id(chunk_tool_id(item))


def is_decomposed_tool_root_chunk(item: dict[str, Any]) -> bool:
    """True for the tool root file (schemas/decomposed/{tool_id}.json).

    Root vs optional is determined by decomposition output path, not chunk content
    (e.g. presence of ``description``). Required properties stay in the root file;
    properties not in a parent object's ``required`` array are extracted to nested paths.
    """
    file_path = str(item.get("file_path", ""))
    if not file_path:
        return False
    root_key = get_root_tool_key(file_path)
    decomposed_key = to_decomposed_key(file_path)
    return root_key is not None and decomposed_key == root_key


def is_decomposed_optional_property_chunk(item: dict[str, Any]) -> bool:
    """True for optional property extractions (schemas/decomposed/{tool_id}/…/{prop}.json)."""
    file_path = str(item.get("file_path", ""))
    if not file_path:
        return False
    decomposed_key = to_decomposed_key(file_path)
    if decomposed_key is None:
        return False
    root_key = get_root_tool_key(file_path)
    return root_key is not None and decomposed_key != root_key


def is_system_root_chunk(item: dict[str, Any]) -> bool:
    return is_system_chunk(item) and is_decomposed_tool_root_chunk(item)


def is_mcp_root_chunk(item: dict[str, Any]) -> bool:
    return is_non_system_chunk(item) and is_decomposed_tool_root_chunk(item)


def is_system_optional_chunk(item: dict[str, Any]) -> bool:
    return is_system_chunk(item) and is_decomposed_optional_property_chunk(item)


def is_mcp_optional_chunk(item: dict[str, Any]) -> bool:
    return is_non_system_chunk(item) and is_decomposed_optional_property_chunk(item)


def needs_partition(
    system_policy: SystemToolPolicy = system_tool_policy,
    mcp_policy: MCPToolPolicy = mcp_tool_policy,
) -> bool:
    return system_policy == "prune_optional" or mcp_policy == "prune_optional"


def uses_pruned_recompose(policy: SystemToolPolicy | MCPToolPolicy) -> bool:
    return policy in ("prune_optional", "prune_all")


def needs_pruned_recompose(
    system_policy: SystemToolPolicy = system_tool_policy,
    mcp_policy: MCPToolPolicy = mcp_tool_policy,
) -> bool:
    return uses_pruned_recompose(system_policy) or uses_pruned_recompose(mcp_policy)


def chunk_policy(
    item: dict[str, Any],
    *,
    system_policy: SystemToolPolicy,
    mcp_policy: MCPToolPolicy,
) -> SystemToolPolicy | MCPToolPolicy | None:
    if is_system_chunk(item):
        return system_policy
    if is_non_system_chunk(item):
        return mcp_policy
    return None


def system_tools_pass_through(
    system_policy: SystemToolPolicy = system_tool_policy,
) -> bool:
    """System tools skip decomposition/rerank and are restored unchanged from the request."""
    return system_policy == "always_include"


def mcp_tools_pass_through(
    mcp_policy: MCPToolPolicy = mcp_tool_policy,
) -> bool:
    return mcp_policy == "always_include"


def full_pass_through(
    system_policy: SystemToolPolicy = system_tool_policy,
    mcp_policy: MCPToolPolicy = mcp_tool_policy,
) -> bool:
    return system_policy == "always_include" and mcp_policy == "always_include"


def configure_policies_from_config(config: dict[str, Any] | None = None) -> None:
    """Apply defaults.system_tool_policy / mcp_tool_policy / pruning.per_tool from config.yaml."""
    global system_tool_policy, mcp_tool_policy
    if config is None:
        config = load_config()
    defaults = config.get("defaults", {})
    system = defaults.get("system_tool_policy")
    mcp = defaults.get("mcp_tool_policy")
    if system in ("always_include", "prune_optional", "prune_all"):
        system_tool_policy = system
    if mcp in ("always_include", "prune_optional", "prune_all"):
        mcp_tool_policy = mcp
    pruning = config.get("pruning")
    per_tool = pruning.get("per_tool") if isinstance(pruning, dict) else None
    PER_TOOL_POLICIES.clear()
    if isinstance(per_tool, dict):
        PER_TOOL_POLICIES.update(
            {
                str(tool_id): policy
                for tool_id, policy in per_tool.items()
                if policy in ("always_include", "prune_optional", "prune_all")
            },
        )


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


def _should_pin_json_chunk(item: dict[str, Any]) -> bool:
    if not is_decomposed_tool_root_chunk(item):
        return False
    return effective_policy(root_tool_id_from_chunk(item)) == "prune_optional"


def catalog_needs_partition(data: CatalogDict) -> bool:
    if needs_partition(system_tool_policy, mcp_tool_policy):
        return True
    json_items = data.get("json")
    if not isinstance(json_items, list):
        return False
    seen: set[str] = set()
    for item in json_items:
        if not isinstance(item, dict):
            continue
        tool_id = root_tool_id_from_chunk(item)
        if tool_id in seen:
            continue
        seen.add(tool_id)
        if effective_policy(tool_id) == "prune_optional":
            return True
    return False


def catalog_needs_pruned_recompose(data: CatalogDict) -> bool:
    if needs_pruned_recompose(system_tool_policy, mcp_tool_policy):
        return True
    json_items = data.get("json")
    if not isinstance(json_items, list):
        return False
    seen: set[str] = set()
    for item in json_items:
        if not isinstance(item, dict):
            continue
        tool_id = root_tool_id_from_chunk(item)
        if tool_id in seen:
            continue
        seen.add(tool_id)
        if uses_pruned_recompose(effective_policy(tool_id)):
            return True
    return False


def _partition_json_items(
    json_list: list[Any],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    set[str],
    set[str],
    dict[str, set[str]],
]:
    pinned_json: list[dict[str, Any]] = []
    processable_json: list[dict[str, Any]] = []
    system_required_enums: set[str] = set()
    mcp_required_enums: set[str] = set()
    required_enums_by_tool: dict[str, set[str]] = {}

    for item in json_list:
        if not isinstance(item, dict):
            continue
        if _should_pin_json_chunk(item):
            copy_item = copy.deepcopy(item)
            pinned_json.append(copy_item)
            tool_id = root_tool_id_from_chunk(item)
            enum_vals = collect_enum_values_from_chunks([copy_item])
            required_enums_by_tool.setdefault(tool_id, set()).update(enum_vals)
            if is_system_chunk(item):
                system_required_enums.update(enum_vals)
            elif is_non_system_chunk(item):
                mcp_required_enums.update(enum_vals)
        else:
            processable_json.append(copy.deepcopy(item))

    return (
        pinned_json,
        processable_json,
        system_required_enums,
        mcp_required_enums,
        required_enums_by_tool,
    )


def _partition_md_items(
    md_list: list[Any],
    pinned_enum_values: frozenset[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    processable_md: list[dict[str, Any]] = []
    pinned_md: list[dict[str, Any]] = []

    for md_item in md_list:
        if not isinstance(md_item, dict):
            continue
        copy_item = copy.deepcopy(md_item)
        if _enum_md_matches_values(copy_item, pinned_enum_values):
            pinned_md.append(copy_item)
        else:
            processable_md.append(copy_item)

    return processable_md, pinned_md


def partition_catalog(
    data: CatalogDict,
    system_policy: SystemToolPolicy = system_tool_policy,
    mcp_policy: MCPToolPolicy = mcp_tool_policy,
) -> tuple[CatalogDict, PinnedCatalog]:
    """Split catalog into processable (rerank/llm) and pinned (restored after pruning)."""
    del system_policy, mcp_policy  # per-tool effective_policy drives pinning
    if not catalog_needs_partition(data):
        return copy.deepcopy(data), {}

    json_items = data.get("json")
    md_items = data.get("md")
    json_list = json_items if isinstance(json_items, list) else []
    md_list = md_items if isinstance(md_items, list) else []

    metadata_keys = (
        "json",
        "md",
        "system_required_enum_values",
        "mcp_required_enum_values",
        "required_enum_values_by_tool",
    )
    processable: CatalogDict = {
        k: copy.deepcopy(v) for k, v in data.items() if k not in metadata_keys
    }
    pinned: PinnedCatalog = {
        "json": [],
        "md": [],
        "system_required_enum_values": [],
        "mcp_required_enum_values": [],
        "required_enum_values_by_tool": {},
    }

    (
        pinned_json,
        processable_json,
        system_required_enums,
        mcp_required_enums,
        required_enums_by_tool,
    ) = _partition_json_items(json_list)

    pinned_enum_values: frozenset[str] = frozenset()
    for vals in required_enums_by_tool.values():
        pinned_enum_values = pinned_enum_values | frozenset(vals)

    processable_md, pinned_md = _partition_md_items(md_list, pinned_enum_values)

    processable["json"] = processable_json
    processable["md"] = processable_md
    pinned["json"] = pinned_json
    pinned["md"] = pinned_md
    pinned["system_required_enum_values"] = sorted(system_required_enums)
    pinned["mcp_required_enum_values"] = sorted(mcp_required_enums)
    pinned["required_enum_values_by_tool"] = {
        tool_id: sorted(vals) for tool_id, vals in required_enums_by_tool.items()
    }
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
    if pinned.get("mcp_required_enum_values") is not None:
        merged["mcp_required_enum_values"] = list(pinned["mcp_required_enum_values"])
    if pinned.get("required_enum_values_by_tool") is not None:
        merged["required_enum_values_by_tool"] = copy.deepcopy(
            pinned["required_enum_values_by_tool"],
        )
    return merged


def stash_system_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deep-copy Anthropic tools whose name does not start with mcp__."""
    return [
        copy.deepcopy(t)
        for t in tools
        if isinstance(t, dict) and is_system_tool_id(str(t.get("name", "")))
    ]


def restore_system_tools(stash: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return copy.deepcopy(stash)


def stash_mcp_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deep-copy Anthropic tools whose name starts with mcp__."""
    return [
        copy.deepcopy(t)
        for t in tools
        if isinstance(t, dict) and is_non_system_tool_id(str(t.get("name", "")))
    ]


def restore_mcp_tools(stash: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return copy.deepcopy(stash)


def merge_tools_preserving_order(
    original: list[dict[str, Any]],
    pruned_by_name: dict[str, dict[str, Any]],
    stashed_by_name: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rebuild tools[] in original order from pruned pipeline output and stashed pass-through tools."""
    result: list[dict[str, Any]] = []
    for tool in original:
        name = str(tool.get("name", ""))
        if not name:
            continue
        if name in stashed_by_name:
            result.append(copy.deepcopy(stashed_by_name[name]))
        elif name in pruned_by_name:
            result.append(copy.deepcopy(pruned_by_name[name]))
    return result


def anthropic_tool_is_system(tool: dict[str, Any]) -> bool:
    return is_system_tool_id(str(tool.get("name", "")))


def anthropic_tool_is_mcp(tool: dict[str, Any]) -> bool:
    return is_non_system_tool_id(str(tool.get("name", "")))


def split_anthropic_tools(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    non_system: list[dict[str, Any]] = []
    system: list[dict[str, Any]] = []
    for tool in tools:
        if anthropic_tool_is_system(tool):
            system.append(tool)
        else:
            non_system.append(tool)
    return non_system, system


def entries_for_policy(
    all_entries: list[dict[str, Any]],
    system_policy: SystemToolPolicy = system_tool_policy,
    mcp_policy: MCPToolPolicy = mcp_tool_policy,
) -> list[dict[str, Any]]:
    """Catalog build_index entries to decompose for the pruning pipeline."""
    del system_policy, mcp_policy
    result: list[dict[str, Any]] = []
    for entry in all_entries:
        tool_id = str(entry.get("id", ""))
        if tool_id and tool_pass_through(tool_id):
            continue
        result.append(entry)
    return result


def tools_for_catalog(
    tools: list[dict[str, Any]],
    system_policy: SystemToolPolicy = system_tool_policy,
    mcp_policy: MCPToolPolicy = mcp_tool_policy,
) -> list[dict[str, Any]]:
    """Anthropic tools to decompose, excluding always_include stashes."""
    del system_policy, mcp_policy
    result: list[dict[str, Any]] = []
    for tool in tools:
        name = str(tool.get("name", ""))
        if name and tool_pass_through(name):
            continue
        result.append(tool)
    return result


def system_required_enum_values(data: CatalogDict) -> frozenset[str]:
    raw = data.get("system_required_enum_values")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(x) for x in raw)


def mcp_required_enum_values(data: CatalogDict) -> frozenset[str]:
    raw = data.get("mcp_required_enum_values")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(x) for x in raw)


def required_enum_values_by_tool(data: CatalogDict) -> dict[str, frozenset[str]]:
    raw = data.get("required_enum_values_by_tool")
    if not isinstance(raw, dict):
        return {}
    return {
        str(tool_id): frozenset(str(x) for x in values)
        for tool_id, values in raw.items()
        if isinstance(values, list)
    }


def optional_leaf_survived_rerank(
    item: dict[str, Any],
    *,
    system_policy: SystemToolPolicy | None = None,
    mcp_policy: MCPToolPolicy | None = None,
    rerank_score: float = RERANK_SCORE,
    llm_selected_paths: set[str] | None = None,
) -> bool:
    """Whether an optional property leaf should be merged (then climbed) on recompose."""
    del system_policy, mcp_policy
    if not is_decomposed_optional_property_chunk(item):
        return False
    file_path = str(item.get("file_path", ""))
    if llm_selected_paths is not None and file_path in llm_selected_paths:
        return True
    policy = effective_policy(root_tool_id_from_chunk(item))
    if policy == "prune_all":
        return True
    if policy == "prune_optional":
        return float(item.get("score", 0)) >= rerank_score
    return False


def filter_recompose_json_entries(
    json_list: list[dict[str, Any]],
    *,
    system_policy: SystemToolPolicy = system_tool_policy,
    mcp_policy: MCPToolPolicy = mcp_tool_policy,
    rerank_score: float = RERANK_SCORE,
    llm_selected_paths: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Tool roots always; optional leaves that survived rerank and/or LLM selection."""
    del system_policy, mcp_policy
    filtered: list[dict[str, Any]] = []
    for item in json_list:
        if is_decomposed_tool_root_chunk(item):
            filtered.append(item)
        elif optional_leaf_survived_rerank(
            item,
            rerank_score=rerank_score,
            llm_selected_paths=llm_selected_paths,
        ):
            filtered.append(item)
    return filtered


EMPTY_OPTIONAL_FALLBACK_K = 3


def is_direct_root_optional_property_chunk(item: dict[str, Any]) -> bool:
    """Optional leaf at schemas/decomposed/{tool_id}/{prop}.json (one segment under tool id)."""
    if not is_decomposed_optional_property_chunk(item):
        return False
    file_path = str(item.get("file_path", ""))
    key = to_decomposed_key(file_path)
    if key is None:
        return False
    rel = Path(key).relative_to(DECOMPOSED_ROOT)
    return len(rel.parts) == 2 and rel.parts[1].endswith(".json")


def _chunk_input_schema(item: dict[str, Any]) -> dict[str, Any]:
    content = item.get("content")
    if isinstance(content, dict):
        schema = content.get("inputSchema") or content.get("input_schema")
        if isinstance(schema, dict):
            return schema
    return {}


def root_chunk_properties_empty(item: dict[str, Any]) -> bool:
    if not is_decomposed_tool_root_chunk(item):
        return False
    props = _chunk_input_schema(item).get("properties")
    return not props


def tool_id_has_empty_decomposed_root(catalog_index: CatalogIndex, tool_id: str) -> bool:
    rel = f"{DECOMPOSED_PREFIX}{tool_id}.json"
    raw = catalog_index.files.get(rel)
    if raw is None:
        return False
    parsed = json.loads(raw)
    schema = parsed.get("inputSchema") or parsed.get("input_schema") or {}
    if not isinstance(schema, dict):
        return True
    return not schema.get("properties")


def _original_tool_input_schema(catalog_index: CatalogIndex, tool_id: str) -> dict[str, Any]:
    full_rel = f"schemas/full/{tool_id}.json"
    raw = catalog_index.files.get(full_rel)
    if raw is not None:
        parsed = json.loads(raw)
        schema = parsed.get("inputSchema") or parsed.get("input_schema")
        if isinstance(schema, dict):
            return schema
    for entry in catalog_index.tools:
        if str(entry.get("id", "")) != tool_id:
            continue
        full_schema = entry.get("full_schema")
        if isinstance(full_schema, dict):
            schema = full_schema.get("inputSchema") or full_schema.get("input_schema")
            if isinstance(schema, dict):
                return schema
    return {}


def tool_id_had_empty_original_root_properties(catalog_index: CatalogIndex, tool_id: str) -> bool:
    """True when the pre-decomposition tool already had no root-level properties."""
    return not _original_tool_input_schema(catalog_index, tool_id).get("properties")


def needs_empty_optional_mitigation(catalog_index: CatalogIndex, tool_id: str) -> bool:
    """Mitigate only when optional props were decomposed away, not originally absent."""
    return tool_id_has_empty_decomposed_root(
        catalog_index,
        tool_id,
    ) and not tool_id_had_empty_original_root_properties(catalog_index, tool_id)


def optional_chunks_for_tool(items: list[dict[str, Any]], tool_id: str) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if isinstance(item, dict)
        and is_decomposed_optional_property_chunk(item)
        and root_tool_id_from_chunk(item) == tool_id
    ]


def direct_root_optional_chunks_for_tool(
    items: list[dict[str, Any]],
    tool_id: str,
) -> list[dict[str, Any]]:
    return [
        item
        for item in optional_chunks_for_tool(items, tool_id)
        if is_direct_root_optional_property_chunk(item)
    ]


def _scored_json_entries(post_rerank_scored: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(post_rerank_scored, dict):
        return []
    raw_json = post_rerank_scored.get("json")
    if not isinstance(raw_json, list):
        return []
    return [x for x in raw_json if isinstance(x, dict)]


def _should_mitigate_empty_root(
    tool_id: str,
    root_item: dict[str, Any],
    entries: list[dict[str, Any]],
    catalog_index: CatalogIndex,
) -> bool:
    if not uses_pruned_recompose(effective_policy(tool_id)):
        return False
    if not needs_empty_optional_mitigation(catalog_index, tool_id):
        return False
    if not root_chunk_properties_empty(root_item):
        return False
    return not optional_chunks_for_tool(entries, tool_id)


def _append_rerank_fallback_chunks(
    tool_id: str,
    result: list[dict[str, Any]],
    seen_paths: set[Any],
    scored_json: list[dict[str, Any]],
) -> None:
    candidates = optional_chunks_for_tool(scored_json, tool_id)
    candidates.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
    for chunk in candidates[:EMPTY_OPTIONAL_FALLBACK_K]:
        file_path = chunk.get("file_path")
        if file_path in seen_paths:
            continue
        seen_paths.add(file_path)
        result.append(copy.deepcopy(chunk))


def _tool_roots_from_entries(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    roots_by_tool: dict[str, dict[str, Any]] = {}
    for item in entries:
        if isinstance(item, dict) and is_decomposed_tool_root_chunk(item):
            roots_by_tool[root_tool_id_from_chunk(item)] = item
    return roots_by_tool


def _drop_tools_from_entries(
    entries: list[dict[str, Any]],
    tools_to_drop: set[str],
) -> list[dict[str, Any]]:
    if not tools_to_drop:
        return entries
    return [
        item
        for item in entries
        if isinstance(item, dict) and root_tool_id_from_chunk(item) not in tools_to_drop
    ]


def mitigate_empty_optional_properties(
    entries: list[dict[str, Any]],
    *,
    catalog_index: CatalogIndex,
    post_rerank_scored: dict[str, Any] | None,
    pipeline: list[str],
) -> list[dict[str, Any]]:
    """Avoid recomposed tools with empty root properties when all props were optional."""
    if not pipeline or not entries:
        return entries

    last_stage = pipeline[-1]
    if last_stage not in ("rerank", "llm", "bm25"):
        return entries

    roots_by_tool = _tool_roots_from_entries(entries)
    if not roots_by_tool:
        return entries

    scored_json = _scored_json_entries(post_rerank_scored)

    result = list(entries)
    seen_paths = {item.get("file_path") for item in result if isinstance(item, dict)}
    tools_to_drop: set[str] = set()

    for tool_id, root_item in roots_by_tool.items():
        if not _should_mitigate_empty_root(tool_id, root_item, result, catalog_index):
            continue

        if last_stage == "llm":
            tools_to_drop.add(tool_id)
            continue

        if last_stage in ("rerank", "bm25") and scored_json:
            _append_rerank_fallback_chunks(tool_id, result, seen_paths, scored_json)

    return _drop_tools_from_entries(result, tools_to_drop)


def drop_recomposed_tools_with_empty_properties(
    tools: list[dict[str, Any]],
    catalog_index: CatalogIndex,
) -> list[dict[str, Any]]:
    """Post-recompose safety net: omit tools that still have empty root properties."""
    kept: list[dict[str, Any]] = []
    for tool in tools:
        name = str(tool.get("name", ""))
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        props = schema.get("properties") if isinstance(schema, dict) else None
        if props:
            kept.append(tool)
            continue
        if (
            name
            and uses_pruned_recompose(effective_policy(name))
            and needs_empty_optional_mitigation(catalog_index, name)
        ):
            continue
        kept.append(tool)
    return kept
