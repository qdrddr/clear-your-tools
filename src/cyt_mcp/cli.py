"""``cyt-mcp`` CLI entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, cast

from fastmcp import FastMCP

from cyt.pruners.token_stats import format_catalog_token_line
from cyt_mcp.aggregator import build_aggregator
from cyt_mcp.catalog_export import (
    backend_full_catalog,
    backend_hook_catalog,
    catalog_token_stats,
    frontend_stubs,
    tool_dicts_from_backend_payload,
    tool_dicts_from_frontend_payload,
)
from cyt_mcp.config import AggregatorConfig, load_aggregator_config
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import lookup_tool_definition
from cyt_mcp.tool_list_notify import ToolListChangedMiddleware
from cyt_mcp.transport import refresh_runtime_cache

logger = logging.getLogger(__name__)

CatalogSource = str
BackgroundCatalogRefreshFn = Callable[[], Coroutine[Any, Any, None]]


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
    parser.add_argument(
        "--workspace",
        type=str,
        default=None,
        metavar="PATH",
        help=(
            "Full absolute consumer workspace path, or ${workspaceFolder} from agent MCP config. "
            "Defaults to CYT_WORKSPACE / CYT_SHELL_WORKSPACE when unset."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    permissions = sub.add_parser(
        "permissions",
        help="Manage MCP permissions (alias for cyt permissions)",
    )
    from cyt.permissions.cli import _configure_permissions_parser

    _configure_permissions_parser(permissions)

    catalog = sub.add_parser("catalog", help="Export full tool catalog JSON")
    catalog.add_argument("--agent", help="Agent harness")
    catalog.add_argument("--json", action="store_true", help="Print JSON to stdout")
    catalog.add_argument("--config", type=Path, default=None)
    catalog.add_argument(
        "--source",
        choices=("backend", "frontend", "both"),
        default="backend",
        help="Export backend hook catalog, frontend stubs, or both (default: backend)",
    )
    catalog.add_argument(
        "--full",
        action="store_true",
        help="Backend export uses full search_index definitions (outputSchema, meta, etc.)",
    )
    catalog.add_argument(
        "--tokens",
        action="store_true",
        help="Print compact-JSON token summary to stderr",
    )

    search = sub.add_parser("search", help="Look up a full backend tool definition")
    search.add_argument("tool_name", help="Backend cyt-mcp tool name")
    search.add_argument("--agent", help="Agent harness")
    search.add_argument("--json", action="store_true", help="Print JSON to stdout")
    search.add_argument("--config", type=Path, default=None)
    return parser


async def _run_search(config: AggregatorConfig, tool_name: str) -> int:
    cache = RuntimeToolCache()
    config_holder = ConfigHolder(config)
    server, _middleware, _coordinator = build_aggregator(config_holder, cache)
    await refresh_runtime_cache(server, cache, config)
    try:
        definition = lookup_tool_definition(cache, tool_name)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(definition, ensure_ascii=False, indent=2))
    return 0


def _print_catalog_token_lines(
    *,
    source: CatalogSource,
    backend_payload: dict[str, Any] | None,
    frontend_payload: dict[str, Any] | None,
) -> None:
    lines: list[str] = []
    if source in {"backend", "both"} and backend_payload is not None:
        backend_stats = catalog_token_stats(tool_dicts_from_backend_payload(backend_payload))
        lines.append(
            format_catalog_token_line(
                "backend",
                backend_stats["tool_count"],
                backend_stats["tokens_compact_json"],
            ),
        )
    if source in {"frontend", "both"} and frontend_payload is not None:
        frontend_stats = catalog_token_stats(tool_dicts_from_frontend_payload(frontend_payload))
        lines.append(
            format_catalog_token_line(
                "frontend",
                frontend_stats["tool_count"],
                frontend_stats["tokens_compact_json"],
            ),
        )
    if not lines:
        return
    print("", file=sys.stderr, flush=True)
    for line in lines:
        print(line, file=sys.stderr, flush=True)
    print("", file=sys.stderr, flush=True)


def _catalog_export_payload(
    *,
    source: CatalogSource,
    backend_full: bool,
    cache: RuntimeToolCache,
    config: AggregatorConfig,
    frontend_payload: dict[str, Any],
) -> dict[str, Any]:
    backend_payload = (
        backend_full_catalog(cache, config) if backend_full else backend_hook_catalog(cache, config)
    )
    if source == "backend":
        return backend_payload
    if source == "frontend":
        return frontend_payload
    return {
        "agent": config.agent,
        "backend": backend_payload,
        "frontend": frontend_payload,
        "degraded_servers": cache.degraded(),
    }


async def _run_catalog(config: AggregatorConfig, args: argparse.Namespace) -> int:
    cache = RuntimeToolCache()
    config_holder = ConfigHolder(config)
    server, _middleware, _coordinator = build_aggregator(config_holder, cache)
    await refresh_runtime_cache(server, cache, config)

    source = str(getattr(args, "source", "backend") or "backend")
    backend_full = bool(getattr(args, "full", False))
    show_tokens = bool(getattr(args, "tokens", False)) or source == "both"

    backend_payload = (
        backend_full_catalog(cache, config) if backend_full else backend_hook_catalog(cache, config)
    )
    frontend_payload = await frontend_stubs(server, cache, config)
    payload = _catalog_export_payload(
        source=source,
        backend_full=backend_full,
        cache=cache,
        config=config,
        frontend_payload=frontend_payload,
    )

    if show_tokens:
        if source == "both":
            _print_catalog_token_lines(
                source=source,
                backend_payload=backend_payload,
                frontend_payload=frontend_payload,
            )
        elif source == "backend":
            _print_catalog_token_lines(
                source=source,
                backend_payload=payload,
                frontend_payload=None,
            )
        else:
            _print_catalog_token_lines(
                source=source,
                backend_payload=None,
                frontend_payload=payload,
            )

    use_json = bool(getattr(args, "json", False)) or not sys.stdout.isatty()
    indent = 2 if use_json or sys.stdout.isatty() else None
    print(json.dumps(payload, ensure_ascii=False, indent=indent))
    return 0


def _start_pairing_repair_thread(config: AggregatorConfig) -> None:
    import threading

    from cyt_client.pairing import repair_pairing_from_mcp_runtime
    from cyt_client.skip import hook_skip_enabled

    startup_payload = {
        "hook_event_name": "sessionStart",
        "session_id": "cyt-mcp-startup",
        "cyt_agent": config.agent,
        "cwd": str(Path.cwd()),
    }
    if hook_skip_enabled(startup_payload):
        return
    threading.Thread(
        target=repair_pairing_from_mcp_runtime,
        kwargs={
            "agent": config.agent,
            "catalog_scope": config.catalog_scope,
            "verbose": False,
        },
        name="cyt-mcp-pairing-repair",
        daemon=True,
    ).start()


def _hydrate_server_runtime_caches(
    cache: RuntimeToolCache,
    config: AggregatorConfig,
) -> None:
    from cyt_mcp.catalog_build import hydrate_runtime_cache

    hydrate_runtime_cache(cache, config)


def _make_background_catalog_refresh(
    *,
    cache: RuntimeToolCache,
    config: AggregatorConfig,
    server: FastMCP[Any],
    list_changed_middleware: ToolListChangedMiddleware | None,
) -> BackgroundCatalogRefreshFn:
    from cyt_mcp.catalog_build import refresh_catalog_cache

    async def _background_catalog_refresh() -> None:
        try:
            before_count = len(cache.snapshot())
            await refresh_catalog_cache(server, cache, config)
            after_count = len(cache.snapshot())
            if (
                list_changed_middleware is not None
                and after_count > 0
                and after_count >= before_count
            ):
                from cyt_mcp.tool_list_notify import notify_all_sessions_list_changed

                await notify_all_sessions_list_changed(list_changed_middleware)
        except Exception as exc:
            logger.warning("cyt-mcp background catalog refresh failed: %s", exc)

    return _background_catalog_refresh


def _schedule_initial_cache_refresh(
    *,
    cache: RuntimeToolCache,
    background_catalog_refresh: BackgroundCatalogRefreshFn,
) -> asyncio.Task[None] | None:
    if bool(cache.snapshot()):
        return None
    return asyncio.create_task(
        background_catalog_refresh(),
        name="cyt-mcp-catalog-refresh",
    )


async def _run_server_transport(
    server: FastMCP[Any],
    cache: RuntimeToolCache,
    config: AggregatorConfig,
) -> None:
    if config.transport == "http":
        from cyt_mcp.transport import run_http

        await run_http(server, cache, config)
        return
    await cast(Any, server).run_async("stdio", show_banner=False)


async def _run_server(config: AggregatorConfig, *, aggregator_path: Path | None = None) -> int:
    from cyt_mcp.hook_daemon_push import PushContext, deregister_catalog_push, register_push_context

    _start_pairing_repair_thread(config)
    cache = RuntimeToolCache()
    config_holder = ConfigHolder(config)
    server, list_changed_middleware, _coordinator = build_aggregator(
        config_holder,
        cache,
        aggregator_path=aggregator_path,
    )
    register_push_context(
        PushContext(
            config_holder=config_holder,
            cache=cache,
            server=server,
            list_changed_middleware=list_changed_middleware,
        ),
    )
    if config.workspace_root is not None:
        from cyt.hook.active_workspace import touch_active_workspace

        touch_active_workspace(config.agent, config.workspace_root)
    _hydrate_server_runtime_caches(cache, config)
    background_catalog_refresh = _make_background_catalog_refresh(
        cache=cache,
        config=config,
        server=server,
        list_changed_middleware=list_changed_middleware,
    )
    refresh_task = _schedule_initial_cache_refresh(
        cache=cache,
        background_catalog_refresh=background_catalog_refresh,
    )
    try:
        await _run_server_transport(server, cache, config)
    finally:
        if refresh_task is not None:
            refresh_task.cancel()
            try:
                await refresh_task
            except asyncio.CancelledError:
                pass
        deregister_catalog_push(config)
    return 0


def _resolve_cyt_mcp_workspace_folder(raw: str | None) -> Path | None:
    from cyt.hook.workspace_resolution import (
        WorkspacePathNotAbsoluteError,
        _is_template_workspace_value,
        require_absolute_workspace_dir,
        resolve_consumer_project_root,
    )

    if raw is not None and str(raw).strip():
        text = str(raw).strip()
        if _is_template_workspace_value(text):
            return resolve_consumer_project_root()
        try:
            return require_absolute_workspace_dir(text, label="--workspace")
        except WorkspacePathNotAbsoluteError:
            return resolve_consumer_project_root()
    return resolve_consumer_project_root()


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    workspace_folder = _resolve_cyt_mcp_workspace_folder(getattr(args, "workspace", None))

    try:
        if args.command == "permissions":
            from cyt.permissions.cli import run_permissions

            run_permissions(args)
            return 0

        if args.command == "catalog":
            config = load_aggregator_config(
                agent=args.agent,
                aggregator_path=args.config,
                workspace_folder=workspace_folder,
            )
            return asyncio.run(_run_catalog(config, args))

        if args.command == "search":
            if not args.json:
                print("cyt-mcp search requires --json", file=sys.stderr)
                return 1
            config = load_aggregator_config(
                agent=args.agent,
                aggregator_path=args.config,
                workspace_folder=workspace_folder,
            )
            return asyncio.run(_run_search(config, args.tool_name))

        config = load_aggregator_config(
            agent=args.agent,
            aggregator_path=args.config,
            workspace_folder=workspace_folder,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.transport:
        config = AggregatorConfig(
            agent=config.agent,
            mcp_servers=config.mcp_servers,
            transport=args.transport,
            http=config.http,
            stub_name=config.stub_name,
            stub_retain=config.stub_retain,
            codex_stubs_include_description=config.codex_stubs_include_description,
            verify_only=config.verify_only,
            aggregator_path=config.aggregator_path,
            agent_mcp_path=config.agent_mcp_path,
            catalog_scope=config.catalog_scope,
            workspace_root=config.workspace_root,
            mcp_deny=config.mcp_deny,
            server_origins=config.server_origins,
        )
    try:
        return asyncio.run(_run_server(config, aggregator_path=args.config))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logger.error("cyt-mcp failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
