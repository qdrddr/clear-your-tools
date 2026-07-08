"""UserPromptSubmit tools hook handler."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from cyt.config import tools_hook_file_missing
from cyt.indexer.tokens import count_json_tokens
from cyt.injection.pre_exposed import filter_pre_exposed_tools
from cyt.injection.session_text import session_text_from_hook_payload
from cyt.proxy.anthropic import PruneResult
from cyt.skills.budget import count_hook_request_tokens
from cyt.skills.hook_payload import prompt_from_payload
from cyt.skills.hook_quiet import hook_safe_stdout
from cyt.skills.transcript import (
    resolve_model,
    skills_search_query_from_hook_payload,
)
from cyt.tools.budget import (
    resolve_tools_inject_budget,
    tools_budget_precheck,
    tools_inject_allowed,
)
from cyt.tools.inject import format_agent_tools, injection_token_count
from cyt.tools.prune import prune_tools_for_query
from cyt.tools.registry import load_tool_catalog
from cyt.tools.stats import record_tools_hook_injection

logger = logging.getLogger(__name__)


def _missing_tools_catalog_outcome(
    result: PruneResult,
    *,
    debug: bool,
) -> tuple[str, dict[str, Any], str]:
    details: dict[str, Any] = {"catalog_tool_count": 0, "prune_status": result.status}
    if debug:
        return "skipped_missing_tools_catalog", details, ""
    return "skipped_missing_tools_catalog", {}, ""


def handle_user_prompt_tools(
    payload: dict[str, Any],
    config: dict[str, Any],
    *,
    plain_output: bool = False,
    debug: bool = False,
    io_guarded: bool = False,
    allow_transcript_file_read: bool = True,
) -> tuple[str, dict[str, Any], str]:
    """Return (outcome, details, injection_text)."""
    if not tools_inject_allowed(config, "hook", cli_prompt=plain_output):
        return "skipped_inject_via_proxy", {}, ""

    if tools_hook_file_missing(config):
        details: dict[str, Any] = {"catalog_tool_count": 0, "prune_status": "missing_catalog"}
        if debug:
            return "skipped_missing_tools_catalog", details, ""
        return "skipped_missing_tools_catalog", {}, ""

    query = skills_search_query_from_hook_payload(
        payload,
        allow_file_read=allow_transcript_file_read,
    )
    if not query:
        return "user_prompt_missing_prompt", {}, ""

    if not tools_budget_precheck(config):
        return "skipped_budget_zero", {}, ""

    request_tokens = count_hook_request_tokens(payload)
    budget_max, budget_debug = resolve_tools_inject_budget(
        config,
        total_request_tokens=request_tokens,
    )
    if budget_max <= 0:
        return "skipped_budget_zero", {"request_tokens": request_tokens}, ""

    model = resolve_model(payload, allow_file_read=allow_transcript_file_read) or "hook"
    pruned, result, catalog = _prune_hook_tool_catalog(query, config, io_guarded=io_guarded)
    if catalog is None:
        return _missing_tools_catalog_outcome(result, debug=debug)
    if not pruned:
        return (
            "user_prompt_no_tool_matches",
            {
                "resolved_model": model,
                "prune_status": result.status,
                "catalog_tool_count": len(catalog),
                "pruned_tool_count": 0,
            },
            "",
        )

    session_text = session_text_from_hook_payload(
        payload,
        allow_file_read=allow_transcript_file_read,
    )
    gated = filter_pre_exposed_tools(pruned, session_text)
    injected = format_agent_tools(gated)
    if not injected:
        return "user_prompt_empty_tool_injection", {"resolved_model": model}, ""

    return _finish_tools_hook_injection(
        payload=payload,
        config=config,
        query=query,
        model=model,
        result=result,
        catalog=catalog,
        injected=injected,
        request_tokens=request_tokens,
        budget_debug=budget_debug,
        debug=debug,
    )


def _finish_tools_hook_injection(
    *,
    payload: dict[str, Any],
    config: dict[str, Any],
    query: str,
    model: str,
    result: PruneResult,
    catalog: list[dict[str, Any]],
    injected: str,
    request_tokens: int,
    budget_debug: dict[str, int],
    debug: bool,
) -> tuple[str, dict[str, Any], str]:
    prompt = prompt_from_payload(payload) or query
    tools_out = injection_token_count(injected)
    tools_in = result.tokens_in or count_json_tokens(catalog)
    if tools_out > 0:
        record_tools_hook_injection(
            query=prompt,
            model_name=model,
            tools_in=tools_in,
            tools_out=tools_out,
            prompt_tokens=request_tokens,
            pruning_stages=result.pruning_token_usage,
            tools_final_md=injected if debug else None,
            config=config,
            prune_status=result.status,
        )

    details: dict[str, Any] = {
        "resolved_model": model,
        "prune_status": result.status,
        "request_tokens": request_tokens,
        "budget_debug": budget_debug,
        "catalog_tool_count": len(catalog),
        "pruned_tool_count": len(result.tools or []),
    }
    if debug:
        pruned_names = [str(tool.get("name", "")) for tool in (result.tools or [])[:20]]
        details["pruned_tool_names"] = pruned_names
        details["injected_tools"] = injected
    return "user_prompt_tools_injected", details, injected


def finish_tools_hook_injection_from_coordinator(
    *,
    payload: dict[str, Any],
    config: dict[str, Any],
    query: str,
    model: str,
    result: PruneResult,
    catalog: list[dict[str, Any]],
    injected: str,
    request_tokens: int,
    budget_debug: dict[str, int],
    debug: bool,
) -> tuple[str, dict[str, Any], str]:
    return _finish_tools_hook_injection(
        payload=payload,
        config=config,
        query=query,
        model=model,
        result=result,
        catalog=catalog,
        injected=injected,
        request_tokens=request_tokens,
        budget_debug=budget_debug,
        debug=debug,
    )


def _prune_hook_tool_catalog(
    query: str,
    config: dict[str, Any],
    *,
    io_guarded: bool = False,
) -> tuple[list[dict[str, Any]], PruneResult, list[dict[str, Any]] | None]:
    stdout_guard = contextlib.nullcontext() if io_guarded else hook_safe_stdout()
    with stdout_guard:
        catalog = load_tool_catalog(config)
        if catalog is None:
            return (
                [],
                PruneResult(
                    tools=None,
                    status="skipped",
                    query=query,
                    tools_in=0,
                    mcp_tools_in=0,
                    tools_out=None,
                    error="missing catalog",
                ),
                None,
            )
        result = prune_tools_for_query(catalog, query, config=config)
    return result.tools or [], result, catalog
