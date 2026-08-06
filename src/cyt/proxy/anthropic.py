"""Anthropic API request transform for the LLM proxy."""

from __future__ import annotations

import copy
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cyt.skills.proxy_inject import DeferredSkillsContext, SkillsProxyInjectMeta

from cyt.common.search_query import format_search_query
from cyt.config import (
    load_config,
)
from cyt.indexer.tokens import count_json_tokens
from cyt.pruners.policies import (
    policy_context_from_config,
    request_pass_through,
)
from cyt.pruners.remote import PrunerSettingsCache
from cyt.pruners.tools_filter import (
    LLM_STAGE_MAX_ATTEMPTS,
    _run_catalog_pruning,
    _run_llm_stage,
    _run_pipeline_stage,
    _snapshot_catalog,
    filter_tools_for_query,
    merge_api_tool_onto_original,
)
from cyt.tools.budget import tools_inject_allowed
from cyt_core.types.prune import PruneResult

__all__ = [
    "LLM_STAGE_MAX_ATTEMPTS",
    "PruneResult",
    "_run_catalog_pruning",
    "_run_llm_stage",
    "_run_pipeline_stage",
    "_snapshot_catalog",
    "filter_tools_for_query",
    "format_search_query",
    "merge_api_tool_onto_original",
]

logger = logging.getLogger(__name__)

SYSTEM_REMINDER_PREFIX = "<system-reminder>"

_META_QUERY_PATTERNS = (
    re.compile(r"stepped away", re.IGNORECASE),
    re.compile(r"coming back", re.IGNORECASE),
    re.compile(r"recap in under \d+ words", re.IGNORECASE),
)

_ERROR_QUERY_PATTERNS = (
    re.compile(r"malformed and could not be parsed", re.IGNORECASE),
    re.compile(r"please retry\.?$", re.IGNORECASE),
)


def _is_junk_text(text: str) -> bool:
    stripped = text.strip()
    return not stripped or stripped == " " or stripped.startswith(SYSTEM_REMINDER_PREFIX)


def _clean_content_block(block: dict[str, Any]) -> dict[str, Any] | None:
    block_type = block.get("type")
    if block_type == "text":
        text = block.get("text", "")
        if not isinstance(text, str) or _is_junk_text(text):
            return None
        out: dict[str, Any] = {"type": "text", "text": text}
        citations = block.get("citations")
        if isinstance(citations, list) and citations:
            out["citations"] = citations
        return out
    if block_type == "thinking":
        thinking = block.get("thinking")
        if thinking is None:
            return None
        return {"type": "thinking", "thinking": thinking}
    if block_type == "tool_result":
        content = block.get("content")
        if isinstance(content, list):
            return None
        out = {"type": "tool_result", "content": content}
        if "is_error" in block:
            out["is_error"] = block["is_error"]
        if "tool_use_id" in block:
            out["tool_use_id"] = block["tool_use_id"]
        return out
    return None


