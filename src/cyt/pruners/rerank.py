import argparse
import json
import logging
import sys
from collections.abc import Callable
from typing import Any, cast

from litellm import rerank

from cyt.common.token_usage import TIKTOKEN_CL100K, StageTokenUsage, empty_usage
from cyt.config import (
    load_config,
    require_proxy_env,
    rerank_score_skills,
    reranker_minimum_tools,
)
from cyt.indexer.tokens import count_json_tokens, log_token_usage
from cyt.pruners.catalog_common import (
    catalog_below_minimum_tools,
    finalize_catalog_result,
    load_pruner_catalog_input,
    prepare_catalog_for_scoring,
    prepare_indexed_documents,
    prune_catalog_lists,
    resolve_policy_context,
)
from cyt.pruners.documents import (
    extract_json_catalog_document,
    extract_md_catalog_document,
)
from cyt.pruners.litellm_quiet import configure_litellm_quiet
from cyt.pruners.policies import (
    RERANK_SCORE,
    MCPToolPolicy,
    PolicyContext,
    SystemToolPolicy,
    configure_policies_from_config,
)
from cyt.pruners.remote import RerankPruningSettings, resolve_remote_pruning_settings
from cyt.pruners.split import split_into_bulks

logger = logging.getLogger(__name__)
RERANK_ENUMS: bool = True
RERANK_ENUM_SCORE: float = 0.0001


def rerank_pruning_settings(
    config: dict[str, Any] | None = None,
    *,
    settings: RerankPruningSettings | None = None,
) -> RerankPruningSettings:
    """Resolve pruning reranker model from pipeline config."""
    if settings is not None:
        return settings
    return resolve_remote_pruning_settings(
        config=config,
        model_kind="rerankers",
        pipeline_name="rerank",
        missing_nick_message=(
            "pruning.tools.pipelines.rerank.model_nick is required for rerank pruning"
        ),
        derive_dns_from_base_url=True,
    )


def process_response(
    response: object,
    valid_indices: list[int],
    items: list[dict[str, Any]],
) -> None:
    """Processes the rerank response and updates item scores."""
    # LiteLLM's rerank response usually has a 'results' attribute or key
    results_list: list[Any] = []
    results_attr = getattr(response, "results", None)
    if results_attr is not None:
        results_list = list(cast(Any, results_attr))
    elif isinstance(response, dict):
        resp_dict = cast(dict[str, Any], response)
        if "results" in resp_dict:
            results_list = cast(list[Any], resp_dict["results"])
    elif isinstance(response, list):
        results_list = list(response)

    for result in results_list:
        try:
            # Try attribute access first
            doc_idx = getattr(result, "index", None)
            relevance_score = getattr(result, "relevance_score", None)

            # Fallback to dictionary access
            if doc_idx is None and isinstance(result, dict):
                doc_idx = result["index"]
            if relevance_score is None and isinstance(result, dict):
                relevance_score = result["relevance_score"]

            if doc_idx is None or relevance_score is None:
                continue
            original_idx = valid_indices[int(doc_idx)]
            # Store as string with 20 decimal places to avoid scientific notation in JSON
            items[original_idx]["score"] = f"{relevance_score:.20f}"
        except (KeyError, TypeError, IndexError) as e:
            print(f"Debug: Error processing result {result}: {e}", file=sys.stderr)
            continue


def rerank_bulk_base_tokens(query: str) -> int:
    """Tiktoken budget reserved per rerank bulk (query + empty documents payload)."""
    return count_json_tokens({"query": query, "documents": []})


def count_rerank_request_tokens(query: str, documents: list[str]) -> int:
    """Estimate input tokens sent to the rerank API for one request."""
    return count_json_tokens({"query": query, "documents": documents})


def _rerank_single_bulk(
    bulk: list[tuple[int, str]],
    *,
    query: str,
    settings: RerankPruningSettings,
    items: list[dict[str, Any]],
) -> tuple[StageTokenUsage, bool, Exception | None]:
    bulk_indices = [x[0] for x in bulk]
    bulk_docs = [x[1] for x in bulk]
    bulk_tokens = count_rerank_request_tokens(query, bulk_docs)
    logger.info(
        "rerank request tokens: %d (query + %d documents)",
        bulk_tokens,
        len(bulk_docs),
    )
    usage = StageTokenUsage(
        input_tokens=bulk_tokens,
        output_tokens=0,
        usage_source=TIKTOKEN_CL100K,
        model_name=settings.model_name,
        provider_dns_name=settings.provider_dns,
        provider=settings.provider,
    )
    rerank_kwargs: dict[str, Any] = {
        "model": settings.model_name,
        "query": query,
        "documents": bulk_docs,
        "api_key": settings.api_key,
    }
    if settings.base_url:
        rerank_kwargs["api_base"] = settings.base_url

    try:
        response = rerank(**rerank_kwargs)
        process_response(response, bulk_indices, items)
        return usage, True, None
    except Exception as bulk_exc:
        print(f"Error during reranking bulk: {bulk_exc}", file=sys.stderr)
        return usage, False, bulk_exc


def _rerank_prepared_bulks(
    indexed_docs: list[tuple[int, str]],
    *,
    query: str,
    settings: RerankPruningSettings,
    items: list[dict[str, Any]],
    base_tokens: int,
    min_score: float | None,
) -> tuple[list[dict[str, Any]], StageTokenUsage]:
    total_usage = empty_usage()
    bulks = split_into_bulks(
        items=indexed_docs,
        transform_fn=lambda x: x[1],
        base_tokens=base_tokens,
    )

    bulk_errors: list[Exception] = []
    any_success = False
    for bulk in bulks:
        bulk_usage, success, bulk_exc = _rerank_single_bulk(
            bulk,
            query=query,
            settings=settings,
            items=items,
        )
        total_usage = total_usage.merge(bulk_usage)
        if success:
            any_success = True
        elif bulk_exc is not None:
            bulk_errors.append(bulk_exc)

    if not any_success and bulk_errors:
        raise RuntimeError(
            f"All rerank bulks failed ({len(bulk_errors)}): {bulk_errors[-1]}",
        ) from bulk_errors[-1]

    items.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
    if min_score is not None:
        return [item for item in items if float(item.get("score", 0)) >= min_score], total_usage
    return items, total_usage


