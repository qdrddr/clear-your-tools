"""LLM, reranker, and BM25 catalog pruners."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "RelevantChunkIds",
    "apply_selector_ids_to_catalog",
    "bm25_catalog_dict",
    "bm25_stage_usage",
    "call_llm",
    "extract_skill_node_document",
    "llm_catalog_dict",
    "llm_select_ids",
    "prepare_catalog_selector_chunks",
    "prune_bm25_catalog",
    "prune_reranked_catalog",
    "prune_reranked_skill_items",
    "rerank_catalog_dict",
    "rerank_items",
    "rerank_unified_item_lists",
    "trim_catalog_dict",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "RelevantChunkIds": ("cyt.pruners.llm", "RelevantChunkIds"),
    "apply_selector_ids_to_catalog": ("cyt.pruners.llm", "apply_selector_ids_to_catalog"),
    "call_llm": ("cyt.pruners.llm", "call_llm"),
    "llm_catalog_dict": ("cyt.pruners.llm", "llm_catalog_dict"),
    "llm_select_ids": ("cyt.pruners.llm", "llm_select_ids"),
    "prepare_catalog_selector_chunks": ("cyt.pruners.llm", "prepare_catalog_selector_chunks"),
    "trim_catalog_dict": ("cyt.pruners.llm", "trim_catalog_dict"),
    "prune_reranked_catalog": ("cyt.pruners.rerank", "prune_reranked_catalog"),
    "prune_reranked_skill_items": ("cyt.pruners.rerank", "prune_reranked_skill_items"),
    "rerank_catalog_dict": ("cyt.pruners.rerank", "rerank_catalog_dict"),
    "rerank_items": ("cyt.pruners.rerank", "rerank_items"),
    "rerank_unified_item_lists": ("cyt.pruners.rerank", "rerank_unified_item_lists"),
    "bm25_catalog_dict": ("cyt.pruners.bm25", "bm25_catalog_dict"),
    "bm25_stage_usage": ("cyt.pruners.bm25", "bm25_stage_usage"),
    "prune_bm25_catalog": ("cyt.pruners.bm25", "prune_bm25_catalog"),
    "extract_skill_node_document": ("cyt.pruners.documents", "extract_skill_node_document"),
}


def __getattr__(name: str) -> object:
    if name == "litellm_quiet":
        return __import__("cyt.pruners.litellm_quiet", fromlist=["configure_litellm_quiet"])
    if name in _LAZY_EXPORTS:
        module_name, attr_name = _LAZY_EXPORTS[name]
        module = __import__(module_name, fromlist=[attr_name])
        return getattr(module, attr_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if TYPE_CHECKING:
    from types import ModuleType

    litellm_quiet: ModuleType
    from cyt.pruners.bm25 import bm25_catalog_dict, bm25_stage_usage, prune_bm25_catalog
    from cyt.pruners.documents import extract_skill_node_document
    from cyt.pruners.llm import (
        RelevantChunkIds,
        apply_selector_ids_to_catalog,
        call_llm,
        llm_catalog_dict,
        llm_select_ids,
        prepare_catalog_selector_chunks,
        trim_catalog_dict,
    )
    from cyt.pruners.rerank import (
        prune_reranked_catalog,
        prune_reranked_skill_items,
        rerank_catalog_dict,
        rerank_items,
        rerank_unified_item_lists,
    )
