"""UserPromptSubmit tools hook handler."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from cyt.cloudflare.readiness import cloudflare_hook_catalog_usable
from cyt.config import (
    tools_hook_file_missing,
    tools_hook_sources,
    uses_executor_tool_catalog,
    uses_mcpc_tool_catalog,
)
from cyt.cyt_mcp.readiness import cyt_mcp_hook_catalog_usable
from cyt.indexer.tokens import count_json_tokens
from cyt.injection.pre_exposure_context import PreExposureContext
from cyt.injection.pre_exposure_pipeline import gate_and_filter_tools
from cyt.injection.rules_refresh import bypass_injection_pre_exposure
from cyt.injection.session_log import SessionLogIndex
from cyt.injection.session_text import session_text_from_hook_payload
from cyt.injection.tool_catalog_emit import append_tool_catalog_to_details
from cyt.mcpc.readiness import mcpc_hook_catalog_usable
from cyt.proxy.anthropic import PruneResult
from cyt.pruners.remote import PrunerSettingsCache
from cyt.pruning.hook_bridge import run_hook_coordinated_prune
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
from cyt.tools.source_inject import (
    format_cloudflare_source_section,
    format_cyt_mcp_source_section,
    format_definitions_source_section,
    format_executor_source_section,
    format_mcp_source_section,
    format_multi_source_agent_tools,
)
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
        session_text=session_text,
    )


def _partition_tools_by_source(
    tools: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]] | None:
    if not any(tool.get("cyt_catalog_source") for tool in tools):
        return None
    return {
        "cyt_mcp": [tool for tool in tools if tool.get("cyt_catalog_source") == "cyt_mcp"],
        "mcpc": [tool for tool in tools if tool.get("cyt_catalog_source") == "mcpc"],
        "cloudflare": [tool for tool in tools if tool.get("cyt_catalog_source") == "cloudflare"],
        "executor": [tool for tool in tools if tool.get("cyt_catalog_source") == "executor"],
        "definitions": [tool for tool in tools if tool.get("cyt_catalog_source") == "definitions"],
    }


def _format_session_text(payload: dict[str, Any], ctx: PreExposureContext) -> str:
    if bypass_injection_pre_exposure(payload):
        return ""
    return ctx.combined_text


def _gate_source_tools(
    tools: list[dict[str, Any]],
    *,
    source_id: str,
    config: dict[str, Any],
    ctx: PreExposureContext,
    catalog_tools: list[dict[str, Any]] | None,
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str] | None]:
    gated, filtered_logs, surviving_instruction_sessions = gate_and_filter_tools(
        tools,
        config=config,
        ctx=ctx,
        catalog_tools=catalog_tools,
        source_id=source_id,
        payload=payload,
    )
    return gated, filtered_logs, surviving_instruction_sessions


def _format_gated_source_section(
    source_id: str,
    gated: list[dict[str, Any]],
    *,
    config: dict[str, Any],
    workspace_paths: list[str],
    combined_text: str,
    surviving_instruction_sessions: set[str] | None,
) -> str:
    if source_id == "mcpc":
        return format_mcp_source_section(
            gated,
            workspace_paths=workspace_paths,
            session_text=combined_text,
            surviving_instruction_sessions=surviving_instruction_sessions,
        )
    if source_id == "cyt_mcp":
        return format_cyt_mcp_source_section(
            gated,
            workspace_paths=workspace_paths,
            session_text=combined_text,
        )
    if source_id == "cloudflare":
        return format_cloudflare_source_section(
            gated,
            workspace_paths=workspace_paths,
        )
    if source_id == "executor":
        return format_executor_source_section(gated, workspace_paths=workspace_paths)
    return format_definitions_source_section(gated, workspace_paths=workspace_paths)


def _gate_and_build_source_sections(
    source_tools: dict[str, list[dict[str, Any]]],
    *,
    config: dict[str, Any],
    ctx: PreExposureContext,
    catalog_tools: list[dict[str, Any]] | None,
    workspace_paths: list[str],
    payload: dict[str, Any],
    format_session_text: str,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    sections: dict[str, str] = {}
    all_logs: list[dict[str, Any]] = []
    for source_id, tools in source_tools.items():
        if not tools:
            continue
        gated, logs, surviving_sessions = _gate_source_tools(
            tools,
            source_id=source_id,
            config=config,
            ctx=ctx,
            catalog_tools=catalog_tools,
            payload=payload,
        )
        all_logs.extend(logs)
        if not gated:
            continue
        section = _format_gated_source_section(
            source_id,
            gated,
            config=config,
            workspace_paths=workspace_paths,
            combined_text=format_session_text,
            surviving_instruction_sessions=surviving_sessions,
        )
        if section:
            sections[source_id] = section
    return sections, all_logs


def _legacy_gate_and_format(
    pruned_tools: list[dict[str, Any]],
    *,
    config: dict[str, Any],
    payload: dict[str, Any],
    ctx: PreExposureContext,
    catalog_tools: list[dict[str, Any]] | None,
    by_source: dict[str, list[dict[str, Any]]] | None,
) -> tuple[str, list[dict[str, Any]]]:
    source_id = next(iter(by_source)) if by_source and len(by_source) == 1 else None
    gated, filtered_logs, surviving_instruction_sessions = gate_and_filter_tools(
        pruned_tools,
        config=config,
        ctx=ctx,
        catalog_tools=catalog_tools,
        source_id=source_id,
        payload=payload,
    )
    formatted = _format_hook_tool_injection(
        gated,
        config,
        payload,
        session_text=_format_session_text(payload, ctx),
        surviving_instruction_sessions=surviving_instruction_sessions,
    )
    return formatted, filtered_logs


def gate_and_format_hook_tools(
    pruned_tools: list[dict[str, Any]],
    *,
    config: dict[str, Any],
    payload: dict[str, Any],
    session_text: str,
    catalog_tools: list[dict[str, Any]] | None = None,
    session_index: SessionLogIndex | None = None,
    prune_results: dict[str, PruneResult] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Apply session-log gate, pre-exposure, and format hook tool injection."""
    ctx = PreExposureContext.for_hook_payload(payload)
    workspace_paths = workspace_paths_for_tools_inject(payload)
    format_session_text = _format_session_text(payload, ctx)

    if prune_results and len(prune_results) > 1:
        source_tools = {
            source_id: (result.tools or [])
            for source_id, result in prune_results.items()
            if result and result.tools
        }
        sections, all_logs = _gate_and_build_source_sections(
            source_tools,
            config=config,
            ctx=ctx,
            catalog_tools=catalog_tools,
            workspace_paths=workspace_paths,
            payload=payload,
            format_session_text=format_session_text,
        )
        formatted = format_multi_source_agent_tools(
            sections,
            workspace_paths=workspace_paths,
            session_text=format_session_text,
        )
        return formatted, all_logs

    by_source = _partition_tools_by_source(pruned_tools)
    if by_source and sum(len(items) for items in by_source.values()) > 0:
        sections, all_logs = _gate_and_build_source_sections(
            by_source,
            config=config,
            ctx=ctx,
            catalog_tools=catalog_tools,
            workspace_paths=workspace_paths,
            payload=payload,
            format_session_text=format_session_text,
        )
        if sections:
            formatted = format_multi_source_agent_tools(
                sections,
                workspace_paths=workspace_paths,
                session_text=format_session_text,
            )
            return formatted, all_logs

    return _legacy_gate_and_format(
        pruned_tools,
        config=config,
        payload=payload,
        ctx=ctx,
        catalog_tools=catalog_tools,
        by_source=by_source,
    )


