"""``cyt launch`` command orchestration."""

from __future__ import annotations

import argparse
from pathlib import Path

from cyt.config import DEFAULT_REVERSE_PORT, load_config
from cyt.launch.claude import build_claude_env
from cyt.launch.claude import run as run_claude
from cyt.launch.codex import configure_provider, restore_provider
from cyt.launch.codex import run as run_codex
from cyt.launch.config import codex_env_key_name
from cyt.launch.endpoints import resolve_agent_endpoint
from cyt.launch.env_report import print_runtime_env_report
from cyt.launch.proxy_guard import ensure_proxy
from cyt.launch.secrets import ensure_runtime_credentials
from cyt.launch.upstream import AgentName, parse_agent_name, resolve_upstream_kind
from cyt.proxy.bootstrap import prepare_runtime
from cyt.proxy.setup import normalize_upstream_kind


def parse_launch_remainder(remainder: list[str]) -> tuple[AgentName, list[str]]:
    """Parse ``-- claude|codex [args...]``."""
    if not remainder or remainder[0] != "--":
        raise SystemExit(
            "cyt launch requires `--` followed by agent name (claude or codex).",
        )
    if len(remainder) < 2:
        raise SystemExit("Missing agent name after `--`; expected claude or codex.")
    try:
        agent = parse_agent_name(remainder[1])
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    return agent, remainder[2:]


def add_shared_upstream_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Reverse listen port (default from config, else {DEFAULT_REVERSE_PORT})",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml (default: ./config.yaml, then ~/.config/cyt/config.yaml)",
    )
    parser.add_argument(
        "--upstream",
        metavar="URL",
        default=None,
        help="Upstream API base URL (writes minimal upstream config to config.yaml)",
    )
    parser.add_argument(
        "--upstream-kind",
        type=normalize_upstream_kind,
        default=None,
        help=(
            "Upstream protocol kind (optional for canonical URLs): "
            "anthropic, openai, or aliases claude/claude-code, codex"
        ),
    )
    parser.add_argument(
        "--upstream-name",
        default=None,
        help="Upstream endpoint name (default: derived from URL second-level domain)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress runtime env summary before agent start",
    )


def add_launch_parser(subparsers: argparse._SubParsersAction) -> None:
    launch_parser = subparsers.add_parser(
        "launch",
        help="Launch Claude Code or Codex through the CYT proxy",
    )
    add_shared_upstream_args(launch_parser)
    launch_parser.add_argument(
        "--endpoint",
        default=None,
        help="Reverse proxy endpoint name for this launch",
    )
    launch_parser.add_argument(
        "--configure",
        action="store_true",
        help="Write Codex provider config only (codex agent)",
    )
    launch_parser.add_argument(
        "--restore",
        action="store_true",
        help="Remove launch-managed Codex provider config (codex agent)",
    )
    launch_parser.add_argument(
        "remainder",
        nargs=argparse.REMAINDER,
        help="Use `-- claude|codex [agent args...]`",
    )


def _validate_upstream_kind_args(args: argparse.Namespace) -> None:
    upstream_url = getattr(args, "upstream", None)
    upstream_kind = getattr(args, "upstream_kind", None)
    upstream_name = getattr(args, "upstream_name", None)
    if upstream_kind is not None and upstream_url is None:
        raise SystemExit("--upstream-kind requires --upstream")
    if upstream_name is not None and upstream_url is None:
        raise SystemExit("--upstream-name requires --upstream")


def run(args: argparse.Namespace) -> None:
    """Run ``cyt launch`` after argparse."""
    _validate_upstream_kind_args(args)
    agent, agent_args = parse_launch_remainder(getattr(args, "remainder", []))

    if args.configure and args.restore:
        raise SystemExit("Pass either --configure or --restore, not both.")

    if args.configure or args.restore:
        if agent != "codex":
            raise SystemExit("--configure and --restore are only supported for codex.")

    if args.restore:
        config = load_config(args.config)
        restore_provider(env_key=codex_env_key_name(config))
        return

    upstream_kind = resolve_upstream_kind(
        args.upstream,
        agent=agent,
        explicit=args.upstream_kind,
    )
    if args.upstream is not None and upstream_kind is None:
        raise SystemExit(
            "Cannot infer upstream kind from URL. "
            "Pass --upstream-kind or use a canonical URL "
            "(https://api.openai.com or https://api.anthropic.com).",
        )

    runtime = prepare_runtime(
        agent=agent,
        config_path=args.config,
        port=args.port,
        upstream_url=args.upstream,
        upstream_kind=upstream_kind,
        upstream_name=args.upstream_name,
        resolve_credentials=False,
    )

    endpoint = resolve_agent_endpoint(
        runtime.config,
        agent=agent,
        config_path=runtime.config_path,
        endpoint_override=args.endpoint,
        upstream_cli_endpoint=runtime.upstream_endpoint,
    )

    if args.configure:
        configure_provider(
            port=runtime.port,
            endpoint=endpoint,
            env_key=codex_env_key_name(runtime.config),
        )
        return

    ensure_runtime_credentials(
        runtime.config,
        agent=agent,
        credential_sources=runtime.credential_sources,
        endpoint=endpoint,
    )

    ensure_proxy(
        port=runtime.port,
        config_path=runtime.config_path,
        quiet=args.quiet,
    )

    launch_env: dict[str, str] | None = None
    if agent == "claude":
        _, launch_env = build_claude_env(
            config=runtime.config,
            port=runtime.port,
            endpoint=endpoint,
        )

    print_runtime_env_report(
        quiet=args.quiet,
        credential_sources=runtime.credential_sources,
        port=runtime.port,
        endpoint=endpoint,
        upstream_url=runtime.upstream_url,
        include_agent_recipe=True,
        agent=agent,
        launch_env=launch_env,
        config=runtime.config,
    )

    if agent == "claude":
        raise SystemExit(
            run_claude(
                config=runtime.config,
                port=runtime.port,
                endpoint=endpoint,
                agent_args=agent_args,
            ),
        )

    raise SystemExit(
        run_codex(
            config=runtime.config,
            port=runtime.port,
            endpoint=endpoint,
            agent_args=agent_args,
        ),
    )
