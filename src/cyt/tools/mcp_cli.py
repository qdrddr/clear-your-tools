"""``cyt mcp`` commands for MCP client configuration and tool snapshots."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cyt.config import (
    load_config,
    resolve_config_path,
    save_user_config,
    tools_hook_mcp_client_file,
    tools_hook_mcp_definitions_file,
)
from cyt.proxy.setup_wizard import _prompt
from cyt.tools.hook_setup import build_pruning_tools_hook_save_overlay
from cyt.tools.sources.mcp_client import McpServerFetchResult, fetch_mcp_client_tools

_CONFIG_SUFFIXES = {".yaml", ".yml"}


def _mcp_client_file_is_usable(path: Path) -> bool:
    if not path.is_file():
        return False
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False
    servers = data.get("mcpServers")
    return isinstance(servers, dict) and bool(servers)


def _resolve_user_config_path(config_arg: Path | None) -> Path:
    if config_arg is None:
        return resolve_config_path(None)

    candidate = config_arg.expanduser()
    if candidate.suffix.lower() in _CONFIG_SUFFIXES:
        return candidate

    if _mcp_client_file_is_usable(candidate):
        raise SystemExit(
            f"{candidate} looks like an MCP client JSON file, not config.yaml. "
            "Use --config ~/.config/cyt/config.yaml and set "
            "pruning.tools.hook.mcp_client_file instead.",
        )

    raise SystemExit(
        f"Config path must be a .yaml file: {candidate}. Use --config ~/.config/cyt/config.yaml.",
    )


def add_mcp_parser(subparsers: argparse._SubParsersAction) -> None:
    mcp_parser = subparsers.add_parser("mcp", help="MCP client utilities")
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_command", required=True)
    save_parser = mcp_sub.add_parser(
        "save",
        help="Fetch MCP tool definitions via FastMCP and write them to JSON",
    )
    save_parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help=(
            "Output definitions file (default: pruning.tools.hook.mcp_definitions_file from config)"
        ),
    )
    save_parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml (default: ./config.yaml, then ~/.config/cyt/config.yaml)",
    )
    save_parser.set_defaults(mcp_handler=run_mcp_save)


def _resolve_mcp_client_file(
    config_path: Path,
    config: dict[str, Any],
) -> Path:
    client_path = tools_hook_mcp_client_file(config)
    if _mcp_client_file_is_usable(client_path):
        return client_path

    if not sys.stdin.isatty():
        raise SystemExit(
            f"MCP client config not found or empty: {client_path}. "
            "Run interactively or set pruning.tools.hook.mcp_client_file in config.yaml.",
        )

    print(f"\nMCP client config not found: {client_path}")
    while True:
        path_text = _prompt("MCP client config file (mcp.json)", str(client_path))
        candidate = Path(path_text).expanduser()
        if _mcp_client_file_is_usable(candidate):
            client_path = candidate
            break
        print(f"{candidate} is missing or has no mcpServers.", file=sys.stderr)

    overlay = build_pruning_tools_hook_save_overlay(
        tools_from="client",
        mcp_client_file=str(client_path),
        mcp_definitions_file=str(tools_hook_mcp_definitions_file(config)),
    )
    if save_user_config(config_path, overlay, apply_bundled_sections=False):
        print(f"Saved MCP client file to {config_path}")
    return client_path


def _write_definitions_file(path: Path, tools: list[dict[str, Any]]) -> None:
    resolved = path.expanduser()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = {"tools": tools}
    resolved.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _print_server_results(results: list[McpServerFetchResult]) -> None:
    if not results:
        print("No MCP servers found in client config.", file=sys.stderr)
        return

    ok = sum(1 for result in results if result.status == "ok")
    failed = sum(1 for result in results if result.status == "failed")
    skipped = sum(1 for result in results if result.status == "skipped")
    print(
        f"MCP servers: {len(results)} total, {ok} ok, {failed} failed, {skipped} skipped",
        file=sys.stderr,
    )
    for result in results:
        if result.status == "ok":
            print(f"  {result.server_name}: {len(result.tools)} tools", file=sys.stderr)
            continue
        detail = result.error or result.status
        print(f"  {result.server_name}: {result.status} ({detail})", file=sys.stderr)


def run_mcp_save(args: argparse.Namespace) -> None:
    config_path = _resolve_user_config_path(getattr(args, "config", None))
    config = load_config(config_path)
    client_path = _resolve_mcp_client_file(config_path, config)

    try:
        tools, results = fetch_mcp_client_tools(client_path, claude_fallback=False)
    except ImportError as exc:
        raise SystemExit(str(exc)) from exc

    _print_server_results(results)

    output_path = (
        Path(args.file).expanduser()
        if getattr(args, "file", None) is not None
        else tools_hook_mcp_definitions_file(config)
    )
    _write_definitions_file(output_path, tools)
    print(f"Wrote {len(tools)} tools to {output_path}")

    if str(output_path) != str(tools_hook_mcp_definitions_file(config)):
        overlay = build_pruning_tools_hook_save_overlay(
            tools_from="client",
            mcp_client_file=str(client_path),
            mcp_definitions_file=str(output_path),
        )
        if save_user_config(config_path, overlay, apply_bundled_sections=False):
            print(f"Updated definitions file in {config_path}")
