"""``cyt executor`` commands for executor HTTP tool snapshots."""

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
    tools_hook_executor_url,
    tools_hook_mcp_definitions_file,
)
from cyt.proxy.setup_wizard import _prompt
from cyt.tools.hook_setup import build_pruning_tools_hook_save_overlay
from cyt.tools.sources.executor_http import fetch_executor_tools_for_cli

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


def add_executor_parser(subparsers: argparse._SubParsersAction) -> None:
    executor_parser = subparsers.add_parser("executor", help="Executor MCP aggregator utilities")
    executor_sub = executor_parser.add_subparsers(dest="executor_command", required=True)
    save_parser = executor_sub.add_parser(
        "save",
        help="Fetch tool definitions via executor HTTP API and write them to JSON",
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
    save_parser.set_defaults(executor_handler=run_executor_save)


def _resolve_executor_url(
    config_path: Path,
    config: dict[str, Any],
) -> str:
    executor_url = tools_hook_executor_url(config)
    if executor_url:
        return executor_url

    if not sys.stdin.isatty():
        raise SystemExit(
            "Executor URL not configured. "
            "Run interactively or set pruning.tools.hook.executor_url in config.yaml.",
        )

    print("\nExecutor URL not configured.")
    while True:
        url_text = _prompt("Executor base URL", "http://localhost:4789").strip().rstrip("/")
        if url_text:
            overlay = build_pruning_tools_hook_save_overlay(
                tools_from="executor",
                executor_url=url_text,
                mcp_definitions_file=str(tools_hook_mcp_definitions_file(config)),
            )
            if save_user_config(config_path, overlay, apply_bundled_sections=False):
                print(f"Saved executor URL to {config_path}")
            return url_text
        print("Executor URL is required.", file=sys.stderr)


def _write_definitions_file(path: Path, tools: list[dict[str, Any]]) -> None:
    resolved = path.expanduser()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = {"tools": tools}
    resolved.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def run_executor_save(args: argparse.Namespace) -> None:
    config_path = _resolve_user_config_path(getattr(args, "config", None))
    config = load_config(config_path)
    _resolve_executor_url(config_path, config)
    config = load_config(config_path)

    tools = fetch_executor_tools_for_cli(config, allow_prompt=True, blocking=True)
    if not tools:
        raise SystemExit("No tools fetched from executor.")

    output_path = (
        Path(args.file).expanduser()
        if getattr(args, "file", None) is not None
        else tools_hook_mcp_definitions_file(config)
    )
    _write_definitions_file(output_path, tools)
    print(f"Wrote {len(tools)} tools to {output_path}")

    if str(output_path) != str(tools_hook_mcp_definitions_file(config)):
        overlay = build_pruning_tools_hook_save_overlay(
            tools_from="executor",
            executor_url=tools_hook_executor_url(config),
            mcp_definitions_file=str(output_path),
        )
        if save_user_config(config_path, overlay, apply_bundled_sections=False):
            print(f"Updated definitions file in {config_path}")
