"""System vs MCP tool policies for catalog pruning (rerank / llm)."""

from __future__ import annotations

import copy
from typing import Any, Literal

from build_index import collect_enums, tool_id_from_decomposed_rel
from retrieve_catalog import get_root_tool_key, to_decomposed_key

from rerank import RERANK_SCORE

SystemToolPolicy = Literal["always_include", "prune_optional", "prune_all"]
MCPToolPolicy = Literal["always_include", "prune_optional", "prune_all"]
ToolPolicy = Literal["always_include", "prune_optional", "prune_all"]

SYSTEM_TOOL_POLICY: SystemToolPolicy = "always_include"
MCP_TOOL_POLICY: MCPToolPolicy = "prune_optional"
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
        return SYSTEM_TOOL_POLICY
    return MCP_TOOL_POLICY


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
    system_policy: SystemToolPolicy = SYSTEM_TOOL_POLICY,
    mcp_policy: MCPToolPolicy = MCP_TOOL_POLICY,
) -> bool:
    return system_policy == "prune_optional" or mcp_policy == "prune_optional"


def uses_pruned_recompose(policy: SystemToolPolicy | MCPToolPolicy) -> bool:
    return policy in ("prune_optional", "prune_all")


def needs_pruned_recompose(
    system_policy: SystemToolPolicy = SYSTEM_TOOL_POLICY,
    mcp_policy: MCPToolPolicy = MCP_TOOL_POLICY,
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
    system_policy: SystemToolPolicy = SYSTEM_TOOL_POLICY,
) -> bool:
    """System tools skip decomposition/rerank and are restored unchanged from the request."""
    return system_policy == "always_include"


def mcp_tools_pass_through(
    mcp_policy: MCPToolPolicy = MCP_TOOL_POLICY,
) -> bool:
    return mcp_policy == "always_include"


def full_pass_through(
    system_policy: SystemToolPolicy = SYSTEM_TOOL_POLICY,
    mcp_policy: MCPToolPolicy = MCP_TOOL_POLICY,
) -> bool:
    return system_policy == "always_include" and mcp_policy == "always_include"


def configure_policies_from_config(config: dict[str, Any] | None = None) -> None:
    """Apply defaults.system_tool_policy / mcp_tool_policy / pruning.per_tool from config.yaml."""
    global SYSTEM_TOOL_POLICY, MCP_TOOL_POLICY, PER_TOOL_POLICIES
    if config is None:
        from configs import load_config

        config = load_config()
    defaults = config.get("defaults", {})
    system = defaults.get("system_tool_policy")
    mcp = defaults.get("mcp_tool_policy")
    if system in ("always_include", "prune_optional", "prune_all"):
        SYSTEM_TOOL_POLICY = system
    if mcp in ("always_include", "prune_optional", "prune_all"):
        MCP_TOOL_POLICY = mcp
    pruning = config.get("pruning")
    per_tool = pruning.get("per_tool") if isinstance(pruning, dict) else None
    PER_TOOL_POLICIES.clear()
    if isinstance(per_tool, dict):
        PER_TOOL_POLICIES.update(
            {
                str(tool_id): policy
                for tool_id, policy in per_tool.items()
                if policy in ("always_include", "prune_optional", "prune_all")
            }
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
    if needs_partition(SYSTEM_TOOL_POLICY, MCP_TOOL_POLICY):
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
    if needs_pruned_recompose(SYSTEM_TOOL_POLICY, MCP_TOOL_POLICY):
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


def partition_catalog(
    data: CatalogDict,
    system_policy: SystemToolPolicy = SYSTEM_TOOL_POLICY,
    mcp_policy: MCPToolPolicy = MCP_TOOL_POLICY,
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
        k: copy.deepcopy(v)
        for k, v in data.items()
        if k not in metadata_keys
    }
    pinned: PinnedCatalog = {
        "json": [],
        "md": [],
        "system_required_enum_values": [],
        "mcp_required_enum_values": [],
        "required_enum_values_by_tool": {},
    }

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

    pinned_enum_values: frozenset[str] = frozenset()
    for vals in required_enums_by_tool.values():
        pinned_enum_values = pinned_enum_values | frozenset(vals)

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
        merged["required_enum_values_by_tool"] = copy.deepcopy(pinned["required_enum_values_by_tool"])
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
        if not isinstance(tool, dict):
            continue
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
        if not isinstance(tool, dict):
            continue
        if anthropic_tool_is_system(tool):
            system.append(tool)
        else:
            non_system.append(tool)
    return non_system, system


def entries_for_policy(
    all_entries: list[dict[str, Any]],
    system_policy: SystemToolPolicy = SYSTEM_TOOL_POLICY,
    mcp_policy: MCPToolPolicy = MCP_TOOL_POLICY,
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
    system_policy: SystemToolPolicy = SYSTEM_TOOL_POLICY,
    mcp_policy: MCPToolPolicy = MCP_TOOL_POLICY,
) -> list[dict[str, Any]]:
    """Anthropic tools to decompose, excluding always_include stashes."""
    del system_policy, mcp_policy
    result: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
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
) -> bool:
    """Whether an optional property leaf should be merged (then climbed) on recompose."""
    del system_policy, mcp_policy
    if not is_decomposed_optional_property_chunk(item):
        return False
    policy = effective_policy(root_tool_id_from_chunk(item))
    if policy == "prune_all":
        return True
    if policy == "prune_optional":
        return float(item.get("score", 0)) >= rerank_score
    return False


def filter_recompose_json_entries(
    json_list: list[dict[str, Any]],
    *,
    system_policy: SystemToolPolicy = SYSTEM_TOOL_POLICY,
    mcp_policy: MCPToolPolicy = MCP_TOOL_POLICY,
    rerank_score: float = RERANK_SCORE,
) -> list[dict[str, Any]]:
    """Tool roots always; optional leaves that survived rerank (policy-specific)."""
    del system_policy, mcp_policy
    filtered: list[dict[str, Any]] = []
    for item in json_list:
        if not isinstance(item, dict):
            continue
        if is_decomposed_tool_root_chunk(item):
            filtered.append(item)
        elif optional_leaf_survived_rerank(item, rerank_score=rerank_score):
            filtered.append(item)
    return filtered
