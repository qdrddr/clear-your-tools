"""SDK bootstrap — configure cyt-indexer runtime defaults."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

__all__ = [
    "AppContext",
    "bootstrap",
    "configure_sdk_bm25_defaults",
    "configure_sdk_path_constants",
    "configure_sdk_runtime_defaults",
    "configure_sdk_tokenizer_defaults",
]

MD_EXT: str = ".md"
JSON_EXT: str = ".json"
DECOMPOSED_PREFIX: str = "schemas/decomposed/"
DECOMPOSED_ROOT = Path("schemas/decomposed")
CATALOG_PREFIX: str = "src/catalog"
BUILDER_MEMORY_ONLY: bool = False
DEFAULT_CATALOG_DIR = Path("catalog")
WRITE_CATALOG_PRUNE: bool = True

DECOMPOSED_SCORE: float = 0.5
ENUM_SCORE: float = 0.2
RERANK_SCORE: float = 0.003
EMPTY_OPTIONAL_FALLBACK_K: int = 3
DEFAULT_SYSTEM_TOOL_POLICY = "prune_optional"
DEFAULT_MCP_TOOL_POLICY = "prune_all"


@dataclass(frozen=True)
class AppContext:
    """Configured CYT application context after bootstrap."""

    version: str


def _package_version() -> str:
    try:
        return version("clear-your-tools")
    except PackageNotFoundError:
        return "0.0.0"


def configure_sdk_path_constants() -> None:
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


def configure_sdk_runtime_defaults() -> None:
    from cyt_indexer.runtime_defaults import configure_runtime_defaults

    configure_runtime_defaults(
        decomposed_score=DECOMPOSED_SCORE,
        enum_score=ENUM_SCORE,
        rerank_score=RERANK_SCORE,
        empty_optional_fallback_k=EMPTY_OPTIONAL_FALLBACK_K,
        default_system_policy=DEFAULT_SYSTEM_TOOL_POLICY,
        default_mcp_policy=DEFAULT_MCP_TOOL_POLICY,
    )


def configure_sdk_tokenizer_defaults() -> None:
    from cyt_indexer.tokens import configure_tokenizer_defaults

    configure_tokenizer_defaults(encoding="cl100k_base", allowed_special="all")


def configure_sdk_bm25_defaults(config: dict | None = None) -> None:
    from cyt_indexer.bm25_search import configure_bm25_defaults

    if config is None:
        configure_bm25_defaults(use_stopwords=True, mmap=False)
        return

    from cyt.config import (
        bm25_index_dir,
        bm25_mmap_enabled,
        bm25_stem_language,
        bm25_stopwords,
    )

    configure_bm25_defaults(
        index_dir=str(bm25_index_dir(config)),
        stem_language=bm25_stem_language(config),
        stopwords=bm25_stopwords(config),
        use_stopwords=True,
        mmap=bm25_mmap_enabled(config),
    )


def bootstrap(*, config: dict | None = None) -> AppContext:
    """Apply SDK runtime overrides and return the configured app context."""
    configure_sdk_path_constants()
    configure_sdk_runtime_defaults()
    configure_sdk_tokenizer_defaults()
    configure_sdk_bm25_defaults(config)
    return AppContext(version=_package_version())
