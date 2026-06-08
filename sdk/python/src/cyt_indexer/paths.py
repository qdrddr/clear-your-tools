"""Path helpers and runtime config (defaults and overrides live in Rust PathConfig)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cyt_indexer._native import (
    collect_enums as _collect_enums,
)
from cyt_indexer._native import (
    configure_path_constants as _configure_path_constants_native,
)
from cyt_indexer._native import (
    get_root_tool_key as _get_root_tool_key,
)
from cyt_indexer._native import (
    to_decomposed_key as _to_decomposed_key,
)
from cyt_indexer._native import (
    tool_id_from_decomposed_rel as _tool_id_from_decomposed_rel,
)


def configure_path_constants(
    *,
    md_ext: str,
    json_ext: str,
    decomposed_prefix: str,
    decomposed_root: str | Path,
    catalog_prefix: str,
    builder_memory_only: bool,
    default_catalog_dir: str | Path,
    write_catalog_prune: bool,
) -> None:
    """Push host app overrides into native PathConfig (Rust defaults when not called)."""
    _configure_path_constants_native(
        md_ext,
        json_ext,
        decomposed_prefix,
        str(decomposed_root),
        catalog_prefix,
        str(default_catalog_dir),
        (builder_memory_only, write_catalog_prune),
    )


def to_decomposed_key(file_path: str) -> str | None:
    return _to_decomposed_key(file_path)


def tool_id_from_decomposed_rel(rel_path: str) -> str:
    return _tool_id_from_decomposed_rel(rel_path)


def get_root_tool_key(file_path: str) -> str | None:
    return _get_root_tool_key(file_path)


def collect_enums(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return list(_collect_enums(schema))
