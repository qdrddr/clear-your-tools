"""``cyt-mcp`` CLI entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, cast

from cyt_mcp.aggregator import build_aggregator
from cyt_mcp.catalog import catalog_json
from cyt_mcp.config import AggregatorConfig, load_aggregator_config
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import lookup_tool_definition
from cyt_mcp.transport import refresh_runtime_cache

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cyt-mcp")
    parser.add_argument("--agent", help="Agent harness (cursor, claude, codex)")
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        help="Frontend MCP transport (default from mcp-aggregator.yaml)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to mcp-aggregator.yaml",
    )
    sub = parser.add_subparsers(dest="command")

    catalog = sub.add_parser("catalog", help="Export full tool catalog JSON")
    catalog.add_argument("--agent", help="Agent harness")
    catalog.add_argument("--json", action="store_true", help="Print JSON to stdout")
    catalog.add_argument("--config", type=Path, default=None)

    search = sub.add_parser("search", help="Look up a full backend tool definition")
    search.add_argument("tool_name", help="Backend cyt-mcp tool name")
    search.add_argument("--agent", help="Agent harness")
    search.add_argument("--json", action="store_true", help="Print JSON to stdout")
    search.add_argument("--config", type=Path, default=None)
    return parser


async def _run_search(config: AggregatorConfig, tool_name: str) -> int:
    cache = RuntimeToolCache()
    server = build_aggregator(config, cache)
    await refresh_runtime_cache(server, cache, config)
    try:
        definition = lookup_tool_definition(cache, tool_name)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(definition, ensure_ascii=False, indent=2))
    return 0


async def _run_catalog(config: AggregatorConfig) -> int:
    cache = RuntimeToolCache()
    server = build_aggregator(config, cache)
    await refresh_runtime_cache(server, cache, config)
    print(catalog_json(cache, agent=config.agent))
    return 0


async def _run_server(config: AggregatorConfig) -> int:
    from cyt_client.pairing import repair_pairing_from_mcp_runtime
    from cyt_client.skip import hook_skip_enabled

    startup_payload = {
        "hook_event_name": "sessionStart",
        "session_id": "cyt-mcp-startup",
        "cyt_agent": config.agent,
        "cwd": str(Path.cwd()),
    }
    if not hook_skip_enabled(startup_payload):
        repair_pairing_from_mcp_runtime(agent=config.agent, verbose=False)
    cache = RuntimeToolCache()
    server = build_aggregator(config, cache)
    await refresh_runtime_cache(server, cache, config)
    if config.transport == "http":
        from cyt_mcp.transport import run_http

        await run_http(server, cache, config)
    else:
        await cast(Any, server).run_async("stdio", show_banner=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "catalog":
            config = load_aggregator_config(agent=args.agent, aggregator_path=args.config)
            return asyncio.run(_run_catalog(config))

        if args.command == "search":
            if not args.json:
                print("cyt-mcp search requires --json", file=sys.stderr)
                return 1
            config = load_aggregator_config(agent=args.agent, aggregator_path=args.config)
            return asyncio.run(_run_search(config, args.tool_name))

        config = load_aggregator_config(agent=args.agent, aggregator_path=args.config)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.transport:
        config = AggregatorConfig(
            agent=config.agent,
            mcp_servers=config.mcp_servers,
            transport=args.transport,
            http=config.http,
            codex_stubs_include_description=config.codex_stubs_include_description,
            verify_only=config.verify_only,
            aggregator_path=config.aggregator_path,
            agent_mcp_path=config.agent_mcp_path,
        )
    try:
        return asyncio.run(_run_server(config))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logger.error("cyt-mcp failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
