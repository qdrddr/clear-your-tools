"""CLI to preview hook tool injection for a prompt (testing and troubleshooting)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from cyt.config import load_config, tools_hook_sources
from cyt.hook.workspace_config import resolve_hook_request_config, set_hook_workspace_in_config
from cyt.pruners.token_stats import build_preview_token_stats, format_preview_token_summary_lines
from cyt.pruners.tools_filter import filter_tools_for_query
from cyt.tools.hook import gate_and_format_hook_tools
from cyt.tools.inject import injection_token_count
from cyt.tools.master_catalog import get_master_tool_catalog
from cyt.tools.source_inject import (
    format_cloudflare_source_section,
    format_cyt_mcp_source_section,
    format_definitions_source_section,
    format_executor_source_section,
    format_mcp_source_section,
    format_multi_source_agent_tools,
)
from cyt_client.sessions import session_log_path
from cyt_core.types.prune import PruneResult
from cyt_mcp.catalog_export import catalog_token_stats, frontend_payload_from_hook_tools
from cyt_mcp.config import load_aggregator_config


def _add_preview_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "query",
        help="User prompt to rank/prune tools against (same as hook injection query)",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Workspace root (defaults to cwd)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON (tools, examples, injection text) instead of XML only",
    )
    parser.add_argument(
        "--definitions",
        action="store_true",
        help="Include full cyt-mcp tool definitions for each pruned cyt_mcp tool",
    )
    parser.add_argument(
        "--source",
        action="append",
        choices=("cyt_mcp", "mcpc", "cloudflare", "executor", "definitions"),
        help="Limit output to one or more catalog sources (repeatable)",
    )
    parser.add_argument(
        "--session",
        metavar="ID",
        default=None,
        help=(
            "Session id (reads .cursor/cyt/sessions/<ID>.jsonl) to apply "
            "pre-exposure gating like the hook"
        ),
    )


def add_inject_parser(subparsers: argparse._SubParsersAction) -> None:
    inject_parser = subparsers.add_parser(
        "inject",
        help="Preview hook tool injection for a user prompt",
    )
    inject_sub = inject_parser.add_subparsers(dest="inject_command", required=True)
    preview = inject_sub.add_parser(
        "preview",
        help="Run the hook pruning pipeline and print the agent-tools injection block",
    )
    _add_preview_arguments(preview)
    preview.set_defaults(inject_handler=run_inject_preview)


def _tools_for_sources(
    catalog: list[dict[str, Any]],
    sources: set[str] | None,
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {
        "cyt_mcp": [],
        "mcpc": [],
        "cloudflare": [],
        "executor": [],
        "definitions": [],
    }
    for tool in catalog:
        source = str(tool.get("cyt_catalog_source") or "").strip()
        if source not in grouped:
            continue
        if sources is not None and source not in sources:
            continue
        grouped[source].append(tool)
    return grouped


def _format_sections(
    pruned_by_source: dict[str, list[dict[str, Any]]],
    *,
    workspace_path: Path,
) -> dict[str, str]:
    workspace_paths = [str(workspace_path.resolve())]
    sections: dict[str, str] = {}
    if pruned_by_source.get("cyt_mcp"):
        sections["cyt_mcp"] = format_cyt_mcp_source_section(
            pruned_by_source["cyt_mcp"],
            workspace_paths=workspace_paths,
        )
    if pruned_by_source.get("mcpc"):
        sections["mcpc"] = format_mcp_source_section(
            pruned_by_source["mcpc"],
            workspace_paths=workspace_paths,
        )
    if pruned_by_source.get("cloudflare"):
        sections["cloudflare"] = format_cloudflare_source_section(
            pruned_by_source["cloudflare"],
            workspace_paths=workspace_paths,
        )
    if pruned_by_source.get("executor"):
        sections["executor"] = format_executor_source_section(
            pruned_by_source["executor"],
            workspace_paths=workspace_paths,
        )
    if pruned_by_source.get("definitions"):
        sections["definitions"] = format_definitions_source_section(
            pruned_by_source["definitions"],
            workspace_paths=workspace_paths,
        )
    return sections


def _full_definitions_for_tools(
    tools: list[dict[str, Any]],
    *,
    agent: str,
) -> dict[str, dict[str, Any]]:
    from cyt_mcp.aggregator import build_aggregator
    from cyt_mcp.config import load_aggregator_config
    from cyt_mcp.config_holder import ConfigHolder
    from cyt_mcp.runtime_cache import RuntimeToolCache
    from cyt_mcp.search import lookup_tool_definition
    from cyt_mcp.transport import refresh_runtime_cache

    config = load_aggregator_config(agent=agent)
    cache = RuntimeToolCache()
    config_holder = ConfigHolder(config)
    server, _middleware = build_aggregator(config_holder, cache)

    async def _refresh() -> None:
        await refresh_runtime_cache(server, cache, config)

    asyncio.run(_refresh())

    out: dict[str, dict[str, Any]] = {}
    for tool in tools:
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        try:
            out[name] = lookup_tool_definition(cache, name)
        except ValueError:
            continue
    return out


def _preview_token_stats(
    *,
    grouped: dict[str, list[dict[str, Any]]],
    pruned_injection: str,
    agent: str,
    workspace: Path,
) -> dict[str, int | float]:
    workspace_paths = [str(workspace)]
    full_by_source = {source: tools for source, tools in grouped.items() if tools}
    full_sections = _format_sections(full_by_source, workspace_path=workspace)
    full_injection = format_multi_source_agent_tools(
        full_sections,
        workspace_paths=workspace_paths,
    )
    tokens_in = injection_token_count(full_injection) if full_injection.strip() else 0
    tokens_out = injection_token_count(pruned_injection) if pruned_injection.strip() else 0
    if tokens_in == 0 and tokens_out == 0:
        return {}

    tool_count_in = sum(len(tools) for tools in full_by_source.values())

    frontend_tool_count: int | None = None
    frontend_tokens: int | None = None
    cyt_mcp_tools = grouped.get("cyt_mcp") or []
    if cyt_mcp_tools:
        agg_config = load_aggregator_config(
            agent=agent,
            workspace_folder=workspace,
        )
        frontend_payload = frontend_payload_from_hook_tools(cyt_mcp_tools, config=agg_config)
        frontend_stats = catalog_token_stats(frontend_payload["tools"])
        frontend_tool_count = frontend_stats["tool_count"]
        frontend_tokens = frontend_stats["tokens_compact_json"]

    return build_preview_token_stats(
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tool_count_in=tool_count_in,
        frontend_tool_count=frontend_tool_count,
        frontend_tokens=frontend_tokens,
    )


def _print_preview_token_summary(token_stats: dict[str, int | float]) -> None:
    if not token_stats:
        return
    for line in format_preview_token_summary_lines(token_stats):
        print(line, file=sys.stderr, flush=True)


def _preview_hook_payload(
    *,
    workspace: Path,
    query: str,
    session_id: str,
    agent: str,
) -> dict[str, Any]:
    return {
        "cwd": str(workspace),
        "workspace_roots": [str(workspace)],
        "prompt": query,
        "session_id": session_id,
        "cyt_agent": agent,
    }


def resolve_preview_session_log(
    session_id: str,
    *,
    workspace: Path,
    agent: str,
) -> Path:
    """Resolve session JSONL path (workspace first, then agent home)."""
    payload = _preview_hook_payload(
        workspace=workspace,
        query="",
        session_id=session_id,
        agent=agent,
    )
    path = session_log_path(payload)
    if path is None or not path.is_file():
        hint = str(path) if path is not None else "(unresolved)"
        raise FileNotFoundError(f"Session log not found: {hint}")
    return path


def _prune_result_from_filter(
    result: Any,
    *,
    query: str,
    tools: list[dict[str, Any]],
) -> PruneResult:
    return PruneResult(
        tools=result.tools,
        status=result.status,
        query=query,
        tools_in=result.tools_in,
        mcp_tools_in=result.tools_in,
        tools_out=result.tools_out,
        error=result.error,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        tokens_saved=result.tokens_saved,
        tools_final=result.tools,
    )


def _session_gate_summary(
    pruned_by_source: dict[str, list[dict[str, Any]]],
    session_logs: list[dict[str, Any]],
) -> dict[str, Any]:
    from cyt.injection.session_log_build import tool_item_key

    injected_keys = {
        str(entry.get("key") or "")
        for entry in session_logs
        if entry.get("kind") == "tool" and str(entry.get("key") or "").strip()
    }
    injected_by_source: dict[str, list[str]] = {}
    skipped_by_source: dict[str, list[str]] = {}
    for source, tools in pruned_by_source.items():
        for tool in tools:
            catalog_source = str(tool.get("cyt_catalog_source") or source).strip()
            if catalog_source == "cyt_mcp":
                display_name = str(tool.get("name") or "").strip()
            else:
                display_name = str(tool.get("name") or tool.get("tool_name") or "").strip()
            if not display_name:
                continue
            key = tool_item_key(tool)
            if key in injected_keys:
                injected_by_source.setdefault(source, []).append(display_name)
            else:
                skipped_by_source.setdefault(source, []).append(display_name)

    return {
        "log_entry_count": len(session_logs),
        "injected_tools": injected_by_source,
        "skipped_tools": skipped_by_source,
    }


def _gated_injection_for_preview(
    pruned_by_source: dict[str, list[dict[str, Any]]],
    prune_results: dict[str, PruneResult],
    *,
    config: dict[str, Any],
    catalog: list[dict[str, Any]],
    payload: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    pruned_tools = [tool for tools in pruned_by_source.values() for tool in tools]
    return gate_and_format_hook_tools(
        pruned_tools,
        config=config,
        payload=payload,
        session_text="",
        catalog_tools=catalog,
        prune_results=prune_results if len(prune_results) > 1 else None,
    )


def config_for_inject_preview(workspace: Path) -> dict[str, Any]:
    """Resolve hook config the same way the daemon does for a workspace root."""
    payload = {"cwd": str(workspace)}
    config, resolved_workspace = resolve_hook_request_config(
        payload,
        "cursor",
        base_config=load_config(),
    )
    return set_hook_workspace_in_config(config, resolved_workspace or workspace)


def run_inject_preview(args: argparse.Namespace) -> int:
    workspace = (args.workspace or Path.cwd()).resolve()
    config = config_for_inject_preview(workspace)
    catalog = get_master_tool_catalog(config, blocking=True) or []
    if not catalog:
        from cyt.cyt_mcp.catalog import cyt_mcp_catalog_slug
        from cyt.cyt_mcp.catalog_disk import read_disk_catalog

        slug = cyt_mcp_catalog_slug(config)
        print(
            "No tools in master hook catalog "
            f"(workspace={workspace}, disk_cache={'hit' if read_disk_catalog(slug) else 'miss'}).",
            file=sys.stderr,
        )
        return 1

    sources_filter = set(args.source) if args.source else None
    grouped = _tools_for_sources(catalog, sources_filter)

    pruned_by_source: dict[str, list[dict[str, Any]]] = {}
    prune_meta: dict[str, Any] = {}
    prune_results: dict[str, PruneResult] = {}
    agent = str(config.get("agent") or "cursor")
    for source, tools in grouped.items():
        if not tools:
            continue
        result = filter_tools_for_query(
            tools,
            args.query,
            config=config,
            for_hook=True,
            log_token_counts=False,
        )
        prune_meta[source] = {
            "status": result.status,
            "tools_in": result.tools_in,
            "tools_out": result.tools_out,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "tokens_saved": result.tokens_saved,
            "error": result.error,
        }
        prune_results[source] = _prune_result_from_filter(
            result,
            query=args.query,
            tools=tools,
        )
        if result.tools:
            pruned_by_source[source] = result.tools

    session_raw = getattr(args, "session", None)
    session_id = str(session_raw).strip() if session_raw else ""
    session_log_path_value: Path | None = None
    session_gate: dict[str, Any] | None = None
    session_logs: list[dict[str, Any]] = []

    if session_id:
        try:
            session_log_path_value = resolve_preview_session_log(
                session_id,
                workspace=workspace,
                agent=agent,
            )
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        hook_payload = _preview_hook_payload(
            workspace=workspace,
            query=args.query,
            session_id=session_id,
            agent=agent,
        )
        injection, session_logs = _gated_injection_for_preview(
            pruned_by_source,
            prune_results,
            config=config,
            catalog=catalog,
            payload=hook_payload,
        )
        session_gate = _session_gate_summary(pruned_by_source, session_logs)
    else:
        sections = _format_sections(pruned_by_source, workspace_path=workspace)
        injection = format_multi_source_agent_tools(sections, workspace_paths=[str(workspace)])

    token_stats = _preview_token_stats(
        grouped=grouped,
        pruned_injection=injection,
        agent=agent,
        workspace=workspace,
    )

    if args.json:
        payload: dict[str, Any] = {
            "query": args.query,
            "workspace": str(workspace),
            "sources": sorted(sources_filter)
            if sources_filter
            else sorted(tools_hook_sources(config)),
            "prune": prune_meta,
            "tools": pruned_by_source,
            "injection": injection,
        }
        if session_id:
            payload["session_id"] = session_id
            if session_log_path_value is not None:
                payload["session_log_path"] = str(session_log_path_value)
            if session_gate is not None:
                payload["session_gate"] = session_gate
        if token_stats:
            payload["token_stats"] = token_stats
        if args.definitions and pruned_by_source.get("cyt_mcp"):
            payload["full_definitions"] = _full_definitions_for_tools(
                pruned_by_source["cyt_mcp"],
                agent=agent,
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        _print_preview_token_summary(token_stats)
        return 0

    if injection:
        print(injection)
    else:
        print("(no tools matched the query)", file=sys.stderr)
    if args.definitions and pruned_by_source.get("cyt_mcp"):
        defs = _full_definitions_for_tools(
            pruned_by_source["cyt_mcp"],
            agent=agent,
        )
        print("\n--- full definitions ---\n")
        print(json.dumps(defs, ensure_ascii=False, indent=2))
    _print_preview_token_summary(token_stats)
    return 0 if injection else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cyt inject")
    sub = parser.add_subparsers(dest="inject_command", required=True)
    preview = sub.add_parser(
        "preview",
        help="Run the hook pruning pipeline and print the agent-tools injection block",
    )
    _add_preview_arguments(preview)
    args = parser.parse_args(argv)
    return run_inject_preview(args)
