import argparse
import json
import logging
import sys
from typing import Any
from urllib.parse import urlparse

from litellm import rerank

from cyt.indexer.build import catalog_tool_count, count_tokens, log_token_usage
from cyt.config import (
    _remote_defaults,
    key_var_name_for_model_nick,
    load_config,
    remote_model_entry,
    reranker_minimum_tools,
    resolve_model,
)
from cyt.pruners.split import split_into_bulks
from cyt.common.token_usage import TIKTOKEN_CL100K, StageTokenUsage, empty_usage
from cyt.pruners.policies import (
    MCPToolPolicy,
    SystemToolPolicy,
    catalog_needs_partition,
    configure_policies_from_config,
    full_pass_through,
    mcp_tool_policy,
    merge_catalog,
    partition_catalog,
    system_tool_policy,
)

logger = logging.getLogger(__name__)

RERANK_SCORE: float = 0.003
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


def extract_level_info(data: Any) -> list[str]:
    """
    Recursively searches for description, default, and enum keys at all levels.
    Returns a list of formatted strings, one for each level where at least a description is found.
    """
    results = []

    if isinstance(data, dict):
        # Extract from current level
        desc = data.get("description")
        default_val = data.get("default")
        enums = data.get("enum")

        if desc:
            line = str(desc)
            if default_val is not None:
                line += f"; Default: {default_val}"
            if enums and isinstance(enums, list):
                enums_str = ", ".join(map(str, enums))
                line += f"; Options: {enums_str}"
            results.append(line)

        # Recurse into all values
        for val in data.values():
            results.extend(extract_level_info(val))

    elif isinstance(data, list):
        for item in data:
            results.extend(extract_level_info(item))

    return results


def extract_document_text(item_content: Any) -> str | None:
    """
    Combines information from all levels, with each level on its own newline.
    """
    level_lines = extract_level_info(item_content)
    if not level_lines:
        return None
    return "\n".join(level_lines)


def _extract_json_catalog_document(item: dict[str, Any]) -> str | None:
    """Build rerank document text from schema content only (exclude catalog metadata like id)."""
    content = item.get("content")
    if content is None:
        return None
    return extract_document_text(content)


def process_response(response: Any, valid_indices: list[int], items: list[dict[str, Any]]) -> None:
    """Processes the rerank response and updates item scores."""
    # LiteLLM's rerank response usually has a 'results' attribute or key
    results_list = []
    if hasattr(response, "results"):
        results_list = response.results
    elif isinstance(response, dict) and "results" in response:
        results_list = response["results"]
    else:
        # Fallback if it's already a list
        results_list = response

    for result in results_list:
        try:
            # Try attribute access first
            doc_idx = getattr(result, "index", None)
            relevance_score = getattr(result, "relevance_score", None)

            # Fallback to dictionary access
            if doc_idx is None:
                doc_idx = result["index"]
            if relevance_score is None:
                relevance_score = result["relevance_score"]

            original_idx = valid_indices[doc_idx]
            # Store as string with 20 decimal places to avoid scientific notation in JSON
            items[original_idx]["score"] = f"{relevance_score:.20f}"
        except (KeyError, TypeError, IndexError) as e:
            print(f"Debug: Error processing result {result}: {e}", file=sys.stderr)
            continue


def count_rerank_request_tokens(query: str, documents: list[str]) -> int:
    """Estimate input tokens sent to the rerank API for one request."""
    return count_tokens(query) + sum(count_tokens(doc) for doc in documents)


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
    extract_fn: Any,
) -> list[tuple[int, str]]:
    indexed_docs: list[tuple[int, str]] = []
    for i, item in enumerate(items):
        item["score"] = f"{0.0:.20f}"
        doc_text = extract_fn(item)
        if doc_text:
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
    extract_fn: Any,
    min_score: float | None = None,
) -> tuple[list[dict[str, Any]], StageTokenUsage]:
    """Generic reranking logic for both json and md items."""
    indexed_docs = _prepare_rerank_documents(items, extract_fn)
    if not indexed_docs:
        return items, empty_usage()

    base_tokens = count_tokens(query) + 200

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


def _extract_md_content(item: dict[str, Any]) -> str | None:
    content = item.get("content")
    return str(content) if content else None


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
    system_policy: SystemToolPolicy | None = system_tool_policy,
    mcp_policy: MCPToolPolicy | None = mcp_tool_policy,
    merge_pinned: bool = True,
) -> tuple[dict[str, Any], StageTokenUsage]:
    """Score in-place data['json'] and optionally data['md']; optionally prune by score."""
    if (
        system_policy is not None
        and mcp_policy is not None
        and full_pass_through(system_policy, mcp_policy)
    ):
        return data, empty_usage()

    if _below_reranker_minimum_tools(data):
        return data, empty_usage()

    settings = rerank_pruning_settings()
    total_usage = empty_usage()
    pinned: dict[str, Any] = {}
    if system_policy is not None and mcp_policy is not None and catalog_needs_partition(data):
        data, pinned = partition_catalog(data, system_policy, mcp_policy)

    if "json" in data and isinstance(data["json"], list):
        data["json"], json_usage = rerank_items(
            query,
            data["json"],
            settings,
            _extract_json_catalog_document,
            None,
        )
        total_usage = total_usage.merge(json_usage)

    if RERANK_ENUMS and "md" in data and isinstance(data["md"], list):
        data["md"], md_usage = rerank_items(
            query,
            data["md"],
            settings,
            _extract_md_content,
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

    configure_policies_from_config()

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

    data, _tokens = rerank_catalog_dict(data, args.query)

    output_data = json.dumps(data, indent=2)
    if args.output_json:
        with open(args.output_json, "w") as f:
            f.write(output_data)
        print(f"Results saved to {args.output_json}")
    else:
        print(output_data)


if __name__ == "__main__":
    main()
