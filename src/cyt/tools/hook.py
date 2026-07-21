"""UserPromptSubmit tools hook handler."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from cyt.config import (
    tools_hook_file_missing,
    uses_executor_tool_catalog,
    uses_mcpc_tool_catalog,
)
from cyt.indexer.tokens import count_json_tokens
from cyt.injection.mcpc_pre_exposed import filter_pre_exposed_mcpc_tools
from cyt.injection.pre_exposed import filter_pre_exposed_tools
from cyt.injection.session_gate import gate_tools_for_session
from cyt.injection.session_log import SessionLogIndex, combined_session_text
from cyt.injection.session_text import session_text_from_hook_payload
from cyt.mcpc.readiness import mcpc_hook_catalog_usable
from cyt.proxy.anthropic import PruneResult
from cyt.pruners.remote import PrunerSettingsCache
from cyt.skills.budget import count_hook_request_tokens
from cyt.skills.hook_payload import prompt_from_payload, workspace_paths_for_tools_inject
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
from cyt.tools.mcpc_inject import format_mcpc_agent_tools
from cyt.tools.mcpc_prune import split_mcpc_prune_result
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


def _format_hook_tool_injection(
    gated: list[dict[str, Any]],
    config: dict[str, Any],
    payload: dict[str, Any],
    *,
    session_text: str = "",
    surviving_instruction_sessions: set[str] | None = None,
) -> str:
    workspace_paths = workspace_paths_for_tools_inject(payload)
    if uses_mcpc_tool_catalog(config):
        return format_mcpc_agent_tools(
            gated,
            workspace_paths=workspace_paths,
            session_text=session_text,
            surviving_instruction_sessions=surviving_instruction_sessions,
        )
    return format_agent_tools(
        gated,
        include_executor_workspace_note=uses_executor_tool_catalog(config),
        workspace_paths=workspace_paths,
    )


def gate_and_format_hook_tools(
    pruned_tools: list[dict[str, Any]],
    *,
    config: dict[str, Any],
    payload: dict[str, Any],
    session_text: str,
    catalog_tools: list[dict[str, Any]] | None = None,
    session_index: SessionLogIndex | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Apply session-log gate, pre-exposure, and format hook tool injection."""
    surviving_instruction_sessions: set[str] | None = None
    tools = pruned_tools
    index = session_index or SessionLogIndex.from_payload(payload)
    combined_text = combined_session_text(session_text, index)

    if uses_mcpc_tool_catalog(config):
        tools, surviving_instruction_sessions = split_mcpc_prune_result(pruned_tools)
        session_gated, log_entries, _full_flags = gate_tools_for_session(
            tools,
            config=config,
            session_text=session_text,
            index=index,
            catalog_tools=catalog_tools,
        )
        gated = filter_pre_exposed_mcpc_tools(session_gated, combined_text)
    else:
        session_gated, log_entries, _full_flags = gate_tools_for_session(
            tools,
            config=config,
            session_text=session_text,
            index=index,
            catalog_tools=catalog_tools,
        )
        gated = filter_pre_exposed_tools(session_gated, combined_text)

    from cyt.injection.session_log_build import CatalogKind, tool_item_key

    catalog_kind: CatalogKind = "mcpc" if uses_mcpc_tool_catalog(config) else "executor"
    gated_keys = {tool_item_key(tool, catalog=catalog_kind) for tool in gated}
    filtered_logs = [entry for entry in log_entries if str(entry.get("key") or "") in gated_keys]

    formatted = _format_hook_tool_injection(
        gated,
        config,
        payload,
        session_text=combined_text,
        surviving_instruction_sessions=surviving_instruction_sessions,
    )
    return formatted, filtered_logs


def _skipped_mcpc_unavailable_outcome(
    config: dict[str, Any],
    *,
    debug: bool,
) -> tuple[str, dict[str, Any], str] | None:
    if not uses_mcpc_tool_catalog(config) or mcpc_hook_catalog_usable(config):
        return None
    if debug:
        return "skipped_mcpc_unavailable", {"catalog_tool_count": 0}, ""
    return "skipped_mcpc_unavailable", {}, ""


def _hook_tools_preflight_outcome(
    config: dict[str, Any],
    *,
    plain_output: bool,
    debug: bool,
) -> tuple[str, dict[str, Any], str] | None:
    if not tools_inject_allowed(config, "hook", cli_prompt=plain_output):
        return "skipped_inject_via_proxy", {}, ""
    if tools_hook_file_missing(config):
        details: dict[str, Any] = {"catalog_tool_count": 0, "prune_status": "missing_catalog"}
        if debug:
            return "skipped_missing_tools_catalog", details, ""
        return "skipped_missing_tools_catalog", {}, ""
    return _skipped_mcpc_unavailable_outcome(config, debug=debug)


def handle_user_prompt_tools(
    payload: dict[str, Any],
    config: dict[str, Any],
    *,
    plain_output: bool = False,
    debug: bool = False,
    io_guarded: bool = False,
    allow_transcript_file_read: bool = True,
    pruner_settings: PrunerSettingsCache | None = None,
) -> tuple[str, dict[str, Any], str]:
    """Return (outcome, details, injection_text)."""
    preflight = _hook_tools_preflight_outcome(config, plain_output=plain_output, debug=debug)
    if preflight is not None:
        return preflight

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
    pruned, result, catalog = _prune_hook_tool_catalog(
        query,
        config,
        io_guarded=io_guarded,
        pruner_settings=pruner_settings,
    )
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
    injected, session_log = gate_and_format_hook_tools(
        pruned,
        config=config,
        payload=payload,
        session_text=session_text,
        catalog_tools=catalog,
    )
    if not injected:
        return "user_prompt_empty_tool_injection", {"resolved_model": model}, ""

    outcome, details, injected_text = _finish_tools_hook_injection(
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
    if session_log:
        details["session_log"] = session_log
    return outcome, details, injected_text


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
    pruner_settings: PrunerSettingsCache | None = None,
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
        result = prune_tools_for_query(
            catalog,
            query,
            config=config,
            pruner_settings=pruner_settings,
        )
    return result.tools or [], result, catalog
