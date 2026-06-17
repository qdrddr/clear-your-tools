"""``cyt launch`` command orchestration."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from cyt.config import DEFAULT_REVERSE_PORT, load_config
from cyt.launch.agent_credentials import AgentAuthBinding, ensure_agent_upstream_auth
from cyt.launch.claude import build_claude_env
from cyt.launch.claude import run as run_claude
from cyt.launch.codex import configure_provider, restore_provider
from cyt.launch.codex import run as run_codex
from cyt.launch.config import codex_env_key_name
from cyt.launch.endpoints import resolve_agent_endpoint
from cyt.launch.env_report import print_runtime_env_report
from cyt.launch.proxy_guard import (
    ensure_proxy,
    require_healthy_proxy,
    resolve_launch_port,
)
from cyt.launch.quiet import configure_launch_quiet
from cyt.launch.upstream import AgentName, parse_agent_name, resolve_upstream_kind
from cyt.proxy.bootstrap import RuntimeContext, prepare_runtime
from cyt.proxy.setup import normalize_upstream_kind
from cyt.skills.agents import launch_agent_env


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
        help=argparse.SUPPRESS,
    )


def add_launch_parser(subparsers: argparse._SubParsersAction) -> None:
    launch_parser = subparsers.add_parser(
        "launch",
        help="Launch Claude Code or Codex through the CYT proxy",
    )
    add_shared_upstream_args(launch_parser)
    for action in launch_parser._actions:
        if action.dest == "port":
            action.help = (
                f"Base reverse port for launch scan (default: configured proxy port, "
                f"else {DEFAULT_REVERSE_PORT}; spawns on base port when free)"
            )
            break
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
        help="Legacy no-op for codex (managed ~/.codex/config.toml is preserved)",
    )
    launch_parser.add_argument(
        "--debug",
        action="store_true",
        help="Log transformed requests to {endpoint}.log and forward to upstream",
    )
    launch_parser.add_argument(
        "--debug-dry-run",
        action="store_true",
        help="Dry-run: log transformed requests to {endpoint}.log without calling upstream",
    )
    launch_parser.add_argument(
        "--debug-strict",
        action="store_true",
        help="With --debug-dry-run, return 502 when pruning did not apply",
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


def _launch_debug_flags(args: argparse.Namespace) -> tuple[bool, bool, bool]:
    return (
        bool(getattr(args, "debug", False)),
        bool(getattr(args, "debug_dry_run", False)),
        bool(getattr(args, "debug_strict", False)),
    )


def _ensure_launch_agent_auth(
    runtime: RuntimeContext,
    *,
    agent: AgentName,
    endpoint: str | None,
) -> tuple[RuntimeContext, AgentAuthBinding | None]:
    if endpoint is None:
        return runtime, None
    runtime.config, binding = ensure_agent_upstream_auth(
        agent=agent,
        config=runtime.config,
        config_path=runtime.config_path,
        endpoint=endpoint,
        credential_sources=runtime.credential_sources,
    )
    return runtime, binding


def _run_launched_agent(
    *,
    agent: AgentName,
    runtime: RuntimeContext,
    endpoint: str,
    agent_args: list[str],
    auth_binding: AgentAuthBinding | None = None,
) -> int:
    if agent == "claude":
        return run_claude(
            config=runtime.config,
            port=runtime.port,
            endpoint=endpoint,
            agent_args=agent_args,
            auth_binding=auth_binding,
        )
    return run_codex(
        config=runtime.config,
        port=runtime.port,
        endpoint=endpoint,
        agent_args=agent_args,
        auth_binding=auth_binding,
    )


def _proxy_spawn_extra_env(
    *,
    credential_sources: dict[str, str],
    auth_binding: AgentAuthBinding | None,
) -> dict[str, str] | None:
    """Credential env vars for a launch-spawned proxy child (never agent auth)."""
    extra: dict[str, str] = {}
    agent_var = auth_binding.agent_env_var if auth_binding is not None else None
    for name in credential_sources:
        if name == agent_var:
            continue
        if value := os.environ.get(name):
            extra[name] = value
    return extra or None


def _launch_env_for_agent(
    *,
    agent: AgentName,
    runtime: RuntimeContext,
    endpoint: str,
    auth_binding: AgentAuthBinding | None,
) -> dict[str, str] | None:
    if agent == "claude":
        _, launch_env = build_claude_env(
            config=runtime.config,
            port=runtime.port,
            endpoint=endpoint,
            auth_binding=auth_binding,
        )
        return launch_env
    if auth_binding is not None:
        return {auth_binding.agent_env_var: auth_binding.source}
    return None


def _run_launch_session(
    *,
    args: argparse.Namespace,
    agent: AgentName,
    agent_args: list[str],
    runtime: RuntimeContext,
    endpoint: str,
) -> int:
    debug, debug_dry_run, debug_strict = _launch_debug_flags(args)

    os.environ.update(launch_agent_env(agent))

    runtime, auth_binding = _ensure_launch_agent_auth(
        runtime,
        agent=agent,
        endpoint=endpoint,
    )

    proxy_extra_env = _proxy_spawn_extra_env(
        credential_sources=runtime.credential_sources,
        auth_binding=auth_binding,
    )

    proxy_guard = ensure_proxy(
        base_port=runtime.port,
        required_endpoint=endpoint,
        config_path=runtime.config_path,
        quiet=True,
        agent=agent,
        debug=debug,
        debug_dry_run=debug_dry_run,
        debug_strict=debug_strict,
        extra_env=proxy_extra_env,
    )
    runtime.port = proxy_guard.port
    require_healthy_proxy(
        port=runtime.port,
        debug=debug,
        debug_dry_run=debug_dry_run,
    )

    print_runtime_env_report(
        quiet=False,
        credential_sources=runtime.credential_sources,
        port=runtime.port,
        endpoint=endpoint,
        upstream_url=runtime.upstream_url,
        include_agent_recipe=True,
        agent=agent,
        launch_env=_launch_env_for_agent(
            agent=agent,
            runtime=runtime,
            endpoint=endpoint,
            auth_binding=auth_binding,
        ),
        config=runtime.config,
        config_path=runtime.config_path,
        auth_binding=auth_binding,
        debug=debug,
        debug_dry_run=debug_dry_run,
        debug_strict=debug_strict,
    )

    return _run_launched_agent(
        agent=agent,
        runtime=runtime,
        endpoint=endpoint,
        agent_args=agent_args,
        auth_binding=auth_binding,
    )


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

    configure_launch_quiet()

    runtime = prepare_runtime(
        agent=agent,
        config_path=args.config,
        port=args.port,
        upstream_url=args.upstream,
        upstream_kind=upstream_kind,
        upstream_name=args.upstream_name,
    )

    endpoint = resolve_agent_endpoint(
        runtime.config,
        agent=agent,
        config_path=runtime.config_path,
        endpoint_override=args.endpoint,
        upstream_cli_endpoint=runtime.upstream_endpoint,
    )

    debug, debug_dry_run, _debug_strict = _launch_debug_flags(args)

    if args.configure:
        configure_provider(
            port=resolve_launch_port(
                runtime.port,
                required_endpoint=endpoint,
                quiet=True,
                debug=debug,
                debug_dry_run=debug_dry_run,
            ),
            endpoint=endpoint,
            env_key=codex_env_key_name(runtime.config),
        )
        return

    raise SystemExit(
        _run_launch_session(
            args=args,
            agent=agent,
            agent_args=agent_args,
            runtime=runtime,
            endpoint=endpoint,
        ),
    )
