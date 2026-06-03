import argparse
import json
import logging
import sys
from typing import Any, TypeVar

from litellm import completion
from pydantic import BaseModel

from cyt.common.token_usage import TIKTOKEN_CL100K, StageTokenUsage, empty_usage
from cyt.config import (
    _remote_defaults,
    key_var_name_for_model_nick,
    llm_minimum_tools,
    load_config,
    remote_model_entry,
    resolve_model,
)
from cyt.indexer.build import catalog_tool_count
from cyt.indexer.tokens import compact_json, count_tokens, log_token_usage
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

T = TypeVar("T")

# Top_k may exclude relevant items, should not be capping by top_k, but by score only.
# LLM_TRIM_TOP_K_JSON: int = 40
LLM_TRIM_MIN_JSON_SCORE: float = 0.11

LLM_TRIM_FIELDS: tuple[str, ...] = ("score", "language", "end_line", "start_line")
LLM_MODEL_EXCLUDED_FIELDS: tuple[str, ...] = (*LLM_TRIM_FIELDS, "id")

# Only decomposed catalog lists are selectable; ``tools`` is metadata, not a chunk.
LLM_CATALOG_LIST_KEYS: tuple[str, ...] = ("json", "md")

SELECTOR_SYSTEM_PROMPT = (
    'These are MCP tools and their enums and optional properties in a "decomposed" state. '
    "Your task is to select the most relevant tool(s), enums and properties based on the user query. "
    "Later on the results will re-compile MCP tools into their full normal definitions based on your selection. "
    "The goal is to return chunk IDs that match the user query the most. "
    "It will be used as a hint for another LLM to use only these relevant tools, enums and optional properties "
    "to save on tokens by removing the irrelevant to user query noise."
    "The goal is to choose most relevant pieces of MCP tools, enums and optional properties that could "
    "potentially be useful for the request. "
)


class RelevantChunkIds(BaseModel):
    ids: list[int]


class LlmPruningSettings:
    """Resolved LLM pruning model and credentials from config."""

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


def llm_pruning_settings(config: dict[str, Any] | None = None) -> LlmPruningSettings:
    """Resolve pruning LLM model and API key from ``defaults.remote.llm_model_nick``."""
    cfg = config or load_config()
    model_nick = _remote_defaults(cfg).get("llm_model_nick")
    if not model_nick:
        raise ValueError("defaults.remote.llm_model_nick is required for llm pruning")
    nick = str(model_nick)
    model_name, api_key, base_url = resolve_model(nick, "llm", "remote", config=cfg)
    if not api_key:
        key_var = key_var_name_for_model_nick(cfg, "llm", nick)
        print(f"Error: {key_var} not found.", file=sys.stderr)
        sys.exit(1)
    entry = remote_model_entry(cfg, "llm", nick)
    provider = entry.get("provider")
    domain_match = entry.get("domain_match")
    provider_dns = None
    if isinstance(domain_match, list) and domain_match:
        provider_dns = str(domain_match[0])
    return LlmPruningSettings(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        provider=str(provider) if provider else None,
        provider_dns=provider_dns,
    )


def read_json_input(path: str) -> dict[str, Any]:
    try:
        with open(path) as f:
            data_loaded: Any = json.load(f)
            if not isinstance(data_loaded, dict):
                print(f"Error: JSON root must be a dictionary in {path}", file=sys.stderr)
                sys.exit(1)
            return data_loaded
    except Exception as e:
        print(f"Error reading JSON: {e}", file=sys.stderr)
        sys.exit(1)


def _json_item_score(item: dict[str, Any]) -> float:
    score = item.get("score")
    if score is None:
        return 0.0
    try:
        return float(score)
    except (TypeError, ValueError):
        return 0.0


