"""Local BM25 catalog pruning with mmap indexes under ~/.config/cyt/bm25/."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import bm25s
import numpy as np
import Stemmer
from bm25s.tokenization import Tokenizer

from cyt.common.token_usage import StageTokenUsage, empty_usage
from cyt.config import (
    DEFAULT_BM25_PRUNE_ENUMS,
    DEFAULT_BM25_SCORE_TOOL,
    DEFAULT_BM25_SCORE_TOOL_ENUM,
    bm25_index_dir,
    bm25_mmap_enabled,
    bm25_prune_enums,
    bm25_score_tool,
    bm25_score_tool_enum,
    bm25_stem_language,
    bm25_stopwords,
    load_config,
)
from cyt.indexer.build import catalog_tool_count
from cyt.pruners.documents import extract_json_catalog_document, extract_md_catalog_document
from cyt.pruners.policies import (
    MCPToolPolicy,
    PolicyContext,
    SystemToolPolicy,
    catalog_needs_partition,
    configure_policies_from_config,
    full_pass_through,
    merge_catalog,
    partition_catalog,
    policy_context_from_config,
)

logger = logging.getLogger(__name__)

BM25_SCORE: float = DEFAULT_BM25_SCORE_TOOL
BM25_ENUM_SCORE: float = DEFAULT_BM25_SCORE_TOOL_ENUM
BM25_ENUMS: bool = DEFAULT_BM25_PRUNE_ENUMS
BM25_STATS_ID: str = "bm25"
_MANIFEST_NAME = "manifest.json"


def bm25_stage_usage() -> StageTokenUsage:
    """Stage usage metadata recorded in stats when BM25 pruning runs."""
    return StageTokenUsage(
        model_name=BM25_STATS_ID,
        provider_dns_name=BM25_STATS_ID,
        provider=BM25_STATS_ID,
        usage_source="local:bm25",
    )


def build_bm25_tokenizer(config: dict[str, Any] | None = None) -> Tokenizer:
    """Create a BM25 tokenizer with PyStemmer and configured stopwords."""
    cfg = config or load_config()
    language = bm25_stem_language(cfg)
    stemmer = Stemmer.Stemmer(language)
    return Tokenizer(
        lower=True,
        stopwords=bm25_stopwords(cfg),
        stemmer=stemmer.stemWord,
    )


def _catalog_documents(data: dict[str, Any]) -> list[tuple[str, str, str, int]]:
    """Return sorted (list_key, file_path, text, item_index) for fingerprinting and indexing."""
    docs: list[tuple[str, str, str, int]] = []
    for list_key in ("json", "md"):
        items = data.get(list_key)
        if not isinstance(items, list):
            continue
        extract_fn = (
            extract_json_catalog_document if list_key == "json" else extract_md_catalog_document
        )
        for item_index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            catalog_item = cast(dict[str, Any], item)
            text = extract_fn(catalog_item)
            if not text:
                continue
            file_path = str(catalog_item.get("file_path", ""))
            docs.append((list_key, file_path, text, item_index))
    docs.sort(key=lambda row: (row[0], row[1], row[2]))
    return docs


def catalog_fingerprint(
    data: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> str:
    """Hash catalog documents plus tokenizer settings for index cache invalidation."""
    cfg = config or load_config()
    stem_language = bm25_stem_language(cfg)
    stopwords = bm25_stopwords(cfg)
    docs = _catalog_documents(data)
    hasher = hashlib.sha256()
    hasher.update(stem_language.encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(stopwords.encode("utf-8"))
    hasher.update(b"\0")
    for list_key, file_path, text, _item_index in docs:
        hasher.update(list_key.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(file_path.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(text.encode("utf-8"))
        hasher.update(b"\0")
    return hasher.hexdigest()


def prepare_bm25_documents(
    items: list[dict[str, Any]],
    extract_fn: Callable[[dict[str, Any]], str | None],
) -> list[tuple[int, str]]:
    """Build (item_index, text) pairs for items with extractable document text."""
    indexed: list[tuple[int, str]] = []
    for item_index, item in enumerate(items):
        item["score"] = f"{0.0:.20f}"
        if text := extract_fn(item):
            indexed.append((item_index, text))
    return indexed


def _index_dir_for_fingerprint(fingerprint: str, config: dict[str, Any] | None = None) -> Path:
    return bm25_index_dir(config) / fingerprint


def _write_manifest(
    index_dir: Path,
    *,
    fingerprint: str,
    tool_count: int,
    stem_language: str,
    stopwords: str,
    doc_count: int,
) -> None:
    manifest = {
        "fingerprint": fingerprint,
        "tool_count": tool_count,
        "stem_language": stem_language,
        "stopwords": stopwords,
        "doc_count": doc_count,
        "created_at": datetime.now(tz=UTC).isoformat(),
    }
    (index_dir / _MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _index_is_complete(index_dir: Path) -> bool:
    required = (
        "data.csc.index.npy",
        "indices.csc.index.npy",
        "indptr.csc.index.npy",
        "vocab.index.json",
        "params.index.json",
        "vocab.tokenizer.json",
        _MANIFEST_NAME,
    )
    return all((index_dir / name).exists() for name in required)


class Bm25Index:
    """Loaded BM25 retriever, tokenizer, and document-to-item mapping."""

    __slots__ = ("doc_mapping", "retriever", "tokenizer")

    def __init__(
        self,
        retriever: bm25s.BM25,
        tokenizer: Tokenizer,
        doc_mapping: list[dict[str, Any]],
    ) -> None:
        self.retriever = retriever
        self.tokenizer = tokenizer
        self.doc_mapping = doc_mapping


def build_or_load_index(
    data: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> Bm25Index | None:
    """Load mmap index from disk or build and persist a new one."""
    cfg = config or load_config()
    fingerprint = catalog_fingerprint(data, config=cfg)
    index_dir = _index_dir_for_fingerprint(fingerprint, cfg)
    stem_language = bm25_stem_language(cfg)
    stopwords = bm25_stopwords(cfg)
    mmap = bm25_mmap_enabled(cfg)
    tokenizer = build_bm25_tokenizer(cfg)

    docs = _catalog_documents(data)
    if not docs:
        return None

    doc_mapping: list[dict[str, Any]] = []
    corpus_entries: list[dict[str, Any]] = []
    for list_key, _file_path, text, item_index in docs:
        mapping = {"list_key": list_key, "item_index": item_index}
        doc_mapping.append(mapping)
        corpus_entries.append({"text": text, **mapping})

    if _index_is_complete(index_dir):
        retriever = bm25s.BM25.load(str(index_dir), mmap=mmap, load_corpus=True)
        tokenizer.load_vocab(str(index_dir))
        return Bm25Index(retriever, tokenizer, doc_mapping)

    texts = [entry["text"] for entry in corpus_entries]
    corpus_tokens = tokenizer.tokenize(texts, return_as="tuple")
    retriever = bm25s.BM25(corpus=corpus_entries)
    retriever.index(corpus_tokens)

    index_dir.mkdir(parents=True, exist_ok=True)
    retriever.save(str(index_dir), corpus=corpus_entries)
    tokenizer.save_vocab(str(index_dir))
    _write_manifest(
        index_dir,
        fingerprint=fingerprint,
        tool_count=catalog_tool_count(data),
        stem_language=stem_language,
        stopwords=stopwords,
        doc_count=len(corpus_entries),
    )

    if mmap:
        retriever = bm25s.BM25.load(str(index_dir), mmap=True, load_corpus=True)
        tokenizer = build_bm25_tokenizer(cfg)
        tokenizer.load_vocab(str(index_dir))

    return Bm25Index(retriever, tokenizer, doc_mapping)


def _normalize_scores(scores: np.ndarray) -> np.ndarray:
    if scores.size == 0:
        return scores
    max_s = float(scores.max())
    min_s = float(scores.min())
    if max_s > min_s:
        return (scores - min_s) / (max_s - min_s)
    return np.zeros_like(scores, dtype=float)


def _query_token_ids(tokenizer: Tokenizer, query: str) -> list[int]:
    tokenized = tokenizer.tokenize([query], update_vocab=False, return_as="ids")
    if not isinstance(tokenized, list) or not tokenized:
        return []
    query_tokens = tokenized[0]
    if not isinstance(query_tokens, list):
        return []
    return [token_id for token_id in query_tokens if isinstance(token_id, int)]


def score_items(
    query: str,
    items: list[dict[str, Any]],
    index: Bm25Index,
    *,
    list_key: str,
) -> None:
    """Score catalog items in-place using a shared BM25 index."""
    if not items or not index.doc_mapping:
        return

    query_ids = _query_token_ids(index.tokenizer, query)
    if not query_ids:
        return

    all_scores = index.retriever.get_scores_from_ids(query_ids)
    normalized = _normalize_scores(all_scores)

    for doc_idx, mapping in enumerate(index.doc_mapping):
        if mapping.get("list_key") != list_key:
            continue
        item_index = mapping.get("item_index")
        if not isinstance(item_index, int) or item_index >= len(items):
            continue
        score = float(normalized[doc_idx])
        items[item_index]["score"] = f"{score:.20f}"

    items.sort(key=lambda x: float(x.get("score", 0)), reverse=True)


def _bm25_thresholds(config: dict[str, Any] | None = None) -> tuple[float, float, bool]:
    cfg = config or load_config()
    return (
        bm25_score_tool(cfg),
        bm25_score_tool_enum(cfg),
        bm25_prune_enums(cfg),
    )


def prune_bm25_catalog(
    data: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Drop catalog items below BM25 score thresholds."""
    score_tool, score_tool_enum, prune_enums = _bm25_thresholds(config)
    json_items = data.get("json")
    if isinstance(json_items, list):
        data["json"] = [item for item in json_items if float(item.get("score", 0)) >= score_tool]

    if prune_enums:
        md_items = data.get("md")
        if isinstance(md_items, list):
            data["md"] = [
                item for item in md_items if float(item.get("score", 0)) >= score_tool_enum
            ]

    return data


