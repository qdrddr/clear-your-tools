"""Local BM25 catalog pruning via cyt-indexer-sdk (Tantivy BM25)."""

from __future__ import annotations

import argparse
import json
import logging
import math
from typing import Any

from cyt.common.token_usage import StageTokenUsage, empty_usage
from cyt.config import (
    DEFAULT_BM25_PRUNE_ENUMS,
    DEFAULT_BM25_SCORE_TOOL,
    DEFAULT_BM25_SCORE_TOOL_ENUM,
    bm25_prune_enums,
    bm25_score_tool,
    bm25_score_tool_enum,
    load_config,
)
from cyt.indexer.bm25_search import bm25_catalog_fingerprint, bm25_score_catalog
from cyt.pruners.catalog_common import (
    finalize_catalog_result,
    load_pruner_catalog_input,
    prepare_catalog_for_scoring,
    prune_catalog_lists,
    resolve_policy_context,
)
from cyt.pruners.policies import (
    MCPToolPolicy,
    PolicyContext,
    SystemToolPolicy,
    configure_policies_from_config,
)

logger = logging.getLogger(__name__)

BM25_SCORE: float = DEFAULT_BM25_SCORE_TOOL
BM25_ENUM_SCORE: float = DEFAULT_BM25_SCORE_TOOL_ENUM
BM25_ENUMS: bool = DEFAULT_BM25_PRUNE_ENUMS
BM25_STATS_ID: str = "bm25"


def bm25_stage_usage() -> StageTokenUsage:
    """Stage usage metadata recorded in stats when BM25 pruning runs."""
    return StageTokenUsage(
        model_name=BM25_STATS_ID,
        provider_dns_name=BM25_STATS_ID,
        provider=BM25_STATS_ID,
        usage_source="local:bm25",
    )


class _LegacyTokenizer:
    """Compatibility stub; tokenization lives in Rust core."""

    stemmer: object = object()


def build_bm25_tokenizer(config: dict[str, Any] | None = None) -> _LegacyTokenizer:
    """Legacy compatibility — tokenizer is configured in Rust core."""
    del config
    return _LegacyTokenizer()


class Bm25Index:
    """Compatibility stub — scoring is stateless in Rust."""

    __slots__ = ("retriever",)

    def __init__(self) -> None:
        self.retriever = object()


def _configure_bm25_from_config(config: dict[str, Any]) -> None:
    from cyt.common.bm25_constants import configure_sdk_bm25_defaults

    configure_sdk_bm25_defaults(config)


def catalog_fingerprint(
    data: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> str:
    cfg = config or load_config()
    _configure_bm25_from_config(cfg)
    return bm25_catalog_fingerprint(data)


def build_or_load_index(
    data: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> Bm25Index | None:
    """Return compatibility handle; Tantivy persistence is handled in Rust scoring."""
    del data, config
    return Bm25Index()


def normalize_bm25_similarity(raw: float) -> float:
    """Map a raw BM25 score to absolute similarity in [0, 1]."""
    try:
        from cyt.indexer.bm25_search import exp_similarity

        return float(exp_similarity(raw))
    except ImportError:
        if raw <= 0.0:
            return 0.0
        return float(1.0 - math.exp(-raw))


def normalize_bm25_similarity_array(scores: list[float]) -> list[float]:
    """Map raw BM25 scores to absolute similarity in [0, 1]."""
    return [normalize_bm25_similarity(score) for score in scores]


def score_items(
    query: str,
    items: list[dict[str, Any]],
    index: Bm25Index,
    *,
    list_key: str,
) -> None:
    """Score catalog items in-place using Rust BM25."""
    del index
    if not items:
        return
    wrapper: dict[str, Any] = {"json": [], "md": []}
    wrapper[list_key] = items
    bm25_score_catalog(wrapper, query, prune_enums=False)


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
    return prune_catalog_lists(
        data,
        json_threshold=score_tool,
        md_threshold=score_tool_enum,
        prune_enums=prune_enums,
    )


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
    policy_ctx = resolve_policy_context(
        ctx=ctx,
        system_policy=system_policy,
        mcp_policy=mcp_policy,
        config=config,
    )
    data, pinned, skip_scoring = prepare_catalog_for_scoring(data, policy_ctx)
    if skip_scoring:
        return data, empty_usage()

    cfg = config or load_config()
    _configure_bm25_from_config(cfg)
    score_tool, score_tool_enum, prune_enums = _bm25_thresholds(cfg)

    build_or_load_index(data, config=cfg)

    scored = bm25_score_catalog(
        data,
        query,
        prune_json_threshold=score_tool if prune else None,
        prune_md_threshold=score_tool_enum if prune and prune_enums else None,
        prune_enums=prune and prune_enums,
    )
    data["json"] = scored.get("json", data.get("json"))
    data["md"] = scored.get("md", data.get("md"))

    return finalize_catalog_result(data, pinned, merge_pinned=merge_pinned), bm25_stage_usage()


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
    data = load_pruner_catalog_input(json_path=args.json, dir_path=args.dir)
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