def trim_catalog_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Cap json entries after rerank; strip fields irrelevant to the LLM selector."""
    json_items = data.get("json")
    if not isinstance(json_items, list):
        return data

    filtered: list[dict[str, Any]] = []
    for item in json_items:
        if not isinstance(item, dict):
            continue
        if _json_item_score(item) < LLM_TRIM_MIN_JSON_SCORE:
            continue
        filtered.append(item)

    filtered.sort(key=lambda x: str(x.get("file_path", "")))

    #    trimmed = filtered[:LLM_TRIM_TOP_K_JSON]
    #    for item in trimmed:
    #        for field in LLM_TRIM_FIELDS:
    #            item.pop(field, None)
    #
    #    data["json"] = trimmed
    data["json"] = filtered
    return data


def prepare_chunks(data: dict[str, Any]) -> tuple[list[str], dict[int, Any], list[str]]:
    list_keys = [k for k in LLM_CATALOG_LIST_KEYS if k in data and isinstance(data.get(k), list)]

    if not list_keys:
        raise ValueError("No json/md catalog lists found in JSON root.")

    formatted_chunks: list[str] = []
    item_metadata_storage: dict[int, Any] = {}
    keys_to_remove = list(LLM_TRIM_FIELDS)
    model_excluded_fields = set(LLM_MODEL_EXCLUDED_FIELDS)
    global_chunk_id = 1

    for target_key in list_keys:
        items = data[target_key]
        if isinstance(items, list):
            # Sort items by file_path if available
            try:
                items.sort(key=lambda x: str(x.get("file_path", "")) if isinstance(x, dict) else "")
            except (AttributeError, TypeError):
                pass

            for item in items:
                if not isinstance(item, dict):
                    continue
                item_metadata_storage[global_chunk_id] = {
                    "key": target_key,
                    "item": item,
                    "metadata": {k: item.get(k) for k in keys_to_remove},
                }

                if target_key == "json" and not str(item.get("file_path", "")).strip():
                    continue

                item_for_llm = item.copy()
                for k in model_excluded_fields:
                    item_for_llm.pop(k, None)

                chunk_body = compact_json(item_for_llm)
                formatted_chunks.append(f"<chunk id={global_chunk_id}>\n{chunk_body}\n</chunk>\n")
                global_chunk_id += 1

    return formatted_chunks, item_metadata_storage, list_keys


def count_llm_request_tokens(query: str, chunks_text: str) -> int:
    """Estimate input tokens sent to the LLM selector for one request."""
    user_message = f"User Query: {query}\n\nAvailable Chunks:\n\n{chunks_text}"
    return count_tokens(SELECTOR_SYSTEM_PROMPT) + count_tokens(user_message)


def _usage_from_litellm_response(
    response: Any,
    fallback_output_text: str,
    *,
    settings: LlmPruningSettings,
) -> StageTokenUsage:
    usage = getattr(response, "usage", None)
    if usage is not None:
        prompt = getattr(usage, "prompt_tokens", None)
        completion = getattr(usage, "completion_tokens", None)
        if prompt is not None or completion is not None:
            return StageTokenUsage(
                input_tokens=int(prompt or 0),
                output_tokens=int(completion or 0),
                usage_source="provider",
                request_id=getattr(response, "id", None),
                model_name=settings.model_name,
                provider_dns_name=settings.provider_dns,
                provider=settings.provider,
            )
    return StageTokenUsage(
        output_tokens=count_tokens(fallback_output_text),
        usage_source=TIKTOKEN_CL100K,
        model_name=settings.model_name,
        provider_dns_name=settings.provider_dns,
        provider=settings.provider,
    )


def call_llm(
    settings: LlmPruningSettings,
    query: str,
    chunks_text: str,
) -> tuple[RelevantChunkIds, StageTokenUsage]:
    user_message = f"User Query: {query}\n\nAvailable Chunks:\n\n{chunks_text}"
    input_tokens = count_llm_request_tokens(query, chunks_text)

    completion_kwargs: dict[str, Any] = {
        "model": settings.model_name,
        "messages": [
            {"role": "system", "content": SELECTOR_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "api_key": settings.api_key,
        "response_format": RelevantChunkIds,
    }
    if settings.base_url:
        completion_kwargs["api_base"] = settings.base_url

    # litellm.completion returns a ModelResponse object but it's often treated as Any
    response: Any = completion(**completion_kwargs)

    content_val: Any = response.choices[0].message.content
    if not isinstance(content_val, str):
        raise ValueError(f"Unexpected response content type: {type(content_val)}")

    try:
        parsed = RelevantChunkIds.model_validate_json(content_val)
    except Exception:
        import re

        if json_match := re.search(r"\{.*\}", content_val, re.DOTALL):
            parsed = RelevantChunkIds.model_validate_json(json_match.group(0))
        else:
            raise ValueError(f"Could not parse LLM response: {content_val}") from None

    usage = _usage_from_litellm_response(response, content_val, settings=settings)
    if usage.usage_source == TIKTOKEN_CL100K:
        usage = StageTokenUsage(
            input_tokens=input_tokens,
            output_tokens=usage.output_tokens,
            usage_source=TIKTOKEN_CL100K,
            request_id=getattr(response, "id", None),
            model_name=settings.model_name,
            provider_dns_name=settings.provider_dns,
            provider=settings.provider,
        )
    elif usage.input_tokens == 0:
        usage = StageTokenUsage(
            input_tokens=input_tokens,
            output_tokens=usage.output_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            usage_source=usage.usage_source,
            request_id=usage.request_id,
            model_name=usage.model_name,
            provider_dns_name=usage.provider_dns_name,
            provider=usage.provider,
        )
    return parsed, usage


def score_item(item: dict[str, Any], is_selected: bool) -> None:
    if "score" not in item or item["score"] is None:
        return

    try:
        orig_score_val = item["score"]
        is_str = isinstance(orig_score_val, str)
        score_float = float(orig_score_val)

        new_score = score_float if is_selected else score_float / 10.0

        if is_str:
            item["score"] = f"{new_score:.4f}"
        else:
            item["score"] = new_score
    except (ValueError, TypeError):
        pass


def process_results(
    data: dict[str, Any],
    item_metadata_storage: dict[int, Any],
    selected_ids: set[int],
    list_keys: list[str],
) -> dict[str, Any]:
    new_data_lists: dict[str, list[dict[str, Any]]] = {k: [] for k in list_keys}

    for chunk_id, storage in item_metadata_storage.items():
        target_key: str = storage["key"]
        item: dict[str, Any] = storage["item"]
        metadata: dict[str, Any | None] = storage["metadata"]

        for k, v in metadata.items():
            if v is not None:
                item[k] = v

        is_selected = chunk_id in selected_ids

        if target_key == "md":
            score_item(item, is_selected)
            new_data_lists[target_key].append(item)
        elif is_selected:
            new_data_lists[target_key].append(item)

    for k, v in new_data_lists.items():
        data[k] = v

    return data


def llm_catalog_dict(
    data: dict[str, Any],
    query: str,
    *,
    system_policy: SystemToolPolicy | None = system_tool_policy,
    mcp_policy: MCPToolPolicy | None = mcp_tool_policy,
    merge_pinned: bool = True,
) -> tuple[dict[str, Any], StageTokenUsage]:
    """Select relevant catalog chunks via LLM; same contract as rerank_catalog_dict."""
    if (
        system_policy is not None
        and mcp_policy is not None
        and full_pass_through(system_policy, mcp_policy)
    ):
        return data, empty_usage()

    minimum_tools = llm_minimum_tools()
    tool_count = catalog_tool_count(data)
    if tool_count < minimum_tools:
        logger.info(
            "llm pruning skipped: %d tools below minimum %d",
            tool_count,
            minimum_tools,
        )
        return data, empty_usage()

    settings = llm_pruning_settings()
    pinned: dict[str, Any] = {}
    if system_policy is not None and mcp_policy is not None and catalog_needs_partition(data):
        data, pinned = partition_catalog(data, system_policy, mcp_policy)

    formatted_chunks, item_metadata_storage, list_keys = prepare_chunks(data)

    from cyt.pruners.split import split_chunks_into_bulks

    bulks = split_chunks_into_bulks(query, SELECTOR_SYSTEM_PROMPT, formatted_chunks)
    selected_ids: set[int] = set()
    total_usage = empty_usage()

    for bulk_text in bulks:
        bulk_tokens = count_llm_request_tokens(query, bulk_text)
        logger.info("llm request tokens: %d", bulk_tokens)
        parsed_response, bulk_usage = call_llm(settings, query, bulk_text)
        if bulk_usage.input_tokens == 0:
            bulk_usage = StageTokenUsage(
                input_tokens=bulk_tokens,
                output_tokens=bulk_usage.output_tokens,
                usage_source=bulk_usage.usage_source,
                request_id=bulk_usage.request_id,
                model_name=bulk_usage.model_name,
                provider_dns_name=bulk_usage.provider_dns_name,
                provider=bulk_usage.provider,
            )
        total_usage = total_usage.merge(bulk_usage)
        selected_ids.update(parsed_response.ids)

    if total_usage.input_tokens or total_usage.output_tokens:
        log_token_usage(
            "pruning model tokens (llm)",
            total_usage.input_tokens + total_usage.output_tokens,
        )

    result = process_results(data, item_metadata_storage, selected_ids, list_keys)
    if merge_pinned and pinned:
        result = merge_catalog(result, pinned)
    return result, total_usage


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter JSON items using an LLM on OpenRouter.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--json", help="Input JSON file path")
    group.add_argument("--dir", help="Path to the directory containing decomposed tool files")
    parser.add_argument("--output-json", help="Optional output JSON file path")
    parser.add_argument("query", help="User search query")

    args = parser.parse_args()

    configure_policies_from_config()

    if args.json:
        data = read_json_input(args.json)
    else:
        from cyt.indexer.retrieve import load_catalog

        try:
            data = load_catalog(args.dir)
        except Exception as e:
            print(f"Error loading catalog directory: {e}", file=sys.stderr)
            sys.exit(1)

    try:
        result, _tokens = llm_catalog_dict(data, args.query)
        output_data = json.dumps(result, indent=2)
        if args.output_json:
            with open(args.output_json, "w") as f:
                f.write(output_data)
            print(f"Results saved to {args.output_json}")
        else:
            print(output_data)
    except Exception as e:
        print(f"Error during LLM processing: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