def bm25_catalog_dict(
    data: dict[str, Any],
    query: str,
    *,
    prune: bool = True,
    ctx: PolicyContext | None = None,
    system_policy: SystemToolPolicy | None = None,
    mcp_policy: MCPToolPolicy | None = None,
    merge_pinned: bool = True,
    config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], StageTokenUsage]:
    """Score in-place data['json'] and optionally data['md']; optionally prune by score."""
    policy_ctx = ctx
    if policy_ctx is None and (system_policy is not None or mcp_policy is not None):
        policy_ctx = policy_context_from_config(system=system_policy, mcp=mcp_policy, config=config)

    if policy_ctx is not None and full_pass_through(policy_ctx):
        return data, empty_usage()

    cfg = config or load_config()
    pinned: dict[str, Any] = {}
    if policy_ctx is not None and catalog_needs_partition(data, policy_ctx):
        data, pinned = partition_catalog(data, policy_ctx)

    index = build_or_load_index(data, config=cfg)
    if index is None:
        logger.info("bm25 pruning skipped: no indexable documents")
        if merge_pinned and pinned:
            data = merge_catalog(data, pinned)
        return data, empty_usage()

    if isinstance(data.get("json"), list):
        prepare_bm25_documents(data["json"], extract_json_catalog_document)
        score_items(query, data["json"], index, list_key="json")

    _, _, prune_enums = _bm25_thresholds(cfg)
    if prune_enums and isinstance(data.get("md"), list):
        prepare_bm25_documents(data["md"], extract_md_catalog_document)
        score_items(query, data["md"], index, list_key="md")

    if prune:
        data = prune_bm25_catalog(data, config=cfg)

    if merge_pinned and pinned:
        data = merge_catalog(data, pinned)

    return data, bm25_stage_usage()


def main() -> None:
    parser = argparse.ArgumentParser(description="BM25-prune catalog chunks locally.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--json", help="Input JSON file path")
    group.add_argument("--dir", help="Path to the directory containing decomposed tool files")
    parser.add_argument("--output-json", help="Optional output JSON file path")
    parser.add_argument(
        "command",
        choices=["search"],
        nargs="?",
        default="search",
        help="Command to run (default: search)",
    )
    parser.add_argument("query", help="Search query")

    args = parser.parse_args()
    ctx = configure_policies_from_config()

    if args.json:
        try:
            with open(args.json) as f:
                data = json.load(f)
        except Exception as e:
            print(f"Error reading JSON: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        from cyt.indexer.retrieve import load_catalog

        try:
            data = load_catalog(args.dir)
        except Exception as e:
            print(f"Error loading catalog directory: {e}", file=sys.stderr)
            sys.exit(1)

    data, _tokens = bm25_catalog_dict(data, args.query, ctx=ctx)

    output_data = json.dumps(data, indent=2)
    if args.output_json:
        with open(args.output_json, "w") as f:
            f.write(output_data)
        print(f"Results saved to {args.output_json}")
    else:
        print(output_data)


if __name__ == "__main__":
    main()
