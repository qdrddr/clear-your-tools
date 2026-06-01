"""Reconstruct tool schemas from decomposed catalog data (Rust-backed core)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from cyt_indexer._native import load_catalog as _load_catalog
from cyt_indexer._native import retrieve_core as _retrieve_core
from cyt_indexer.paths import (
    JSON_EXT,
    get_root_tool_key,
    to_decomposed_key,
    tool_id_from_decomposed_rel,
)

if TYPE_CHECKING:
    from cyt_indexer.build import CatalogIndex

DECOMPOSED_SCORE: float = 0.5
ENUM_SCORE: float = 0.2


@dataclass
class DecomposedCatalog:
    """In-memory access to decomposed catalog JSON files."""

    _json_files: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_catalog_index(cls, index: CatalogIndex) -> DecomposedCatalog:
        json_files: dict[str, dict[str, Any]] = {}
        for rel_path, content in index.files.items():
            if rel_path.startswith("schemas/decomposed/") and rel_path.endswith(JSON_EXT):
                json_files[rel_path] = json.loads(content)
        return cls(_json_files=json_files)

    @classmethod
    def from_catalog_dict(cls, data: dict[str, Any]) -> DecomposedCatalog:
        json_files: dict[str, dict[str, Any]] = {}
        for entry in data.get("json", []):
            if not isinstance(entry, dict):
                continue
            file_path = entry.get("file_path")
            content = entry.get("content")
            if not isinstance(file_path, str) or not isinstance(content, dict):
                continue
            key = to_decomposed_key(file_path)
            if key is not None:
                json_files[key] = content
        return cls(_json_files=json_files)

    def resolve_key(self, file_path: str) -> str | None:
        candidates: list[str] = []
        normalized = to_decomposed_key(file_path)
        if normalized is not None:
            candidates.append(normalized)
        candidates.append(file_path)
        for candidate in candidates:
            if self.has_json(candidate):
                return candidate
        return None

    def has_json(self, key: str) -> bool:
        return key in self._json_files

    def get_json(self, key: str) -> dict[str, Any] | None:
        return self._json_files.get(key)


def load_catalog(dir_path: str) -> dict[str, list[dict[str, Any]]]:
    """Walk directory and build catalog dict for rerank/llm."""
    return _load_catalog(dir_path)


def _build_policy_options(  # noqa: C901
    *,
    policy_module: Any,
    catalog_dict: dict[str, Any],
    store: DecomposedCatalog,
    preserve_values: frozenset[str] | None,
    system_policy: str | None,
    mcp_policy: str | None,
) -> dict[str, Any] | None:
    if system_policy is None:
        system_policy = policy_module.system_tool_policy
    if mcp_policy is None:
        mcp_policy = policy_module.mcp_tool_policy

    system_preserve_set = policy_module.system_required_enum_values(catalog_dict)
    mcp_preserve_set = policy_module.mcp_required_enum_values(catalog_dict)
    required_by_tool_map = policy_module.required_enum_values_by_tool(catalog_dict)

    if preserve_values is not None and not system_preserve_set:
        system_preserve_set = preserve_values

    opts: dict[str, Any] = {}
    prune_optional_tools: list[str] = []
    for root_tool in {get_root_tool_key(k) for k in store._json_files}:
        if root_tool is None:
            continue
        tool_name = tool_id_from_decomposed_rel(root_tool)
        if policy_module.effective_policy(tool_name) == "prune_optional":
            prune_optional_tools.append(tool_name)
    if prune_optional_tools:
        opts["prune_optional_tools"] = prune_optional_tools

    system_preserve = sorted(system_preserve_set) if system_preserve_set else None
    if system_preserve is not None:
        opts["system_preserve"] = system_preserve

    mcp_preserve = sorted(mcp_preserve_set) if mcp_preserve_set else None
    if mcp_preserve is not None:
        opts["mcp_preserve"] = mcp_preserve

    if required_by_tool_map:
        opts["required_by_tool"] = {k: sorted(v) for k, v in required_by_tool_map.items()}

    return opts or None


def retrieve_tools(
    data: Any,
    *,
    catalog: DecomposedCatalog | CatalogIndex,
    apply_decomposed_score_filter: bool = True,
    preserve_values: frozenset[str] | None = None,
    system_policy: str | None = None,
    mcp_policy: str | None = None,
    policy_module: Any | None = None,
) -> list[dict[str, Any]]:
    """
    Reconstruct merged tool schemas from search/rerank/llm output.

    When ``policy_module`` is provided it must expose the same helpers as
    ``cyt.pruners.policies`` (used by clear-your-tools). Standalone callers
    can omit it and pass explicit preserve sets via future kwargs.
    """
    from cyt_indexer.build import CatalogIndex

    if isinstance(catalog, DecomposedCatalog):
        store = catalog
    elif isinstance(catalog, CatalogIndex):
        store = DecomposedCatalog.from_catalog_index(catalog)
    else:
        raise TypeError("catalog must be DecomposedCatalog or CatalogIndex")

    catalog_dict = data if isinstance(data, dict) else {}
    survivor_store = DecomposedCatalog.from_catalog_dict(catalog_dict)

    policy_options: dict[str, Any] | None = None
    if policy_module is not None:
        policy_options = _build_policy_options(
            policy_module=policy_module,
            catalog_dict=catalog_dict,
            store=store,
            preserve_values=preserve_values,
            system_policy=system_policy,
            mcp_policy=mcp_policy,
        )

    result = _retrieve_core(
        catalog_dict,
        store._json_files,
        survivor_store._json_files,
        apply_decomposed_score_filter,
        policy_options,
    )
    return list(result)
