import argparse
import json
import logging
import sys
from collections.abc import Callable
from typing import Any, cast
from urllib.parse import urlparse

from litellm import rerank

from cyt.common.token_usage import TIKTOKEN_CL100K, StageTokenUsage, empty_usage
from cyt.config import (
    _remote_defaults,
    key_var_name_for_model_nick,
    load_config,
    remote_model_entry,
    require_proxy_env,
    reranker_minimum_tools,
    resolve_model,
)
from cyt.indexer.build import catalog_tool_count
from cyt.indexer.tokens import count_json_tokens, log_token_usage
from cyt.pruners.documents import (
    extract_json_catalog_document,
    extract_md_catalog_document,
)
from cyt.pruners.policies import (
    RERANK_SCORE,
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
from cyt.pruners.split import split_into_bulks

logger = logging.getLogger(__name__)
RERANK_ENUMS: bool = True
RERANK_ENUM_SCORE: float = 0.0001


class RerankPruningSettings:
    """Resolved reranker model and credentials from config."""

    __slots__ = ("api_key", "base_url", "model_name", "provider", "provider_dns")

    def __init__(
        self,
        model_name: str,
        api_key: str,
        base_url: str | None,
        provider: str | None,
        provider_dns: str | None,
    ) -> None:
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self.provider = provider
        self.provider_dns = provider_dns


def rerank_pruning_settings(config: dict[str, Any] | None = None) -> RerankPruningSettings:
    """Resolve pruning reranker model and API key from ``defaults.remote.reranking_model_nick``."""
    cfg = config or load_config()
    model_nick = _remote_defaults(cfg).get("reranking_model_nick")
    if not model_nick:
        raise ValueError("defaults.remote.reranking_model_nick is required for rerank pruning")
    nick = str(model_nick)
    model_name, api_key, base_url = resolve_model(nick, "rerankers", "remote", config=cfg)
    if not api_key:
        key_var = key_var_name_for_model_nick(cfg, "rerankers", nick)
        print(f"Error: {key_var} not found.", file=sys.stderr)
        sys.exit(1)
    entry = remote_model_entry(cfg, "rerankers", nick)
    provider = entry.get("provider")
    domain_match = entry.get("domain_match")
    provider_dns = None
    if isinstance(domain_match, list) and domain_match:
        provider_dns = str(domain_match[0])
    elif base_url:
        provider_dns = urlparse(str(base_url)).hostname
    return RerankPruningSettings(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        provider=str(provider) if provider else None,
        provider_dns=provider_dns,
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


def _prepare_rerank_documents(
    items: list[dict[str, Any]],
    extract_fn: Callable[[dict[str, Any]], str | None],
) -> list[tuple[int, str]]:
    indexed_docs: list[tuple[int, str]] = []
    for i, item in enumerate(items):
        item["score"] = f"{0.0:.20f}"
        if doc_text := extract_fn(item):
            indexed_docs.append((i, doc_text))
    return indexed_docs


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


def rerank_items(
    query: str,
    items: list[dict[str, Any]],
    settings: RerankPruningSettings,
    extract_fn: Callable[[dict[str, Any]], str | None],
    min_score: float | None = None,
) -> tuple[list[dict[str, Any]], StageTokenUsage]:
    """Generic reranking logic for both json and md items."""
    indexed_docs = _prepare_rerank_documents(items, extract_fn)
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


def _below_reranker_minimum_tools(data: dict[str, Any]) -> bool:
    minimum_tools = reranker_minimum_tools()
    tool_count = catalog_tool_count(data)
    if tool_count < minimum_tools:
        logger.info(
            "rerank pruning skipped: %d tools below minimum %d",
            tool_count,
            minimum_tools,
        )
        return True
    return False


def prune_reranked_catalog(data: dict[str, Any]) -> dict[str, Any]:
    """Drop catalog items below RERANK_SCORE / RERANK_ENUM_SCORE after rerank_items scored them."""
    if _below_reranker_minimum_tools(data):
        return data

    json_items = data.get("json")
    if isinstance(json_items, list):
        data["json"] = [item for item in json_items if float(item.get("score", 0)) >= RERANK_SCORE]

    if RERANK_ENUMS:
        md_items = data.get("md")
        if isinstance(md_items, list):
            data["md"] = [
                item for item in md_items if float(item.get("score", 0)) >= RERANK_ENUM_SCORE
            ]

    return data


def rerank_catalog_dict(
    data: dict[str, Any],
    query: str,
    *,
    prune: bool = True,
    ctx: PolicyContext | None = None,
    system_policy: SystemToolPolicy | None = None,
    mcp_policy: MCPToolPolicy | None = None,
    merge_pinned: bool = True,
) -> tuple[dict[str, Any], StageTokenUsage]:
    """Score in-place data['json'] and optionally data['md']; optionally prune by score."""
    policy_ctx = ctx
    if policy_ctx is None and (system_policy is not None or mcp_policy is not None):
        policy_ctx = policy_context_from_config(system=system_policy, mcp=mcp_policy)

    if policy_ctx is not None and full_pass_through(policy_ctx):
        return data, empty_usage()

    if _below_reranker_minimum_tools(data):
        return data, empty_usage()

    settings = rerank_pruning_settings()
    total_usage = empty_usage()
    pinned: dict[str, Any] = {}
    if policy_ctx is not None and catalog_needs_partition(data, policy_ctx):
        data, pinned = partition_catalog(data, policy_ctx)

    if "json" in data and isinstance(data["json"], list):
        data["json"], json_usage = rerank_items(
            query,
            data["json"],
            settings,
            extract_json_catalog_document,
            None,
        )
        total_usage = total_usage.merge(json_usage)

    if RERANK_ENUMS and "md" in data and isinstance(data["md"], list):
        data["md"], md_usage = rerank_items(
            query,
            data["md"],
            settings,
            extract_md_catalog_document,
            None,
        )
        total_usage = total_usage.merge(md_usage)

    if total_usage.input_tokens:
        log_token_usage("pruning model tokens (rerank)", total_usage.input_tokens)

    if prune:
        data = prune_reranked_catalog(data)

    if merge_pinned and pinned:
        data = merge_catalog(data, pinned)

    return data, total_usage


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
