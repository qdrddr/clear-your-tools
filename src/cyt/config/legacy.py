"""Backward-compatible config path resolution. Delete this module to drop legacy keys."""

from __future__ import annotations

from typing import Any

# Maps logical setting name → legacy key paths (first match wins).
LEGACY_PATHS: dict[str, list[tuple[str, ...]]] = {
    "tools.sequence": [("pruning", "pipeline")],
    "tools.policy.system_tool": [
        ("pruning", "policy", "system_tool"),
        ("defaults", "system_tool_policy"),
    ],
    "tools.policy.mcp_tool": [
        ("pruning", "policy", "mcp_tool"),
        ("defaults", "mcp_tool_policy"),
    ],
    "tools.policy.minimum_tools": [
        ("pruning", "policy", "minimum_tools"),
        ("models", "rerankers", "minimum_tools"),
        ("models", "llm", "minimum_tools"),
    ],
    "tools.policy.per_tool": [("pruning", "per_tool")],
    "pipelines.bm25": [("pruning", "bm25")],
    "pipelines.rerank": [("pruning", "rerank")],
    "pipelines.llm": [("pruning", "llm")],
    "pipelines.rerank.model_nick": [
        ("pruning", "rerank", "model_nick"),
        ("pruning", "rerank", "model", "remote", "model_nick"),
        ("defaults", "remote", "reranking_model_nick"),
    ],
    "pipelines.llm.model_nick": [
        ("pruning", "llm", "model_nick"),
        ("pruning", "llm", "model", "remote", "model_nick"),
        ("defaults", "remote", "llm_model_nick"),
    ],
    "pipelines.rerank.minimum_tools": [
        ("pruning", "policy", "rerank", "minimum_tools"),
        ("models", "rerankers", "minimum_tools"),
    ],
    "pipelines.llm.minimum_tools": [
        ("pruning", "policy", "llm", "minimum_tools"),
        ("models", "llm", "minimum_tools"),
    ],
    "pipelines.bm25.index_dir": [
        ("pruning", "bm25", "index_dir"),
        ("models", "bm25", "index_dir"),
    ],
}


def _nested_dict_value(root: dict[str, Any], *keys: str) -> object | None:
    if not keys:
        return None
    current: object = root
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def resolve_legacy(
    merged: dict[str, Any],
    user: dict[str, Any],
    legacy_name: str,
) -> object | None:
    """Return the first non-None value along legacy paths (user overlay, then merged)."""
    for keys in LEGACY_PATHS.get(legacy_name, []):
        value = _nested_dict_value(user, *keys)
        if value is not None:
            return value
    for keys in LEGACY_PATHS.get(legacy_name, []):
        value = _nested_dict_value(merged, *keys)
        if value is not None:
            return value
    return None


def resolve_user_then_merged(
    merged: dict[str, Any],
    user: dict[str, Any],
    *,
    canonical_keys: tuple[str, ...],
    legacy_name: str,
) -> object | None:
    """Prefer explicit user canonical keys, then user legacy, then merged canonical, then merged legacy."""
    user_canonical = _nested_dict_value(user, *canonical_keys)
    user_legacy = None
    for keys in LEGACY_PATHS.get(legacy_name, []):
        value = _nested_dict_value(user, *keys)
        if value is not None:
            user_legacy = value
            break

    if user_canonical is not None and user_legacy is None:
        return user_canonical
    if user_legacy is not None:
        return user_legacy
    if user_canonical is not None:
        return user_canonical

    merged_legacy = resolve_legacy(merged, {}, legacy_name)
    if merged_legacy is not None:
        return merged_legacy
    return _nested_dict_value(merged, *canonical_keys)
