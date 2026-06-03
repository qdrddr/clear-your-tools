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
    from cyt_indexer.paths import configure_path_constants

    configure_path_constants(
        md_ext=MD_EXT,
        json_ext=JSON_EXT,
        decomposed_prefix=DECOMPOSED_PREFIX,
        decomposed_root=DECOMPOSED_ROOT,
        catalog_prefix=CATALOG_PREFIX,
        builder_memory_only=BUILDER_MEMORY_ONLY,
        default_catalog_dir=DEFAULT_CATALOG_DIR,
        write_catalog_prune=WRITE_CATALOG_PRUNE,
    )