def _skipped_mcpc_unavailable_outcome(
    config: dict[str, Any],
    *,
    debug: bool,
) -> tuple[str, dict[str, Any], str] | None:
    sources = tools_hook_sources(config)
    if len(sources) != 1 or sources[0] != "mcpc":
        return None
    if mcpc_hook_catalog_usable(config):
        return None
    if debug:
        return "skipped_mcpc_unavailable", {"catalog_tool_count": 0}, ""
    return "skipped_mcpc_unavailable", {}, ""


def _skipped_cloudflare_unavailable_outcome(
    config: dict[str, Any],
    *,
    debug: bool,
) -> tuple[str, dict[str, Any], str] | None:
    sources = tools_hook_sources(config)
    if len(sources) != 1 or sources[0] != "cloudflare":
        return None
    if cloudflare_hook_catalog_usable(config):
        return None
    if debug:
        return "skipped_cloudflare_unavailable", {"catalog_tool_count": 0}, ""
    return "skipped_cloudflare_unavailable", {}, ""


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
    return (
        _skipped_cloudflare_unavailable_outcome(
            config,
            debug=debug,
        )
        or _skipped_cyt_mcp_unavailable_outcome(
            config,
            debug=debug,
        )
        or _skipped_mcpc_unavailable_outcome(
            config,
            debug=debug,
        )
    )


