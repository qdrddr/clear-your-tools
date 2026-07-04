"""App-owned SDK runtime overrides (not defined in cyt-indexer-sdk)."""

from __future__ import annotations

from pathlib import Path

MD_EXT: str = ".md"
JSON_EXT: str = ".json"
DECOMPOSED_PREFIX: str = "schemas/decomposed/"
DECOMPOSED_ROOT = Path("schemas/decomposed")
CATALOG_PREFIX: str = "src/catalog"
BUILDER_MEMORY_ONLY: bool = False
DEFAULT_CATALOG_DIR = Path("catalog")
WRITE_CATALOG_PRUNE: bool = True


def configure_sdk_path_constants() -> None:
    """Push app runtime overrides into cyt-indexer (Python mirrors + Rust core)."""
    from cyt_core.bootstrap import configure_sdk_path_constants as _configure

    _configure()
