"""Tantivy BM25 catalog search via Rust core."""

from __future__ import annotations

from typing import Any

from cyt_indexer._native import batch_reconstruct_skill_matches as _batch_reconstruct_native
from cyt_indexer._native import bm25_catalog_fingerprint as _bm25_catalog_fingerprint_native
from cyt_indexer._native import bm25_frontmatter_gate as _bm25_frontmatter_gate_native
from cyt_indexer._native import bm25_score_catalog as _bm25_score_catalog_native
from cyt_indexer._native import bm25_search_skill_chunks as _bm25_search_skill_chunks_native
from cyt_indexer._native import configure_bm25_defaults as _configure_bm25_defaults_native
from cyt_indexer._native import exp_similarity as _exp_similarity_native
from cyt_indexer._native import greedy_select_skill_items as _greedy_select_native

__all__ = [
    "batch_reconstruct_skill_matches",
    "bm25_catalog_fingerprint",
    "bm25_frontmatter_gate",
    "bm25_score_catalog",
    "bm25_search_skill_chunks",
    "configure_bm25_defaults",
    "exp_similarity",
    "greedy_select_skill_items",
]


def configure_bm25_defaults(
    *,
    index_dir: str | None = None,
    stem_language: str | None = None,
    stopwords: str | None = None,
    use_stopwords: bool | None = None,
    k1: float | None = None,
    b: float | None = None,
    mmap: bool | None = None,
) -> None:
    """Override SDK BM25 search defaults in native core."""
    _configure_bm25_defaults_native(
        index_dir=index_dir,
        stem_language=stem_language,
        stopwords=stopwords,
        use_stopwords=use_stopwords,
        k1=k1,
        b=b,
        mmap=mmap,
    )


def bm25_catalog_fingerprint(data: dict[str, Any]) -> str:
    """Hash catalog documents plus analyzer settings."""
    return str(_bm25_catalog_fingerprint_native(data))


def bm25_score_catalog(
    data: dict[str, Any],
    query: str,
    *,
    prune_json_threshold: float | None = None,
    prune_md_threshold: float | None = None,
    prune_enums: bool = True,
) -> dict[str, Any]:
    """Score catalog json/md lists in-place and return the catalog dict."""
    result = _bm25_score_catalog_native(
        data,
        query,
        prune_json_threshold=prune_json_threshold,
        prune_md_threshold=prune_md_threshold,
        prune_enums=prune_enums,
    )
    return dict(result) if isinstance(result, dict) else result


def bm25_frontmatter_gate(
    entries: list[dict[str, Any]],
    query: str,
    *,
    upper_limit: float = 0.4,
) -> dict[str, Any]:
    """Return excluded entry refs and trace metadata."""
    result = _bm25_frontmatter_gate_native(entries, query, upper_limit=upper_limit)
    return dict(result) if isinstance(result, dict) else result


def bm25_search_skill_chunks(
    entries: list[dict[str, Any]],
    query: str,
    *,
    threshold: float = 0.5,
    excluded: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Search skill chunks, reconstruct matches, return matches + trace."""
    result = _bm25_search_skill_chunks_native(
        entries,
        query,
        threshold=threshold,
        excluded=excluded,
    )
    return dict(result) if isinstance(result, dict) else result


def exp_similarity(raw: float) -> float:
    """Map a raw BM25 score to absolute similarity in [0, 1]."""
    return float(_exp_similarity_native(raw))


def batch_reconstruct_skill_matches(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct multiple skill doc groups in one native call."""
    result = _batch_reconstruct_native(groups)
    if isinstance(result, list):
        return [dict(item) if isinstance(item, dict) else item for item in result]
    return []


def greedy_select_skill_items(
    survivors: list[dict[str, Any]],
    *,
    item_kind: str = "node",
    max_tokens: int = 0,
) -> dict[str, Any]:
    """Greedy budget selection over skill search survivors in native core."""
    result = _greedy_select_native(survivors, item_kind, int(max_tokens))
    return dict(result) if isinstance(result, dict) else {}