def _skipped_cyt_mcp_unavailable_outcome(
    config: dict[str, Any],
    *,
    debug: bool,
) -> tuple[str, dict[str, Any], str] | None:
    sources = tools_hook_sources(config)
    if len(sources) != 1 or sources[0] != "cyt_mcp":
        return None
    if cyt_mcp_hook_catalog_usable(config):
        return None
    if debug:
        return "skipped_cyt_mcp_unavailable", {"catalog_tool_count": 0}, ""
    return "skipped_cyt_mcp_unavailable", {}, ""


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
    pruned, result, catalog, prune_results = _prune_hook_tool_catalog(
        query,
        config,
        io_guarded=io_guarded,
        pruner_settings=pruner_settings,
    )
    if catalog is None:
        return _missing_tools_catalog_outcome(result, debug=debug)

    catalog_session_details: dict[str, Any] = {}
    append_tool_catalog_to_details(
        catalog_session_details,
        catalog,
        payload=payload,
        tools_inject_enabled=True,
    )
    catalog_session_log = catalog_session_details.get("session_log") or []

    if not pruned:
        return (
            "user_prompt_no_tool_matches",
            {
                "resolved_model": model,
                "prune_status": result.status,
                "catalog_tool_count": len(catalog),
                "pruned_tool_count": 0,
                "tools_inject_enabled": True,
                "session_log": catalog_session_log,
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
        prune_results=prune_results,
    )
    if not injected:
        return (
            "user_prompt_empty_tool_injection",
            {
                "resolved_model": model,
                "tools_inject_enabled": True,
                "session_log": catalog_session_log,
            },
            "",
        )

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
    merged_log = list(catalog_session_log)
    if session_log:
        merged_log.extend(session_log)
    if merged_log:
        details["session_log"] = merged_log
    details["tools_inject_enabled"] = True
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
) -> tuple[list[dict[str, Any]], PruneResult, list[dict[str, Any]] | None, dict[str, PruneResult]]:
    stdout_guard = contextlib.nullcontext() if io_guarded else hook_safe_stdout()
    with stdout_guard:
        result, _, catalog, prune_results, _phase_timing = run_hook_coordinated_prune(
            query,
            config,
            skills_allowed=False,
            tools_allowed=True,
            io_guarded=io_guarded,
            pruner_settings=pruner_settings,
        )
        if catalog is None or result is None:
            missing = result or PruneResult(
                tools=None,
                status="skipped",
                query=query,
                tools_in=0,
                mcp_tools_in=0,
                tools_out=None,
                error="missing catalog",
            )
            return [], missing, catalog, prune_results
    return result.tools or [], result, catalog, prune_results
