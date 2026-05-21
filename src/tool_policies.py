"""System vs MCP tool policies for catalog pruning (rerank / llm)."""

from __future__ import annotations

import copy
import json
import time
from typing import Any, Literal

_DEBUG_LOG_PATH = (
    "/Volumes/OWCExpress1M2/Users/dberezenko/git/github.com/asadani/tool-attention"
    "/.cursor/debug-b955fa.log"
)


def _agent_debug_log(
    *,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any],
    run_id: str = "pre-fix",
) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "b955fa",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")
    except OSError:
        pass
    # #endregion

from build_index import collect_enums
from retrieve_catalog import get_root_tool_key, to_decomposed_key

SystemToolPolicy = Literal["always_include", "prune_optional", "prune_all"]
MCPToolPolicy = Literal["always_include", "prune_optional", "prune_all"]

SYSTEM_TOOL_POLICY: SystemToolPolicy = "always_include"
MCP_TOOL_POLICY: MCPToolPolicy = "prune_optional"

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
    """Apply defaults.system_tool_policy / mcp_tool_policy from config.yaml."""
    global SYSTEM_TOOL_POLICY, MCP_TOOL_POLICY
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

def _should_pin_json_chunk(
    item: dict[str, Any],
    *,
    system_policy: SystemToolPolicy,
    mcp_policy: MCPToolPolicy,
) -> bool:
    if is_system_chunk(item):
        return system_policy == "prune_optional" and is_system_root_chunk(item)
    if is_non_system_chunk(item):
        return mcp_policy == "prune_optional" and is_mcp_root_chunk(item)
    return False


def partition_catalog(
    data: CatalogDict,
    system_policy: SystemToolPolicy = SYSTEM_TOOL_POLICY,
    mcp_policy: MCPToolPolicy = MCP_TOOL_POLICY,
) -> tuple[CatalogDict, PinnedCatalog]:
    """Split catalog into processable (rerank/llm) and pinned (restored after pruning)."""
    if not needs_partition(system_policy, mcp_policy):
        return copy.deepcopy(data), {}

    json_items = data.get("json")
    md_items = data.get("md")
    json_list = json_items if isinstance(json_items, list) else []
    md_list = md_items if isinstance(md_items, list) else []

    processable: CatalogDict = {
        k: copy.deepcopy(v)
        for k, v in data.items()
        if k not in ("json", "md", "system_required_enum_values", "mcp_required_enum_values")
    }
    pinned: PinnedCatalog = {
        "json": [],
        "md": [],
        "system_required_enum_values": [],
        "mcp_required_enum_values": [],
    }

    pinned_json: list[dict[str, Any]] = []
    processable_json: list[dict[str, Any]] = []
    system_required_enums: set[str] = set()
    mcp_required_enums: set[str] = set()

    for item in json_list:
        if not isinstance(item, dict):
            continue
        if _should_pin_json_chunk(item, system_policy=system_policy, mcp_policy=mcp_policy):
            copy_item = copy.deepcopy(item)
            pinned_json.append(copy_item)
            enum_vals = collect_enum_values_from_chunks([copy_item])
            if is_system_chunk(item):
                system_required_enums.update(enum_vals)
            elif is_non_system_chunk(item):
                mcp_required_enums.update(enum_vals)
        else:
            processable_json.append(copy.deepcopy(item))

    pinned_enum_values: frozenset[str] = frozenset()
    if system_policy == "prune_optional":
        pinned_enum_values = pinned_enum_values | frozenset(system_required_enums)
    if mcp_policy == "prune_optional":
        pinned_enum_values = pinned_enum_values | frozenset(mcp_required_enums)

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
    result: list[dict[str, Any]] = []
    for entry in all_entries:
        tool_id = str(entry.get("id", ""))
        if is_system_tool_id(tool_id) and system_tools_pass_through(system_policy):
            continue
        if is_non_system_tool_id(tool_id) and mcp_tools_pass_through(mcp_policy):
            continue
        result.append(entry)
    return result


def tools_for_catalog(
    tools: list[dict[str, Any]],
    system_policy: SystemToolPolicy = SYSTEM_TOOL_POLICY,
    mcp_policy: MCPToolPolicy = MCP_TOOL_POLICY,
) -> list[dict[str, Any]]:
    """Anthropic tools to decompose, excluding always_include stashes."""
    result: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name", ""))
        if is_system_tool_id(name) and system_tools_pass_through(system_policy):
            continue
        if is_non_system_tool_id(name) and mcp_tools_pass_through(mcp_policy):
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


# Match rerank.py RERANK_SCORE — chunks below this are dropped by rerank prune.
RERANK_SURVIVOR_SCORE: float = 0.001


def filter_recompose_json_entries(
    json_list: list[dict[str, Any]],
    *,
    system_policy: SystemToolPolicy = SYSTEM_TOOL_POLICY,
    mcp_policy: MCPToolPolicy = MCP_TOOL_POLICY,
    rerank_score: float = RERANK_SURVIVOR_SCORE,
) -> list[dict[str, Any]]:
    """Keep pinned roots always; optional property chunks only if they survived rerank scoring."""
    filtered: list[dict[str, Any]] = []
    optional_decisions: list[dict[str, Any]] = []
    for item in json_list:
        if not isinstance(item, dict):
            continue
        if is_decomposed_tool_root_chunk(item):
            filtered.append(item)
            continue
        if not is_decomposed_optional_property_chunk(item):
            continue
        score = float(item.get("score", 0))
        kept = False
        if is_system_chunk(item):
            if system_policy == "prune_all":
                filtered.append(item)
                kept = True
            elif system_policy == "prune_optional" and score >= rerank_score:
                filtered.append(item)
                kept = True
        elif is_non_system_chunk(item):
            if mcp_policy == "prune_all":
                filtered.append(item)
                kept = True
            elif mcp_policy == "prune_optional" and score >= rerank_score:
                filtered.append(item)
                kept = True
        tool_id = chunk_tool_id(item)
        optional_decisions.append(
            {
                "tool_id": tool_id,
                "is_mcp": is_non_system_chunk(item),
                "file_path": item.get("file_path"),
                "score": score,
                "kept": kept,
                "rerank_score": rerank_score,
                "system_policy": system_policy,
                "mcp_policy": mcp_policy,
            }
        )
    if optional_decisions:
        _agent_debug_log(
            hypothesis_id="A",
            location="tool_policies.py:filter_recompose_json_entries",
            message="optional chunk recompose filter",
            data={
                "in_count": len(json_list),
                "out_count": len(filtered),
                "decisions": optional_decisions,
            },
        )
    return filtered
