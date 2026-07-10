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
    path_builder_memory_only as _path_builder_memory_only,
)
from cyt_indexer._native import (
    path_catalog_prefix as _path_catalog_prefix,
)
from cyt_indexer._native import (
    path_decomposed_prefix as _path_decomposed_prefix,
)
from cyt_indexer._native import (
    path_decomposed_root as _path_decomposed_root,
)
from cyt_indexer._native import (
    path_default_catalog_dir as _path_default_catalog_dir,
)
from cyt_indexer._native import (
    path_json_ext as _path_json_ext,
)
from cyt_indexer._native import (
    path_md_ext as _path_md_ext,
)
from cyt_indexer._native import (
    path_write_catalog_prune as _path_write_catalog_prune,
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


def md_ext() -> str:
    return _path_md_ext()


def json_ext() -> str:
    return _path_json_ext()


def decomposed_prefix() -> str:
    return _path_decomposed_prefix()


def decomposed_root() -> str:
    return _path_decomposed_root()


def catalog_prefix() -> str:
    return _path_catalog_prefix()


def default_catalog_dir() -> str:
    return _path_default_catalog_dir()


def builder_memory_only() -> bool:
    return _path_builder_memory_only()


def write_catalog_prune() -> bool:
    return _path_write_catalog_prune()


def to_decomposed_key(file_path: str) -> str | None:
    return _to_decomposed_key(file_path)


def tool_id_from_decomposed_rel(rel_path: str) -> str:
    return _tool_id_from_decomposed_rel(rel_path)


def get_root_tool_key(file_path: str) -> str | None:
    return _get_root_tool_key(file_path)


def collect_enums(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return list(_collect_enums(schema))
