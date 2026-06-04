"""Shared decomposed-catalog debug formatting (Anthropic & OpenAI proxy kinds)."""

from __future__ import annotations

import copy
from typing import Any

from cyt.indexer.retrieve import removed_chunks

# Snapshot stages written by ``_run_pruning_pipeline`` (see ``anthropic._run_*_stage``).
DECOMPOSED_STAGE_ORDER: tuple[str, ...] = (
    "build_index",
    "rerank",
    "rerank_pruned",
    "bm25",
    "bm25_pruned",
    "llm",
)

_FINAL_SURVIVOR_STAGE_BASES: tuple[str, ...] = (
    "llm",
    "bm25_pruned",
    "rerank_pruned",
    "bm25",
    "rerank",
    "build_index",
)


def merge_decomposed_catalog_snapshots(
    existing: dict[str, dict[str, Any]] | None,
    new: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]] | None:
    """Merge catalog snapshots from multiple pruning passes (e.g. OpenAI tools + tool_search)."""
    if not new:
        return existing
    if not existing:
        return copy.deepcopy(new)

    merged = copy.deepcopy(existing)
    suffix = _next_catalog_pass_suffix(merged)
    for stage, catalog in new.items():
        merged[f"{stage}{suffix}"] = copy.deepcopy(catalog)
    return merged


def _next_catalog_pass_suffix(catalog_by_stage: dict[str, Any]) -> str:
    n = 2
    while any(k.endswith(f"#{n}") or f"#{n}" in k for k in catalog_by_stage):
        n += 1
    return f"#{n}"


