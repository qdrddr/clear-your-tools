"""Load proxy debug snapshots and decompose tools via cyt-indexer-sdk."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from cyt_indexer import CatalogIndex, build_catalog_index

REPO_ROOT = Path(__file__).resolve().parents[4]


def resolve_snapshot_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_file():
        return candidate
    from_repo = REPO_ROOT / path
    if from_repo.is_file():
        return from_repo
    msg = f"snapshot file not found: {path} (also tried {from_repo})"
    raise FileNotFoundError(msg)


def load_snapshot(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        msg = f"expected JSON object in {path}"
        raise TypeError(msg)
    return data


def _enums_from_md(md_entries: list[Any]) -> list[str]:
    enums: list[str] = []
    for entry in md_entries:
        if isinstance(entry, dict):
            content = entry.get("content")
            if isinstance(content, str):
                enums.append(content)
    return enums


def _survivor_catalog(stage: dict[str, Any]) -> dict[str, Any]:
    survivor: dict[str, Any] = {}
    for key in ("json", "md"):
        if key in stage:
            survivor[key] = stage[key]
    return survivor


def extract_snapshot_parts(
    data: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Return (build_tools, survivor_catalog, expected_tools)."""
    pruning = data.get("pruning") or {}
    stages = pruning.get("decomposed_catalog") or {}

    if "body" in data:
        expected = data["body"].get("tools") or []
        build_stage = stages.get("build_index") or {}
        survivor_stage = stages.get("rerank") or build_stage
    else:
        expected = data.get("tools") or []
        build_stage = stages.get("build_index") or {}
        survivor_stage = stages if stages.get("json") or stages.get("md") else build_stage

    build_tools = build_stage.get("tools") or []
    if not build_tools and expected:
        msg = (
            "snapshot has no pruning.decomposed_catalog.build_index.tools; "
            "cannot rebuild catalog index"
        )
        raise ValueError(msg)

    survivor = _survivor_catalog(survivor_stage)
    if not survivor.get("json") and not survivor.get("md"):
        msg = "snapshot has no rerank json/md entries for retrieve_tools"
        raise ValueError(msg)

    return build_tools, survivor, expected


def decompose_from_snapshot(data: dict[str, Any]) -> CatalogIndex:
    build_tools, _survivor, _expected = extract_snapshot_parts(data)
    pruning = data.get("pruning") or {}
    build_stage = (pruning.get("decomposed_catalog") or {}).get("build_index") or {}
    enums = _enums_from_md(build_stage.get("md", []))
    return build_catalog_index(build_tools, enums)


def catalog_dict_from_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    return decompose_from_snapshot(data).to_catalog_dict()


def write_output(catalog: dict[str, Any], output_path: str | Path | None) -> None:
    payload = json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"
    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
        return
    sys.stdout.write(payload)
