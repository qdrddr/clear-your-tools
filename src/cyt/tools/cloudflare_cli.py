"""``cyt cloudflare`` commands for Cloudflare portal tool snapshots."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cyt.cloudflare.catalog import fetch_cloudflare_tools_for_cli
from cyt.config import (
    load_config,
    resolve_config_path,
    save_user_config,
    tools_hook_cloudflare_url,
    tools_hook_executor_url,
    tools_hook_mcp_definitions_file,
)
from cyt.proxy.setup_wizard import _prompt
from cyt.tools.hook_setup import build_pruning_tools_hook_save_overlay

_CONFIG_SUFFIXES = {".yaml", ".yml"}


def _resolve_user_config_path(config_arg: Path | None) -> Path:
    if config_arg is None:
        return resolve_config_path(None)

    candidate = config_arg.expanduser()
    if candidate.suffix.lower() in _CONFIG_SUFFIXES:
        return candidate

    raise SystemExit(
        f"Config path must be a .yaml file: {candidate}. Use --config ~/.config/cyt/config.yaml.",
    )


def add_cloudflare_parser(subparsers: argparse._SubParsersAction) -> None:
    cloudflare_parser = subparsers.add_parser(
        "cloudflare",
        help="Cloudflare MCP portal catalog utilities",
    )
    cloudflare_sub = cloudflare_parser.add_subparsers(dest="cloudflare_command", required=True)
    save_parser = cloudflare_sub.add_parser(
        "save",
        help="Fetch tool definitions via Cloudflare MCP portal and write them to JSON",
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
    save_parser.add_argument(
        "--no-health-filter",
        action="store_true",
        help="Skip upstream server health filtering before writing the snapshot",
    )
    save_parser.set_defaults(cloudflare_handler=run_cloudflare_save)


def _resolve_cloudflare_url(
    config_path: Path,
    config: dict[str, Any],
) -> str:
    portal_url = tools_hook_cloudflare_url(config)
    if portal_url:
        return portal_url

    if not sys.stdin.isatty():
        raise SystemExit(
            "Cloudflare portal URL not configured. "
            "Run interactively or set pruning.tools.hook.cloudflare_url in config.yaml.",
        )

    print("\nCloudflare portal URL not configured.")
    while True:
        url_text = _prompt("Cloudflare portal URL", "https://mcp.example.com").strip().rstrip("/")
        if url_text:
            overlay = build_pruning_tools_hook_save_overlay(
                tools_from=["cloudflare"],
                executor_url=tools_hook_executor_url(config),
                mcp_definitions_file=str(tools_hook_mcp_definitions_file(config)),
            )
            overlay.setdefault("pruning", {}).setdefault("tools", {}).setdefault("hook", {})[
                "cloudflare_url"
            ] = url_text
            if save_user_config(config_path, overlay, apply_bundled_sections=False):
                print(f"Saved cloudflare URL to {config_path}")
            return url_text
        print("Cloudflare portal URL is required.", file=sys.stderr)


def _write_definitions_file(path: Path, tools: list[dict[str, Any]]) -> None:
    resolved = path.expanduser()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = {"tools": tools}
    resolved.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def run_cloudflare_save(args: argparse.Namespace) -> None:
    config_path = _resolve_user_config_path(getattr(args, "config", None))
    config = load_config(config_path)
    _resolve_cloudflare_url(config_path, config)
    config = load_config(config_path)

    tools = fetch_cloudflare_tools_for_cli(
        config,
        allow_prompt=True,
        blocking=True,
        apply_health_filter=not bool(getattr(args, "no_health_filter", False)),
    )
    if not tools:
        raise SystemExit("No tools fetched from Cloudflare portal.")

    output_path = (
        Path(args.file).expanduser()
        if getattr(args, "file", None) is not None
        else tools_hook_mcp_definitions_file(config)
    )
    _write_definitions_file(output_path, tools)
    print(f"Wrote {len(tools)} tools to {output_path}")

    if str(output_path) != str(tools_hook_mcp_definitions_file(config)):
        overlay = build_pruning_tools_hook_save_overlay(
            tools_from=["cloudflare"],
            executor_url=tools_hook_executor_url(config),
            mcp_definitions_file=str(output_path),
        )
        overlay.setdefault("pruning", {}).setdefault("tools", {}).setdefault("hook", {})[
            "cloudflare_url"
        ] = tools_hook_cloudflare_url(config)
        if save_user_config(config_path, overlay, apply_bundled_sections=False):
            print(f"Updated definitions file in {config_path}")