def _ordered_catalog_stage_keys(catalog_by_stage: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for base in DECOMPOSED_STAGE_ORDER:
        if base in catalog_by_stage:
            keys.append(base)
        for n in range(2, 16):
            suffixed = f"{base}#{n}"
            if suffixed in catalog_by_stage:
                keys.append(suffixed)
    for key in sorted(catalog_by_stage):
        if key not in keys:
            keys.append(key)
    return keys


def _pruned_snapshot_key(scored_key: str) -> str:
    if "#" in scored_key:
        base, num = scored_key.split("#", 1)
        return f"{base}_pruned#{num}"
    return f"{scored_key}_pruned"


def _catalog_file_paths(catalog: dict[str, Any], key: str) -> list[str]:
    items = catalog.get(key)
    if not isinstance(items, list):
        return []
    paths: list[str] = []
    for item in items:
        if isinstance(item, dict):
            if path := item.get("file_path"):
                paths.append(str(path))
    return sorted(paths)


def _pass_suffix_from_build_key(build_key: str) -> str:
    if build_key == "build_index":
        return ""
    if build_key.startswith("build_index#"):
        return build_key[len("build_index") :]
    return ""


def _survivor_catalog_snapshot_for_pass(
    catalog_by_stage: dict[str, dict[str, Any]],
    *,
    pass_suffix: str = "",
) -> dict[str, Any] | None:
    for base in _FINAL_SURVIVOR_STAGE_BASES:
        key = f"{base}{pass_suffix}"
        catalog = catalog_by_stage.get(key)
        if isinstance(catalog, dict) and (catalog.get("json") or catalog.get("md")):
            return catalog
    return None


def _append_removed_paths_block(
    lines: list[str],
    *,
    label: str,
    removed: dict[str, Any],
) -> None:
    json_paths = _catalog_file_paths(removed, "json")
    md_paths = _catalog_file_paths(removed, "md")
    if not json_paths and not md_paths:
        return
    lines.append("")
    lines.append(f"{label}:")
    if json_paths:
        lines.append(f"  json removed ({len(json_paths)}):")
        lines.extend(f"    {p}" for p in json_paths)
    if md_paths:
        lines.append(f"  enum (md) removed ({len(md_paths)}):")
        lines.extend(f"    {p}" for p in md_paths)


def _prune_stage_pairs(
    catalog_by_stage: dict[str, dict[str, Any]],
) -> list[tuple[str, str, str]]:
    pairs: list[tuple[str, str, str]] = []
    for key in _ordered_catalog_stage_keys(catalog_by_stage):
        if key.endswith("_pruned") or "_pruned#" in key or key == "build_index":
            continue
        if key == "llm" or key.startswith("llm#"):
            continue
        pruned_key = _pruned_snapshot_key(key)
        if pruned_key not in catalog_by_stage:
            continue
        if key in ("rerank",) or key.startswith("rerank#"):
            label = f"{key} pruned away (score cutoff)"
        else:
            label = f"{key} pruned away"
        pairs.append((label, key, pruned_key))
    return pairs


def format_decomposed_table_lines(pruning: dict[str, Any]) -> list[str]:
    breakdown = pruning.get("decomposed_breakdown") or {}
    decomposed = pruning.get("decomposed") or {}
    catalog_by_stage = pruning.get("decomposed_catalog")
    stage_keys: list[str] = []
    if isinstance(catalog_by_stage, dict):
        stage_keys = _ordered_catalog_stage_keys(catalog_by_stage)
    for stage in DECOMPOSED_STAGE_ORDER:
        if (stage in breakdown or stage in decomposed) and stage not in stage_keys:
            stage_keys.append(stage)
    if not stage_keys:
        return ["Decomposed items: (none)"]

    rows: list[tuple[str, str, str]] = []
    for stage in stage_keys:
        if stage in breakdown:
            counts = breakdown[stage]
            json_n = str(counts.get("json", 0))
            md_n = str(counts.get("md", 0))
        elif isinstance(catalog_by_stage, dict) and stage in catalog_by_stage:
            snap = catalog_by_stage[stage]
            json_n = str(len(_catalog_file_paths(snap, "json")))
            md_n = str(len(_catalog_file_paths(snap, "md")))
        else:
            total = decomposed.get(stage.split("#")[0], "-")
            json_n = str(total)
            md_n = "-"
        rows.append((stage, json_n, md_n))

    col_stage = max(len("stage"), max(len(r[0]) for r in rows))
    col_json = max(len("json"), max(len(r[1]) for r in rows))
    col_md = max(len("enum (md)"), max(len(r[2]) for r in rows))
    header = f"{'stage':<{col_stage}}  {'json':>{col_json}}  {'enum (md)':>{col_md}}"
    sep = f"{'-' * col_stage}  {'-' * col_json}  {'-' * col_md}"
    body = [
        f"{stage:<{col_stage}}  {json_n:>{col_json}}  {md_n:>{col_md}}"
        for stage, json_n, md_n in rows
    ]
    return ["Decomposed items:", header, sep, *body]


def format_decomposed_paths_lines(pruning: dict[str, Any]) -> list[str]:
    catalog_by_stage = pruning.get("decomposed_catalog")
    if not isinstance(catalog_by_stage, dict) or not catalog_by_stage:
        return []

    lines: list[str] = [""]
    for stage in _ordered_catalog_stage_keys(catalog_by_stage):
        catalog = catalog_by_stage.get(stage)
        if not isinstance(catalog, dict):
            continue
        json_paths = _catalog_file_paths(catalog, "json")
        md_paths = _catalog_file_paths(catalog, "md")
        lines.append(f"{stage} json ({len(json_paths)}):")
        lines.extend(f"  {p}" for p in json_paths)
        lines.append(f"{stage} enum (md) ({len(md_paths)}):")
        lines.extend(f"  {p}" for p in md_paths)
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def format_removed_chunks_lines(pruning: dict[str, Any]) -> list[str]:
    catalog_by_stage = pruning.get("decomposed_catalog")
    if not isinstance(catalog_by_stage, dict) or not catalog_by_stage:
        return []

    lines: list[str] = []
    for label, full_key, surviving_key in _prune_stage_pairs(catalog_by_stage):
        full = catalog_by_stage.get(full_key)
        surviving = catalog_by_stage.get(surviving_key)
        if not isinstance(full, dict) or not isinstance(surviving, dict):
            continue
        _append_removed_paths_block(
            lines,
            label=label,
            removed=removed_chunks(full, surviving),
        )

    for build_key in _ordered_catalog_stage_keys(catalog_by_stage):
        if build_key != "build_index" and not build_key.startswith("build_index#"):
            continue
        build = catalog_by_stage.get(build_key)
        if not isinstance(build, dict):
            continue
        pass_suffix = _pass_suffix_from_build_key(build_key)
        final = _survivor_catalog_snapshot_for_pass(catalog_by_stage, pass_suffix=pass_suffix)
        if final is None or final is build:
            continue
        label = (
            "removed since build_index (final survivors)"
            if not pass_suffix
            else f"removed since build_index{pass_suffix} (final survivors)"
        )
        _append_removed_paths_block(
            lines,
            label=label,
            removed=removed_chunks(build, final),
        )

    if not lines:
        return []
    return ["", "Decomposed removed (non-surviving chunks):", *lines]
