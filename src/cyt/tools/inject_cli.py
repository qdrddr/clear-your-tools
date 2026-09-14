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
from cyt.pruners.tools_filter import filter_tools_for_query
from cyt.tools.master_catalog import get_master_tool_catalog
from cyt.tools.source_inject import (
    format_cloudflare_source_section,
    format_cyt_mcp_source_section,
    format_definitions_source_section,
    format_executor_source_section,
    format_mcp_source_section,
    format_multi_source_agent_tools,
)


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
    for source, tools in grouped.items():
        if not tools:
            continue
        result = filter_tools_for_query(
            tools,
            args.query,
            config=config,
            for_hook=True,
        )
        prune_meta[source] = {
            "status": result.status,
            "tools_in": result.tools_in,
            "tools_out": result.tools_out,
            "error": result.error,
        }
        if result.tools:
            pruned_by_source[source] = result.tools

    sections = _format_sections(pruned_by_source, workspace_path=workspace)
    injection = format_multi_source_agent_tools(sections, workspace_paths=[str(workspace)])

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
        if args.definitions and pruned_by_source.get("cyt_mcp"):
            payload["full_definitions"] = _full_definitions_for_tools(
                pruned_by_source["cyt_mcp"],
                agent=str(config.get("agent") or "cursor"),
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if injection:
        print(injection)
    else:
        print("(no tools matched the query)", file=sys.stderr)
    if args.definitions and pruned_by_source.get("cyt_mcp"):
        defs = _full_definitions_for_tools(
            pruned_by_source["cyt_mcp"],
            agent=str(config.get("agent") or "cursor"),
        )
        print("\n--- full definitions ---\n")
        print(json.dumps(defs, ensure_ascii=False, indent=2))
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
