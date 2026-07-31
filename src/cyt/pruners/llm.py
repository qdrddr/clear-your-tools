import argparse
import copy
import json
import logging
import sys
from collections.abc import Callable
from contextlib import nullcontext
from typing import Any, Literal, TypeVar, cast

from litellm import completion, responses
from pydantic import BaseModel, Field

from cyt.common.phase_timing import PhaseTimer
from cyt.common.token_usage import TIKTOKEN_CL100K, StageTokenUsage, empty_usage
from cyt.config import (
    llm_enum_score,
    llm_minimum_tools,
    llm_score,
    load_config,
    require_proxy_env,
    tools_selector_soft_budget,
)
from cyt.indexer.tokens import compact_json, count_tokens, count_tokens_batch, log_token_usage
from cyt.pruners.catalog_common import (
    catalog_below_minimum_tools,
    finalize_catalog_result,
    load_pruner_catalog_input,
    prepare_catalog_for_scoring,
    prune_catalog_lists,
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
from cyt.pruners.selector_xml import (
    SELECTOR_SOFT_BUDGET_MIN,
    SELECTOR_SOFT_BUDGET_TOOLS_TOTAL,
    ToolSelectorTokenRow,
    format_selector_soft_budget_line,
    parse_cached_token_count,
    per_bulk_soft_budget,
    replace_selector_soft_budget,
    selector_tokens_attr,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Drop catalog chunks below this normalized score before the LLM selector (after bm25/rerank).
LLM_TRIM_MIN_JSON_SCORE: float = 0.11

LLM_TRIM_FIELDS: tuple[str, ...] = ("score", "language", "end_line", "start_line")
LLM_MODEL_EXCLUDED_FIELDS: tuple[str, ...] = (*LLM_TRIM_FIELDS, "id", "token_count")

# Only decomposed catalog lists are selectable; ``tools`` is metadata, not a chunk.
LLM_CATALOG_LIST_KEYS: tuple[str, ...] = ("json", "md")

SELECTOR_EMPTY_ID = -1
LLM_SCORE = 30
LLM_ENUM_SCORE = 30

SELECTOR_SCORE_INSTRUCTION = (
    "Return a JSON object with a selections array. "
    "Include only chunks with score > 0; omitted ids are treated as 0. "
    "Each element must have id (chunk id) and score (integer 1-100 relevance; "
    "higher means stronger match)."
)

SELECTOR_NO_MATCH_INSTRUCTION = (
    'If nothing is suitable for the user query, return {"selections": []}.'
)

SELECTOR_VAGUE_QUERY_INSTRUCTION = (
    "If the user query is a single generic word with no specific technical task "
    "(e.g. 'test', 'investigate', 'help'), return {\"selections\": []} unless exactly one "
    "chunk is an obvious exact match (include only that chunk with a high score)."
)

_TOOL_SELECTOR_SYSTEM_PROMPT_PREFIX = (
    'These are MCP tools and their enums and optional properties in a "decomposed" state. '
    "Your task is to score chunks by relevance to the user query. "
    "Later we re-compile MCP tools into their full normal definitions based on your selection. "
    f"{SELECTOR_SCORE_INSTRUCTION} "
    "It will be used as a hint for another LLM to use only these relevant tools, enums and optional properties "
    "to save on tokens by removing the irrelevant to user query noise. "
    "Each tool/chunk tag includes a tokens attribute; agent-tools includes total-tokens. "
)

_TOOL_SELECTOR_SYSTEM_PROMPT_SUFFIX = (
    f"{SELECTOR_VAGUE_QUERY_INSTRUCTION}{SELECTOR_NO_MATCH_INSTRUCTION}"
)


def build_tool_selector_system_prompt(*, soft_budget: int) -> str:
    return (
        f"{_TOOL_SELECTOR_SYSTEM_PROMPT_PREFIX}"
        f"{format_selector_soft_budget_line(soft_budget, target='chunks')}"
        f"{_TOOL_SELECTOR_SYSTEM_PROMPT_SUFFIX}"
    )


TOOL_SELECTOR_SYSTEM_PROMPT = build_tool_selector_system_prompt(
    soft_budget=SELECTOR_SOFT_BUDGET_TOOLS_TOTAL,
)


def tool_selector_system_prompt(
    config: dict[str, Any] | None = None,
    *,
    soft_budget: int | None = None,
) -> str:
    """Tool selector prompt plus cached executor MCP execute tool/skill appendix.

    Appendix is memory/disk cache only — never hits the live executor API.
    """
    resolved_budget = soft_budget if soft_budget is not None else tools_selector_soft_budget(config)
    prompt = build_tool_selector_system_prompt(soft_budget=resolved_budget)
    from cyt.config import uses_executor_tool_catalog

    if not uses_executor_tool_catalog(config):
        return prompt
    try:
        from cyt.executor.http import get_executor_mcp_cache
        from cyt.executor.mcp import format_executor_mcp_selector_appendix

        appendix = format_executor_mcp_selector_appendix(
            get_executor_mcp_cache(config, allow_prompt=False),
        )
    except Exception as exc:
        logger.debug("executor MCP selector appendix skipped: %s", exc)
        return prompt
    if not appendix:
        return prompt
    return f"{prompt}\n\n{appendix}"


class ChunkSelection(BaseModel):
    id: int
    score: int = Field(ge=0, le=100)


class RelevantChunkSelections(BaseModel):
    selections: list[ChunkSelection]


def normalize_selector_selections(
    selections: list[ChunkSelection],
) -> list[ChunkSelection]:
    """Drop the empty-selection sentinel; ``id=-1`` means no chunks matched."""
    return [selection for selection in selections if selection.id != SELECTOR_EMPTY_ID]


def selections_to_score_map(selections: list[ChunkSelection]) -> dict[int, int]:
    """Map selector ids to scores, keeping the highest score when ids repeat."""
    scored: dict[int, int] = {}
    for selection in selections:
        scored[selection.id] = max(scored.get(selection.id, 0), selection.score)
    return scored


def filter_selector_selections(
    selections: list[ChunkSelection],
    *,
    min_score: int = LLM_SCORE,
) -> dict[int, int]:
    """Normalize selections and optionally keep ids whose score meets ``min_score``."""
    normalized = normalize_selector_selections(selections)
    scored = selections_to_score_map(normalized)
    if min_score <= 0:
        return scored
    return {chunk_id: score for chunk_id, score in scored.items() if score >= min_score}


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


def _trim_scored_catalog_list(
    items: list[Any],
    *,
    min_score: float,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if _json_item_score(item) < min_score:
            continue
        filtered.append(item)
    filtered.sort(key=_json_item_score, reverse=True)
    return filtered


def trim_catalog_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Drop low-scoring catalog entries before the LLM selector."""
    json_items = data.get("json")
    if isinstance(json_items, list):
        data["json"] = _trim_scored_catalog_list(
            json_items,
            min_score=LLM_TRIM_MIN_JSON_SCORE,
        )

    md_items = data.get("md")
    if isinstance(md_items, list):
        data["md"] = _trim_scored_catalog_list(
            md_items,
            min_score=LLM_TRIM_MIN_JSON_SCORE,
        )

    return data


def prepare_catalog_selector_chunks(
    data: dict[str, Any],
) -> tuple[list[str], dict[int, Any], list[str], list[int], list[ToolSelectorTokenRow]]:
    """Format json/md catalog items as selector chunks with stable global ids."""
    list_keys = [k for k in LLM_CATALOG_LIST_KEYS if k in data and isinstance(data.get(k), list)]

    if not list_keys:
        raise ValueError("No json/md catalog lists found in JSON root.")

    formatted_chunks: list[str] = []
    chunk_token_counts: list[int] = []
    token_rows: list[ToolSelectorTokenRow] = []
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
                token_count = parse_cached_token_count(item)
                item_metadata_storage[global_chunk_id] = {
                    "key": target_key,
                    "item": item,
                    "metadata": {k: item.get(k) for k in keys_to_remove},
                    "token_count": token_count,
                }

                if target_key == "json" and not str(item.get("file_path", "")).strip():
                    continue

                item_for_selector = item.copy()
                for k in model_excluded_fields:
                    item_for_selector.pop(k, None)

                chunk_body = compact_json(item_for_selector)
                tokens_attr = selector_tokens_attr(token_count)
                tag: Literal["tool", "chunk"] = "tool" if target_key == "json" else "chunk"
                formatted_chunks.append(
                    f"<{tag} id={global_chunk_id}{tokens_attr}>\n{chunk_body}\n</{tag}>\n",
                )
                chunk_token_counts.append(token_count or 0)
                file_path = str(item.get("file_path", "")).strip() or None
                token_rows.append(
                    ToolSelectorTokenRow(
                        selector_id=global_chunk_id,
                        tag=tag,
                        tokens=token_count,
                        file_path=file_path,
                    ),
                )
                global_chunk_id += 1

    return formatted_chunks, item_metadata_storage, list_keys, chunk_token_counts, token_rows


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
    cached_content_tokens: int | None = None,
) -> int:
    """Estimate input tokens sent to the LLM selector for one request."""
    if cached_content_tokens is not None and cached_content_tokens > 0:
        prompt_tokens, frame_tokens = count_tokens_batch(
            [system_prompt, _llm_user_message(query, "")],
        )
        return prompt_tokens + frame_tokens + cached_content_tokens

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


def _parse_selector_json(content_val: str) -> RelevantChunkSelections | None:
    try:
        return RelevantChunkSelections.model_validate_json(content_val)
    except Exception:
        import re

        if json_match := re.search(r"\{.*\}", content_val, re.DOTALL):
            try:
                return RelevantChunkSelections.model_validate_json(json_match.group(0))
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
    cached_content_tokens: int | None = None,
    bulk_index: int = 0,
    selector_kind: str = "unknown",
) -> tuple[RelevantChunkSelections, StageTokenUsage]:
    del bulk_index, selector_kind
    configure_litellm_quiet()
    user_message = _llm_user_message(query, chunks_text)
    input_tokens = count_llm_request_tokens(
        query,
        chunks_text,
        system_prompt=system_prompt,
        cached_content_tokens=cached_content_tokens,
    )

    request_kwargs: dict[str, Any] = {
        "model": settings.model_name,
        "api_key": settings.api_key,
    }
    if settings.base_url:
        request_kwargs["api_base"] = settings.base_url

    if settings.responses_api:
        request_kwargs["instructions"] = system_prompt
        request_kwargs["input"] = user_message
        request_kwargs["text_format"] = RelevantChunkSelections
        response: Any = responses(**request_kwargs)
    else:
        request_kwargs["messages"] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        request_kwargs["response_format"] = RelevantChunkSelections
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
        return RelevantChunkSelections(selections=[]), usage

    parsed = _parse_selector_json(content_val)
    if parsed is None:
        _warn_empty_selector_response(
            response,
            model_name=settings.model_name,
            reason="structured content failed Pydantic validation",
        )
        return RelevantChunkSelections(selections=[]), usage

    normalized = normalize_selector_selections(parsed.selections)
    return RelevantChunkSelections(selections=normalized), usage


def _selector_system_prompt_for_budget(
    system_prompt: str,
    *,
    per_bulk_budget: int,
    system_prompt_for_budget: Callable[[int], str] | None,
) -> str:
    if system_prompt_for_budget is not None:
        return system_prompt_for_budget(per_bulk_budget)
    return replace_selector_soft_budget(system_prompt, per_bulk_budget)


def _run_llm_selector_bulk(
    resolved_settings: LlmPruningSettings,
    query: str,
    bulk_text: str,
    *,
    bulk_system_prompt: str,
    cached_content_tokens: int | None = None,
    bulk_index: int = 0,
    selector_kind: str = "unknown",
) -> tuple[dict[int, int], StageTokenUsage]:
    bulk_tokens = count_llm_request_tokens(
        query,
        bulk_text,
        system_prompt=bulk_system_prompt,
        cached_content_tokens=cached_content_tokens,
    )
    logger.info("llm request tokens: %d", bulk_tokens)
    parsed_response, bulk_usage = call_llm(
        resolved_settings,
        query,
        bulk_text,
        system_prompt=bulk_system_prompt,
        cached_content_tokens=cached_content_tokens,
        bulk_index=bulk_index,
        selector_kind=selector_kind,
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

    return selections_to_score_map(parsed_response.selections), bulk_usage


def _merge_selector_scores(
    merged: dict[int, int],
    bulk_scores: dict[int, int],
) -> dict[int, int]:
    for chunk_id, score in bulk_scores.items():
        merged[chunk_id] = max(merged.get(chunk_id, 0), score)
    return merged


def _run_llm_selector_bulks(
    resolved_settings: LlmPruningSettings,
    query: str,
    bulks: list[str],
    *,
    bulk_system_prompt: str,
    bulk_cached_totals: list[int] | None = None,
    selector_kind: str = "tools",
    max_workers: int | None = None,
    phase_timer: PhaseTimer | None = None,
    phase_prefix: str = "tools",
) -> tuple[dict[int, int], StageTokenUsage]:
    merged_scores: dict[int, int] = {}
    total_usage = empty_usage()

    def _cached_for_bulk(index: int) -> int | None:
        if bulk_cached_totals is None or index >= len(bulk_cached_totals):
            return None
        total = bulk_cached_totals[index]
        return total if total > 0 else None

    if len(bulks) <= 1:
        for index, bulk_text in enumerate(bulks):
            bulk_ctx = (
                phase_timer.measure(f"{phase_prefix}:llm-bulk-{index}", selector=selector_kind)
                if phase_timer is not None
                else nullcontext()
            )
            with bulk_ctx:
                bulk_scores, bulk_usage = _run_llm_selector_bulk(
                    resolved_settings,
                    query,
                    bulk_text,
                    bulk_system_prompt=bulk_system_prompt,
                    cached_content_tokens=_cached_for_bulk(index),
                    bulk_index=index,
                    selector_kind=selector_kind,
                )
            total_usage = total_usage.merge(bulk_usage)
            _merge_selector_scores(merged_scores, bulk_scores)
        return merged_scores, total_usage

    from cyt.config import max_prune_batch_workers
    from cyt.pruning.parallel import run_parallel

    worker_cap = max_workers if max_workers is not None else max_prune_batch_workers()

    def _run_bulk(index: int, bulk_text: str) -> tuple[dict[int, int], StageTokenUsage]:
        bulk_ctx = (
            phase_timer.measure(f"{phase_prefix}:llm-bulk-{index}", selector=selector_kind)
            if phase_timer is not None
            else nullcontext()
        )
        with bulk_ctx:
            return _run_llm_selector_bulk(
                resolved_settings,
                query,
                bulk_text,
                bulk_system_prompt=bulk_system_prompt,
                cached_content_tokens=_cached_for_bulk(index),
                bulk_index=index,
                selector_kind=selector_kind,
            )

    work = {
        str(index): (lambda idx=index, text=bulk_text: _run_bulk(idx, text))
        for index, bulk_text in enumerate(bulks)
    }
    parallel_results = run_parallel(work, max_workers=worker_cap)
    for index in range(len(bulks)):
        bulk_scores, bulk_usage = parallel_results[str(index)]
        total_usage = total_usage.merge(bulk_usage)
        _merge_selector_scores(merged_scores, bulk_scores)
    return merged_scores, total_usage


def llm_select_ids(
    query: str,
    system_prompt: str,
    formatted_items: list[str],
    *,
    chunk_token_counts: list[int] | None = None,
    wrap_agent_tools: bool = False,
    soft_budget_total: int = SELECTOR_SOFT_BUDGET_TOOLS_TOTAL,
    soft_budget_min: int = SELECTOR_SOFT_BUDGET_MIN,
    system_prompt_for_budget: Callable[[int], str] | None = None,
    config: dict[str, Any] | None = None,
    settings: LlmPruningSettings | None = None,
    selector_kind: str = "tools",
    phase_timer: PhaseTimer | None = None,
    phase_prefix: str = "tools",
) -> tuple[dict[int, int], StageTokenUsage]:
    """Run LLM selector over formatted chunks; return id→score survivors."""
    if not formatted_items:
        return {}, empty_usage()

    from cyt.config import max_prune_batch_workers, selector_bulk_max_tokens
    from cyt.pruners.split import split_chunks_into_bulks

    resolved_settings = llm_pruning_settings(config, settings=settings)
    bulks, bulk_cached_totals = split_chunks_into_bulks(
        query,
        system_prompt,
        formatted_items,
        chunk_token_counts=chunk_token_counts,
        wrap_agent_tools=wrap_agent_tools,
        max_tokens=selector_bulk_max_tokens(config, selector_kind=selector_kind),
    )
    per_bulk_budget = per_bulk_soft_budget(
        soft_budget_total,
        len(bulks),
        min_budget=soft_budget_min,
    )
    bulk_system_prompt = _selector_system_prompt_for_budget(
        system_prompt,
        per_bulk_budget=per_bulk_budget,
        system_prompt_for_budget=system_prompt_for_budget,
    )
    selected_scores, total_usage = _run_llm_selector_bulks(
        resolved_settings,
        query,
        bulks,
        bulk_system_prompt=bulk_system_prompt,
        bulk_cached_totals=bulk_cached_totals,
        selector_kind=selector_kind,
        max_workers=max_prune_batch_workers(config),
        phase_timer=phase_timer,
        phase_prefix=phase_prefix,
    )

    if total_usage.input_tokens or total_usage.output_tokens:
        log_token_usage(
            f"pruning model tokens ({selector_kind})",
            total_usage.input_tokens + total_usage.output_tokens,
        )

    return selected_scores, total_usage


def apply_llm_chunk_score(item: dict[str, Any], llm_score: int) -> None:
    item["score"] = llm_score / 100.0


def apply_llm_md_score(item: dict[str, Any], llm_score: int) -> None:
    apply_llm_chunk_score(item, llm_score)


def build_full_chunk_score_map(
    item_metadata_storage: dict[int, Any],
    llm_scores: dict[int, int],
) -> dict[int, int]:
    return {chunk_id: llm_scores.get(chunk_id, 0) for chunk_id in item_metadata_storage}


def apply_selector_ids_to_catalog(
    data: dict[str, Any],
    item_metadata_storage: dict[int, Any],
    selected_scores: dict[int, int],
    list_keys: list[str],
) -> dict[str, Any]:
    """Rebuild catalog lists from selector metadata with LLM scores on every chunk."""
    new_data_lists: dict[str, list[dict[str, Any]]] = {k: [] for k in list_keys}

    for chunk_id, storage in item_metadata_storage.items():
        target_key: str = storage["key"]
        item: dict[str, Any] = storage["item"]
        metadata: dict[str, Any | None] = storage["metadata"]

        for k, v in metadata.items():
            if v is not None and k != "score":
                item[k] = v

        apply_llm_chunk_score(item, selected_scores.get(chunk_id, 0))
        new_data_lists[target_key].append(item)

    for k, v in new_data_lists.items():
        data[k] = v

    return data


def prune_llm_catalog(
    data: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Drop catalog items below LLM_SCORE / LLM_ENUM_SCORE after LLM selector scoring."""
    if catalog_below_minimum_tools(data, llm_minimum_tools(config), stage="llm"):
        return data

    return prune_catalog_lists(
        data,
        json_threshold=llm_score(config) / 100.0,
        md_threshold=llm_enum_score(config) / 100.0,
        prune_enums=True,
    )


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
    catalog_bulk_id: str | None = None,
    phase_timer: PhaseTimer | None = None,
    phase_prefix: str = "tools",
) -> tuple[dict[str, Any], dict[str, Any], StageTokenUsage]:
    """Select relevant catalog chunks via LLM; same contract as rerank_catalog_dict."""
    policy_ctx = resolve_policy_context(
        ctx=ctx,
        system_policy=system_policy,
        mcp_policy=mcp_policy,
        config=config,
    )
    data, pinned, skip_scoring = prepare_catalog_for_scoring(data, policy_ctx)
    if skip_scoring:
        snapshot = copy.deepcopy(data)
        return data, snapshot, empty_usage()

    if catalog_below_minimum_tools(data, llm_minimum_tools(config), stage="llm"):
        snapshot = copy.deepcopy(data)
        return data, snapshot, empty_usage()

    resolved_config = config or load_config()
    prepared = None
    if catalog_bulk_id:
        from cyt.tools.catalog_cache import get_prepared_selector_chunks

        prepared = get_prepared_selector_chunks(
            data,
            bulk_id=catalog_bulk_id,
            config=resolved_config,
        )

    if prepared is not None:
        chunk_ctx = (
            phase_timer.measure(f"{phase_prefix}:selector-chunks-cache", bulk_id=catalog_bulk_id)
            if phase_timer is not None
            else nullcontext()
        )
        with chunk_ctx:
            formatted_chunks, item_metadata_storage, list_keys, chunk_token_counts, _token_rows = (
                prepared
            )
    else:
        chunk_ctx = (
            phase_timer.measure(f"{phase_prefix}:selector-chunks-prepare")
            if phase_timer is not None
            else nullcontext()
        )
        with chunk_ctx:
            formatted_chunks, item_metadata_storage, list_keys, chunk_token_counts, _token_rows = (
                prepare_catalog_selector_chunks(data)
            )

    llm_scores, total_usage = llm_select_ids(
        query,
        tool_selector_system_prompt(resolved_config),
        formatted_chunks,
        chunk_token_counts=chunk_token_counts,
        wrap_agent_tools=True,
        system_prompt_for_budget=lambda budget: tool_selector_system_prompt(
            resolved_config,
            soft_budget=budget,
        ),
        soft_budget_total=tools_selector_soft_budget(resolved_config),
        config=resolved_config,
        settings=settings,
        phase_timer=phase_timer,
        phase_prefix=phase_prefix,
    )

    full_scores = build_full_chunk_score_map(item_metadata_storage, llm_scores)
    scored = apply_selector_ids_to_catalog(
        data,
        item_metadata_storage,
        full_scores,
        list_keys,
    )
    scored_snapshot = copy.deepcopy(scored)
    return (
        finalize_catalog_result(scored, pinned, merge_pinned=merge_pinned),
        scored_snapshot,
        total_usage,
    )


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
        scored, _scored, _tokens = llm_catalog_dict(data, args.query, ctx=ctx)
        result = prune_llm_catalog(scored, config=config)
        output_data = json.dumps(result, indent=2)
        if args.output_json:
            from cyt.safe_path import default_cli_base, write_text_under

            output_path = write_text_under(
                args.output_json,
                default_cli_base(),
                output_data,
                label="output JSON",
            )
            print(f"Results saved to {output_path}")
        else:
            print(output_data)
    except Exception as e:
        print(f"Error during LLM processing: {e}", file=sys.stderr)
        sys.exit(1)


prepare_chunks = prepare_catalog_selector_chunks
process_results = apply_selector_ids_to_catalog

if __name__ == "__main__":
    main()