def clean_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter messages for rerank query extraction only (not forwarded upstream)."""
    cleaned: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") not in ("user", "assistant"):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            if _is_junk_text(content):
                continue
            cleaned.append({"role": msg["role"], "content": content})
            continue
        if not isinstance(content, list):
            continue
        blocks = [b for b in (_clean_content_block(x) for x in content) if b is not None]
        if not blocks:
            continue
        if len(blocks) == 1 and blocks[0].get("type") == "text":
            cleaned.append({"role": msg["role"], "content": blocks[0]["text"]})
        else:
            cleaned.append({"role": msg["role"], "content": blocks})
    return cleaned


def _user_message_has_text(msg: dict[str, Any]) -> bool:
    content = msg.get("content")
    if isinstance(content, str):
        return not _is_junk_text(content)
    if isinstance(content, list):
        return any(
            isinstance(b, dict) and b.get("type") == "text" and not _is_junk_text(b.get("text", ""))
            for b in content
        )
    return False


def _text_from_user_message(msg: dict[str, Any]) -> str | None:
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text", "")
            if isinstance(text, str) and not _is_junk_text(text):
                parts.append(text.strip())
        if parts:
            return "\n".join(parts)
    return None


def _is_meta_user_query(text: str) -> bool:
    for pattern in _META_QUERY_PATTERNS:
        if pattern.search(text):
            return True
    if "recap" in text.lower() and "words" in text.lower() and len(text) < 200:
        return True
    return False


def _is_error_user_query(text: str) -> bool:
    for pattern in _ERROR_QUERY_PATTERNS:
        if pattern.search(text):
            return True
    return False


def _is_junk_user_query(text: str) -> bool:
    return _is_meta_user_query(text) or _is_error_user_query(text)


def _query_rank(text: str) -> tuple[int, int]:
    """Higher sorts first: prefer real tasks over errors/recap boilerplate."""
    if _is_junk_user_query(text):
        return (-1, len(text))
    rank = 0
    if "src/" in text or ".py" in text or ".ts" in text:
        rank += 1000
    if len(text) > 80:
        rank += 500
    return (rank, len(text))


def extract_user_query(cleaned_messages: list[dict[str, Any]]) -> str | None:
    """Pick the best substantive user query, skipping recap/meta/error turns when possible."""
    candidates: list[str] = []
    for msg in reversed(cleaned_messages):
        if msg.get("role") != "user":
            continue
        if not _user_message_has_text(msg):
            continue
        if text := _text_from_user_message(msg):
            candidates.append(text)

    if not candidates:
        return None

    non_junk = [text for text in candidates if not _is_junk_user_query(text)]
    pool = non_junk if non_junk else candidates
    return max(pool, key=_query_rank)


def _text_from_assistant_message(msg: dict[str, Any]) -> str | None:
    content = msg.get("content")
    if isinstance(content, str):
        stripped = content.strip()
        return stripped if stripped and not _is_junk_text(stripped) else None
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text", "")
                if isinstance(text, str) and not _is_junk_text(text):
                    parts.append(text.strip())
            elif block_type == "thinking":
                thinking = block.get("thinking", "")
                if isinstance(thinking, str) and thinking.strip():
                    parts.append(thinking.strip())
        if parts:
            return "\n".join(parts)
    return None


def extract_last_assistant_message(cleaned_messages: list[dict[str, Any]]) -> str | None:
    """Return text from the latest assistant message (text and thinking blocks)."""
    for msg in reversed(cleaned_messages):
        if msg.get("role") != "assistant":
            continue
        if text := _text_from_assistant_message(msg):
            return text
    return None


def _anthropic_pass_through_prune_result(tools: list[dict[str, Any]]) -> PruneResult:
    tokens_in = count_json_tokens(tools)
    return PruneResult(
        tools=None,
        status="pass_through",
        query=None,
        tools_in=len(tools),
        mcp_tools_in=sum(1 for t in tools if t.get("name")),
        tools_out=len(tools),
        error=None,
        tokens_in=tokens_in,
        tokens_out=tokens_in,
        tokens_saved=0,
    )


def _anthropic_skipped_no_query_prune_result(tools: list[dict[str, Any]]) -> PruneResult:
    return PruneResult(
        tools=None,
        status="skipped",
        query=None,
        tools_in=len(tools),
        mcp_tools_in=sum(1 for t in tools if t.get("name")),
        tools_out=None,
        error="no user query extracted",
    )


def _format_gated_agent_tools(
    mcp_tools: list[dict[str, Any]],
    session_text: str,
) -> str:
    from cyt.injection.pre_exposed import filter_pre_exposed_tools
    from cyt.tools.inject import format_agent_tools

    gated = filter_pre_exposed_tools(mcp_tools, session_text)
    return format_agent_tools(gated)


def _anthropic_user_message_tools_inject(
    original: dict[str, Any],
    result: PruneResult,
    *,
    session_text: str,
) -> tuple[dict[str, Any], str]:
    from cyt.proxy.user_message_inject import (
        anthropic_root_tools_with_mcp_stubs,
        anthropic_tools_for_user_message_inject,
        split_tools_for_root_and_inject,
    )

    source_tools = original.get("tools") or []
    if not isinstance(source_tools, list) or not source_tools:
        return original, ""

    if result.status == "pass_through" or result.tools is None:
        mcp_tools, system_tools = split_tools_for_root_and_inject(source_tools)
        original["tools"] = anthropic_root_tools_with_mcp_stubs(system_tools, source_tools)
        return original, _format_gated_agent_tools(mcp_tools, session_text)

    pruned_tools = result.tools if isinstance(result.tools, list) else []
    if not pruned_tools:
        return original, ""

    mcp_tools, system_tools = anthropic_tools_for_user_message_inject(
        source_tools,
        pruned_tools,
    )
    original["tools"] = anthropic_root_tools_with_mcp_stubs(system_tools, source_tools)
    return original, _format_gated_agent_tools(mcp_tools, session_text)


def _anthropic_finish_transform(
    original: dict[str, Any],
    result: PruneResult,
    skills_meta: SkillsProxyInjectMeta,
    deferred: DeferredSkillsContext | None,
    config: dict[str, Any] | None,
    skill_out: dict[str, Any],
    query: str | None,
    pruner_settings: PrunerSettingsCache | None = None,
) -> tuple[dict[str, Any], PruneResult, SkillsProxyInjectMeta]:
    from cyt.config import inject_into_user_message, load_config
    from cyt.injection.session_text import session_text_from_proxy_body
    from cyt.proxy.user_message_inject import (
        already_has_user_turn_injection,
        append_injection_to_body,
    )
    from cyt.skills.proxy_inject import finish_deferred_skills_anthropic
    from cyt.tools.budget import tools_inject_allowed

    resolved_config = config or load_config()
    user_message_inject = inject_into_user_message(
        resolved_config,
        agent="claude",
    ) and tools_inject_allowed(
        resolved_config,
        "proxy",
        agent="claude",
    )
    deferred_tools_text = ""
    proxy_session_text = (
        session_text_from_proxy_body(original, "anthropic") if user_message_inject else ""
    )

    if result.status != "applied" or result.tools is None:
        if result.status == "failed":
            logger.warning("tool pruning failed: %s", result.error)
        if result.status != "pass_through":
            original, skills_meta = finish_deferred_skills_anthropic(
                original,
                skills_meta,
                deferred,
                config,
                matches=skill_out.get("matches"),
                query=query,
                prune_result=result,
                pruner_settings=pruner_settings,
            )
            return original, result, skills_meta

        if user_message_inject:
            original, deferred_tools_text = _anthropic_user_message_tools_inject(
                original,
                result,
                session_text=proxy_session_text,
            )
    elif user_message_inject:
        original, deferred_tools_text = _anthropic_user_message_tools_inject(
            original,
            result,
            session_text=proxy_session_text,
        )
    elif result.tools is not None:
        original["tools"] = result.tools

    original, skills_meta = finish_deferred_skills_anthropic(
        original,
        skills_meta,
        deferred,
        config,
        matches=skill_out.get("matches"),
        query=query,
        prune_result=result,
        pruner_settings=pruner_settings,
    )

    if (
        user_message_inject
        and deferred_tools_text
        and not already_has_user_turn_injection(original, "anthropic", tag="<agent-tools>")
    ):
        original = append_injection_to_body(original, deferred_tools_text, kind="anthropic")

    return original, result, skills_meta


def transform_anthropic_request(
    body: dict[str, Any],
    pruning_pipeline: list[str] | None = None,
    capture_decomposed_catalog: bool = False,
    config: dict[str, Any] | None = None,
    pruner_settings: PrunerSettingsCache | None = None,
) -> tuple[dict[str, Any], PruneResult | None, Any]:
    """Return body (tools replaced when pruning applied), pruning metadata, and skills meta."""
    from cyt.skills.proxy_inject import (
        SkillsProxyInjectMeta,
        finish_deferred_skills_anthropic,
        prepare_deferred_skills_context,
    )

    original = copy.deepcopy(body)
    skills_meta = SkillsProxyInjectMeta()

    messages = original.get("messages") or []
    tools = original.get("tools") or []

    cleaned = clean_messages(messages)
    user_query = extract_user_query(cleaned)
    query = (
        format_search_query(user_query, extract_last_assistant_message(cleaned))
        if user_query
        else None
    )
    deferred = prepare_deferred_skills_context(
        config,
        query,
        kind="anthropic",
        body=original,
    )
    skill_out = deferred.skill_out if deferred is not None else {}

    if not tools:
        original, skills_meta = finish_deferred_skills_anthropic(
            original,
            skills_meta,
            deferred,
            config,
            query=query,
            pruner_settings=pruner_settings,
        )
        return original, None, skills_meta

    if request_pass_through(tools, policy_context_from_config()):
        return _anthropic_finish_transform(
            original,
            _anthropic_pass_through_prune_result(tools),
            skills_meta,
            deferred,
            config,
            skill_out,
            query,
            pruner_settings=pruner_settings,
        )

    if not user_query:
        logger.warning("no user query extracted; forwarding original tools")
        original, skills_meta = finish_deferred_skills_anthropic(
            original,
            skills_meta,
            deferred,
            config,
            pruner_settings=pruner_settings,
        )
        return original, _anthropic_skipped_no_query_prune_result(tools), skills_meta

    assert query is not None
    from cyt.pruning.coordinator import ToolSource, coordinate_skills_tools_prune

    skill_entries = (
        deferred.skill_entries if deferred is not None and deferred.skills_allowed else None
    )
    resolved_config = config or load_config()
    tools_allowed = tools_inject_allowed(resolved_config, "proxy", agent="claude")
    coordinated = coordinate_skills_tools_prune(
        query,
        resolved_config,
        [ToolSource("root", tools)],
        skill_entries=skill_entries,
        upstream_kind="anthropic",
        capture_decomposed_catalog=capture_decomposed_catalog,
        pruner_settings=pruner_settings,
        skills_allowed=bool(deferred is not None and deferred.skills_allowed),
        tools_allowed=tools_allowed,
        tools_pipeline_override=pruning_pipeline,
        skill_out=skill_out if deferred is not None else None,
    )
    result = coordinated.prune_results.get("root")
    if result is None:
        result = PruneResult(
            tools=None,
            status="skipped",
            query=query,
            tools_in=len(tools),
            mcp_tools_in=sum(1 for t in tools if t.get("name")),
            tools_out=None,
            error="no tool prune result",
        )
    if coordinated.skill_matches is not None:
        skill_out["matches"] = coordinated.skill_matches
    return _anthropic_finish_transform(
        original,
        result,
        skills_meta,
        deferred,
        config,
        skill_out,
        query,
        pruner_settings=pruner_settings,
    )
