"""App-owned SDK BM25 overrides."""

from __future__ import annotations

from cyt.config import (
    bm25_index_dir,
    bm25_mmap_enabled,
    bm25_stem_language,
    bm25_stopwords,
    load_config,
)


def configure_sdk_bm25_defaults(config: dict | None = None) -> None:
    """Push app BM25 settings into cyt-indexer (Rust core)."""
    from cyt_indexer.bm25_search import configure_bm25_defaults

    cfg = config or load_config()
    configure_bm25_defaults(
        index_dir=str(bm25_index_dir(cfg)),
        stem_language=bm25_stem_language(cfg),
        stopwords=bm25_stopwords(cfg),
        use_stopwords=True,
        mmap=bm25_mmap_enabled(cfg),
    )
