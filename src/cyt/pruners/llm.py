import argparse
import json
import logging
import sys
from typing import Any, TypeVar, cast

from litellm import completion, responses
from pydantic import BaseModel

from cyt.common.token_usage import TIKTOKEN_CL100K, StageTokenUsage, empty_usage
from cyt.config import llm_minimum_tools, load_config, require_proxy_env
from cyt.indexer.tokens import compact_json, count_tokens, count_tokens_batch, log_token_usage
from cyt.pruners.catalog_common import (
    catalog_below_minimum_tools,
    finalize_catalog_result,
    load_pruner_catalog_input,
    prepare_catalog_for_scoring,
    resolve_policy_context,
)
from cyt.pruners.litellm_quiet import configure_litellm_quiet
from cyt.pruners.policies import (
    MCPToolPolicy,
    PolicyContext,
    SystemToolPolicy,
    configure_policies_from_config,
)
from cyt.pruners.remote import (
    LlmPruningSettings,
    request_pruner_settings,
    resolve_remote_pruning_settings,
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

SELECTOR_EMPTY_ID = -1

SELECTOR_NO_MATCH_INSTRUCTION = (
    f"If nothing is suitable for the user query, return ids: [{SELECTOR_EMPTY_ID}] only."
)

TOOL_SELECTOR_SYSTEM_PROMPT = (
    'These are MCP tools and their enums and optional properties in a "decomposed" state. '
    "Your task is to select the most relevant tool(s), enums and properties based on the user query. "
    "Later we re-compile MCP tools into their full normal definitions based on your selection. "
    "The goal is to return chunk IDs that match the user query the most. "
    "It will be used as a hint for another LLM to use only these relevant tools, enums and optional properties "
    "to save on tokens by removing the irrelevant to user query noise."
    "The goal is to choose most relevant pieces of MCP tools, enums and optional properties that could "
    "potentially be useful for the request. You have a soft budget of 5000 tokens to select the most relevant chunks."
    f"{SELECTOR_NO_MATCH_INSTRUCTION}"
)


def tool_selector_system_prompt(config: dict[str, Any] | None = None) -> str:
    """``TOOL_SELECTOR_SYSTEM_PROMPT`` plus cached executor MCP execute tool/skill appendix.

    Appendix is memory/disk cache only — never hits the live executor API.
    """
    prompt = TOOL_SELECTOR_SYSTEM_PROMPT
    try:
        from cyt.tools.sources.executor_http import get_executor_mcp_cache
        from cyt.tools.sources.executor_mcp import format_executor_mcp_selector_appendix

        appendix = format_executor_mcp_selector_appendix(
            get_executor_mcp_cache(config, allow_prompt=False),
        )
    except Exception as exc:
        logger.debug("executor MCP selector appendix skipped: %s", exc)
        return prompt
    if not appendix:
        return prompt
    return f"{prompt}\n\n{appendix}"


class RelevantChunkIds(BaseModel):
    ids: list[int]


def normalize_selector_ids(ids: list[int]) -> list[int]:
    """Drop the empty-selection sentinel; ``[-1]`` means no chunks matched."""
    return [chunk_id for chunk_id in ids if chunk_id != SELECTOR_EMPTY_ID]


def llm_pruning_settings(
    config: dict[str, Any] | None = None,
    *,
    settings: LlmPruningSettings | None = None,
) -> LlmPruningSettings:
    """Resolve pruning LLM model from pipeline config."""
    if settings is not None:
        return settings
    request_cache = request_pruner_settings()
    if request_cache is not None and (cached := request_cache.for_stage("llm")) is not None:
        return cached
    return resolve_remote_pruning_settings(
        config=config,
        model_kind="llm",
        pipeline_name="llm",
        missing_nick_message=("pruning.tools.pipelines.llm.model_nick is required for llm pruning"),
    )


def _json_item_score(item: dict[str, Any]) -> float:
    score = item.get("score")
    if score is None:
        return 0.0
    try:
        return float(score)
    except (TypeError, ValueError):
        return 0.0


def trim_catalog_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Drop low-scoring json entries before the LLM selector stage."""
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


def prepare_catalog_selector_chunks(
    data: dict[str, Any],
) -> tuple[list[str], dict[int, Any], list[str]]:
    """Format json/md catalog items as selector chunks with stable global ids."""
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

                item_for_selector = item.copy()
                for k in model_excluded_fields:
                    item_for_selector.pop(k, None)

                chunk_body = compact_json(item_for_selector)
                formatted_chunks.append(f"<chunk id={global_chunk_id}>\n{chunk_body}\n</chunk>\n")
                global_chunk_id += 1

    return formatted_chunks, item_metadata_storage, list_keys


def _llm_user_message(query: str, chunks_text: str) -> str:
    """User turn for the selector model: stable chunk prefix, query suffix (prompt-cache friendly)."""
    return f"Available Chunks:\n\n{chunks_text}\n\n<user-query >\n{query}\n</user-query>"


def llm_selector_bulk_base_tokens(query: str, system_prompt: str) -> int:
    """Token budget reserved per selector bulk (system + message frame, no chunk bodies)."""
    frame = _llm_user_message(query, "")
    return count_tokens(f"System: {system_prompt}\n{frame}")


def count_llm_request_tokens(
    query: str,
    chunks_text: str,
    *,
    system_prompt: str = TOOL_SELECTOR_SYSTEM_PROMPT,
) -> int:
    """Estimate input tokens sent to the LLM selector for one request."""
    user_message = _llm_user_message(query, chunks_text)
    prompt_tokens, user_tokens = count_tokens_batch([system_prompt, user_message])
    return prompt_tokens + user_tokens


def _usage_from_litellm_response(
    response: object,
    fallback_output_text: str,
    *,
    settings: LlmPruningSettings,
) -> StageTokenUsage:
    usage = getattr(response, "usage", None)
    if usage is not None:
        prompt = getattr(usage, "prompt_tokens", None)
        if prompt is None:
            prompt = getattr(usage, "input_tokens", None)
        completion = getattr(usage, "completion_tokens", None)
        if completion is None:
            completion = getattr(usage, "output_tokens", None)
        reasoning_tokens: int | None = None
        details = getattr(usage, "completion_tokens_details", None)
        if details is not None:
            raw_reasoning = getattr(details, "reasoning_tokens", None)
            if raw_reasoning is not None:
                reasoning_tokens = int(raw_reasoning)
        if prompt is not None or completion is not None:
            return StageTokenUsage(
                input_tokens=int(prompt or 0),
                output_tokens=int(completion or 0),
                reasoning_tokens=reasoning_tokens,
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


def _message_field(message: object | None, name: str) -> object | None:
    if message is None:
        return None
    dict_get = getattr(message, "get", None)
    if callable(dict_get):
        return cast(object | None, dict_get(name))
    return cast(object | None, getattr(message, name, None))


def _structured_selector_content(response: object) -> tuple[str | None, str]:
    """Return selector payload from Pydantic ``response_format`` content only."""
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        message = getattr(choices[0], "message", None)
        content = _message_field(message, "content")
        if isinstance(content, str) and content.strip():
            return content.strip(), "content"

    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip(), "output_text"
    return None, "none"


def _selector_uses_openrouter(settings: LlmPruningSettings) -> bool:
    model = settings.model_name.lower()
    if "openrouter" in model:
        return True
    dns = (settings.provider_dns or "").lower()
    return "openrouter" in dns


_OPENROUTER_MANDATORY_REASONING_MODEL_MARKERS = ("gpt-oss",)


def _selector_requires_reasoning_effort(settings: LlmPruningSettings) -> bool:
    """OpenRouter models that reject ``reasoning.effort: none`` (must enable reasoning)."""
    model = settings.model_name.lower()
    return any(marker in model for marker in _OPENROUTER_MANDATORY_REASONING_MODEL_MARKERS)


def _selector_completion_extras(settings: LlmPruningSettings) -> dict[str, Any]:
    """Request kwargs so reasoning models emit structured selector JSON in content."""
    if not _selector_uses_openrouter(settings):
        return {}
    if _selector_requires_reasoning_effort(settings):
        return {"reasoning": {"effort": "low"}}
    return {"reasoning": {"effort": "none"}}


def _parse_selector_json(content_val: str) -> RelevantChunkIds | None:
    try:
        return RelevantChunkIds.model_validate_json(content_val)
    except Exception:
        import re

        if json_match := re.search(r"\{.*\}", content_val, re.DOTALL):
            try:
                return RelevantChunkIds.model_validate_json(json_match.group(0))
            except Exception:
                pass
    return None


def _warn_empty_selector_response(response: object, *, model_name: str, reason: str) -> None:
    detail = _llm_response_diagnostics(response, model_name=model_name)
    logger.warning("llm selector %s; treating as zero selections (%s)", reason, detail)


def _llm_response_diagnostics(response: object, *, model_name: str) -> str:
    """Summarize LiteLLM response fields useful when selector output is missing."""
    parts = [f"model={model_name!r}"]
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        choice = choices[0]
        parts.append(f"finish_reason={getattr(choice, 'finish_reason', None)!r}")
        message = getattr(choice, "message", None)
        if message is not None:
            content = _message_field(message, "content")
            parts.append(f"content_type={type(content).__name__}")
            for field in ("reasoning_content", "reasoning"):
                val = _message_field(message, field)
                if val is not None:
                    parts.append(f"{field}_type={type(val).__name__}")
            details = _message_field(message, "reasoning_details")
            if isinstance(details, list):
                parts.append(f"reasoning_details_len={len(details)}")
    elif (output_text := getattr(response, "output_text", None)) is not None:
        parts.append(f"output_text_type={type(output_text).__name__}")
    usage = getattr(response, "usage", None)
    if usage is not None:
        completion_tokens = getattr(usage, "completion_tokens", None)
        if completion_tokens is None:
            completion_tokens = getattr(usage, "output_tokens", None)
        parts.append(f"completion_tokens={completion_tokens!r}")
        details = getattr(usage, "completion_tokens_details", None)
        if details is not None:
            parts.append(f"reasoning_tokens={getattr(details, 'reasoning_tokens', None)!r}")
    return ", ".join(parts)


def call_llm(
    settings: LlmPruningSettings,
    query: str,
    chunks_text: str,
    *,
    system_prompt: str = TOOL_SELECTOR_SYSTEM_PROMPT,
) -> tuple[RelevantChunkIds, StageTokenUsage]:
    configure_litellm_quiet()
    user_message = _llm_user_message(query, chunks_text)
    input_tokens = count_llm_request_tokens(query, chunks_text, system_prompt=system_prompt)

    request_kwargs: dict[str, Any] = {
        "model": settings.model_name,
        "api_key": settings.api_key,
    }
    if settings.base_url:
        request_kwargs["api_base"] = settings.base_url

    if settings.responses_api:
        request_kwargs["instructions"] = system_prompt
        request_kwargs["input"] = user_message
        request_kwargs["text_format"] = RelevantChunkIds
        response: Any = responses(**request_kwargs)
    else:
        request_kwargs["messages"] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        request_kwargs["response_format"] = RelevantChunkIds
        request_kwargs.update(_selector_completion_extras(settings))
        response = completion(**request_kwargs)

    content_val, _ = _structured_selector_content(response)

    usage = _usage_from_litellm_response(response, content_val or "", settings=settings)
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

    if not content_val:
        _warn_empty_selector_response(
            response,
            model_name=settings.model_name,
            reason="returned no structured content",
        )
        return RelevantChunkIds(ids=[]), usage

    parsed = _parse_selector_json(content_val)
    if parsed is None:
        _warn_empty_selector_response(
            response,
            model_name=settings.model_name,
            reason="structured content failed Pydantic validation",
        )
        return RelevantChunkIds(ids=[]), usage

    normalized_ids = normalize_selector_ids(parsed.ids)
    return RelevantChunkIds(ids=normalized_ids), usage


def llm_select_ids(
    query: str,
    system_prompt: str,
    formatted_items: list[str],
    *,
    config: dict[str, Any] | None = None,
    settings: LlmPruningSettings | None = None,
) -> tuple[set[int], StageTokenUsage]:
    """Run LLM selector over formatted chunks; return union of Pydantic-parsed ids."""
    if not formatted_items:
        return set(), empty_usage()

    from cyt.pruners.split import split_chunks_into_bulks

    resolved_settings = llm_pruning_settings(config, settings=settings)
    bulks = split_chunks_into_bulks(query, system_prompt, formatted_items)
    selected_ids: set[int] = set()
    total_usage = empty_usage()

    def _run_bulk(bulk_text: str) -> tuple[set[int], StageTokenUsage]:
        bulk_tokens = count_llm_request_tokens(query, bulk_text, system_prompt=system_prompt)
        logger.info("llm request tokens: %d", bulk_tokens)
        parsed_response, bulk_usage = call_llm(
            resolved_settings,
            query,
            bulk_text,
            system_prompt=system_prompt,
        )
        if bulk_usage.input_tokens == 0:
            bulk_usage = StageTokenUsage(
                input_tokens=bulk_tokens,
                output_tokens=bulk_usage.output_tokens,
                usage_source=bulk_usage.usage_source,
                request_id=bulk_usage.request_id,
                model_name=resolved_settings.model_name,
                provider_dns_name=bulk_usage.provider_dns_name,
                provider=bulk_usage.provider,
            )
        return set(parsed_response.ids), bulk_usage

    if len(bulks) <= 1:
        for bulk_text in bulks:
            bulk_ids, bulk_usage = _run_bulk(bulk_text)
            total_usage = total_usage.merge(bulk_usage)
            selected_ids.update(bulk_ids)
    else:
        from cyt.pruning.parallel import run_parallel

        work = {
            str(index): (lambda bulk_text=bulk_text: _run_bulk(bulk_text))
            for index, bulk_text in enumerate(bulks)
        }
        parallel_results = run_parallel(work)
        for index in range(len(bulks)):
            bulk_ids, bulk_usage = parallel_results[str(index)]
            total_usage = total_usage.merge(bulk_usage)
            selected_ids.update(bulk_ids)

    if total_usage.input_tokens or total_usage.output_tokens:
        log_token_usage(
            "pruning model tokens (llm)",
            total_usage.input_tokens + total_usage.output_tokens,
        )

    return selected_ids, total_usage


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


def apply_selector_ids_to_catalog(
    data: dict[str, Any],
    item_metadata_storage: dict[int, Any],
    selected_ids: set[int],
    list_keys: list[str],
) -> dict[str, Any]:
    """Rebuild catalog lists from selector metadata and chosen chunk ids."""
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
    ctx: PolicyContext | None = None,
    system_policy: SystemToolPolicy | None = None,
    mcp_policy: MCPToolPolicy | None = None,
    merge_pinned: bool = True,
    config: dict[str, Any] | None = None,
    settings: LlmPruningSettings | None = None,
) -> tuple[dict[str, Any], StageTokenUsage]:
    """Select relevant catalog chunks via LLM; same contract as rerank_catalog_dict."""
    policy_ctx = resolve_policy_context(
        ctx=ctx,
        system_policy=system_policy,
        mcp_policy=mcp_policy,
        config=config,
    )
    data, pinned, skip_scoring = prepare_catalog_for_scoring(data, policy_ctx)
    if skip_scoring:
        return data, empty_usage()

    if catalog_below_minimum_tools(data, llm_minimum_tools(config), stage="llm"):
        return data, empty_usage()

    formatted_chunks, item_metadata_storage, list_keys = prepare_catalog_selector_chunks(data)

    selected_ids, total_usage = llm_select_ids(
        query,
        tool_selector_system_prompt(config),
        formatted_chunks,
        config=config,
        settings=settings,
    )

    result = apply_selector_ids_to_catalog(
        data,
        item_metadata_storage,
        selected_ids,
        list_keys,
    )
    return finalize_catalog_result(result, pinned, merge_pinned=merge_pinned), total_usage


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter JSON items using an LLM on OpenRouter.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--json", help="Input JSON file path")
    group.add_argument("--dir", help="Path to the directory containing decomposed tool files")
    parser.add_argument("--output-json", help="Optional output JSON file path")
    parser.add_argument("query", help="User search query")

    args = parser.parse_args()

    config = load_config()
    require_proxy_env(config)
    ctx = configure_policies_from_config(config)

    data = load_pruner_catalog_input(json_path=args.json, dir_path=args.dir)

    try:
        result, _tokens = llm_catalog_dict(data, args.query, ctx=ctx)
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


prepare_chunks = prepare_catalog_selector_chunks
process_results = apply_selector_ids_to_catalog

if __name__ == "__main__":
    main()