def prune_reranked_skill_items(
    items: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Drop skill node items below the configured rerank skills score threshold."""
    threshold = rerank_score_skills(config)
    return [item for item in items if float(item.get("score", 0)) >= threshold]


def rerank_unified_item_lists(
    query: str,
    targets: list[tuple[list[dict[str, Any]], Callable[[dict[str, Any]], str | None]]],
    settings: RerankPruningSettings,
) -> StageTokenUsage:
    """Score multiple item lists in shared rerank bulks; write scores back in place."""
    segments: list[tuple[list[dict[str, Any]], int, dict[str, Any]]] = []
    indexed_docs: list[tuple[int, str]] = []
    shadow_items: list[dict[str, Any]] = []

    for items, extract_fn in targets:
        for item_idx, item in enumerate(items):
            item["score"] = f"{0.0:.20f}"
            if not (doc_text := extract_fn(item)):
                continue
            shadow_idx = len(shadow_items)
            shadow = {"score": item["score"]}
            shadow_items.append(shadow)
            segments.append((items, item_idx, shadow))
            indexed_docs.append((shadow_idx, doc_text))

    if not indexed_docs:
        return empty_usage()

    base_tokens = rerank_bulk_base_tokens(query)
    _, usage = _rerank_prepared_bulks(
        indexed_docs,
        query=query,
        settings=settings,
        items=shadow_items,
        base_tokens=base_tokens,
        min_score=None,
    )

    for _items, item_idx, shadow in segments:
        _items[item_idx]["score"] = shadow["score"]

    if usage.input_tokens:
        log_token_usage("pruning model tokens (rerank)", usage.input_tokens)
    return usage


def rerank_items(
    query: str,
    items: list[dict[str, Any]],
    settings: RerankPruningSettings,
    extract_fn: Callable[[dict[str, Any]], str | None],
    min_score: float | None = None,
) -> tuple[list[dict[str, Any]], StageTokenUsage]:
    """Generic reranking logic for both json and md items."""
    configure_litellm_quiet()
    indexed_docs = prepare_indexed_documents(items, extract_fn)
    if not indexed_docs:
        return items, empty_usage()

    base_tokens = rerank_bulk_base_tokens(query)

    try:
        return _rerank_prepared_bulks(
            indexed_docs,
            query=query,
            settings=settings,
            items=items,
            base_tokens=base_tokens,
            min_score=min_score,
        )
    except RuntimeError:
        raise
    except Exception as e:
        print(f"Error during reranking: {e}", file=sys.stderr)

    return items, empty_usage()


def prune_reranked_catalog(data: dict[str, Any]) -> dict[str, Any]:
    """Drop catalog items below RERANK_SCORE / RERANK_ENUM_SCORE after rerank_items scored them."""
    if catalog_below_minimum_tools(data, reranker_minimum_tools(), stage="rerank"):
        return data

    return prune_catalog_lists(
        data,
        json_threshold=RERANK_SCORE,
        md_threshold=RERANK_ENUM_SCORE,
        prune_enums=RERANK_ENUMS,
    )


def rerank_catalog_dict(
    data: dict[str, Any],
    query: str,
    *,
    prune: bool = True,
    ctx: PolicyContext | None = None,
    system_policy: SystemToolPolicy | None = None,
    mcp_policy: MCPToolPolicy | None = None,
    merge_pinned: bool = True,
    config: dict[str, Any] | None = None,
    settings: RerankPruningSettings | None = None,
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

    if catalog_below_minimum_tools(data, reranker_minimum_tools(config), stage="rerank"):
        return data, empty_usage()

    resolved_settings = rerank_pruning_settings(config, settings=settings)
    total_usage = empty_usage()

    if "json" in data and isinstance(data["json"], list):
        data["json"], json_usage = rerank_items(
            query,
            data["json"],
            resolved_settings,
            extract_json_catalog_document,
            None,
        )
        total_usage = total_usage.merge(json_usage)

    if RERANK_ENUMS and "md" in data and isinstance(data["md"], list):
        data["md"], md_usage = rerank_items(
            query,
            data["md"],
            resolved_settings,
            extract_md_catalog_document,
            None,
        )
        total_usage = total_usage.merge(md_usage)

    if total_usage.input_tokens:
        log_token_usage("pruning model tokens (rerank)", total_usage.input_tokens)

    if prune:
        data = prune_reranked_catalog(data)

    return finalize_catalog_result(data, pinned, merge_pinned=merge_pinned), total_usage


def main() -> None:
    parser = argparse.ArgumentParser(description="Rerank JSON items using DeepInfra and LiteLLM.")
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

    config = load_config()
    require_proxy_env(config)
    ctx = configure_policies_from_config(config)

    data = load_pruner_catalog_input(json_path=args.json, dir_path=args.dir)

    data, _tokens = rerank_catalog_dict(data, args.query, ctx=ctx)

    output_data = json.dumps(data, indent=2)
    if args.output_json:
        with open(args.output_json, "w") as f:
            f.write(output_data)
        print(f"Results saved to {args.output_json}")
    else:
        print(output_data)


if __name__ == "__main__":
    main()
