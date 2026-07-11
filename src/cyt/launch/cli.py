"""``cyt launch`` command orchestration."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from cyt.agents._registry import get_agent
from cyt.agents.claude.launch import build_claude_env
from cyt.agents.codex.launch import configure_provider, restore_provider
from cyt.agents.cursor.launch import (
    ensure_cursor_hooks_for_launch,
    ensure_cursor_inject_via_hook,
)
from cyt.common.agents import LAUNCH_AGENTS, launch_agent_usage_hint
from cyt.config import (
    DEFAULT_REVERSE_PORT,
    inject_via,
    launch_needs_proxy,
    load_config,
    required_proxy_env_var_names,
    required_tools_hook_env_var_names,
    tools_enabled,
    tools_hook_tools_from,
)
from cyt.launch.agent_credentials import AgentAuthBinding, ensure_agent_upstream_auth
from cyt.launch.config import codex_env_key_name
from cyt.launch.endpoints import resolve_agent_endpoint
from cyt.launch.env_report import print_runtime_env_report
from cyt.launch.proxy_guard import (
    ProxyGuard,
    ensure_proxy,
    require_healthy_proxy,
    resolve_launch_port,
)
from cyt.launch.quiet import configure_launch_quiet
from cyt.launch.secrets import ensure_named_credentials
from cyt.launch.upstream import AgentName, parse_agent_name, resolve_upstream_kind
from cyt.proxy.bootstrap import RuntimeContext, prepare_runtime
from cyt.proxy.setup_wizard import normalize_upstream_kind
from cyt.skills.agents import launch_agent_env
from cyt.tools.hook_setup import ensure_tools_hook_file_interactive


def parse_launch_remainder(remainder: list[str]) -> tuple[AgentName, list[str]]:
    """Parse ``-- claude|codex|cursor [args...]``."""
    if not remainder or remainder[0] != "--":
        raise SystemExit(
            f"cyt launch requires `--` followed by an agent name.\n\n{launch_agent_usage_hint()}",
        )
    if len(remainder) < 2:
        raise SystemExit(
            f"Missing agent name after `--`.\n\n{launch_agent_usage_hint()}",
        )
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


def add_launch_options(parser: argparse.ArgumentParser) -> None:
    add_shared_upstream_args(parser)
    for action in parser._actions:
        if action.dest == "port":
            action.help = (
                f"Base reverse port for launch scan (default: configured proxy port, "
                f"else {DEFAULT_REVERSE_PORT}; spawns on base port when free)"
            )
            break
    parser.add_argument(
        "--endpoint",
        default=None,
        help="Reverse proxy endpoint name for this launch",
    )
    parser.add_argument(
        "--configure",
        action="store_true",
        help="Write Codex provider config only (codex agent)",
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help="Legacy no-op for codex (managed ~/.codex/config.toml is preserved)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Log transformed requests to {endpoint}.log and forward to upstream",
    )
    parser.add_argument(
        "--debug-dry-run",
        action="store_true",
        help="Dry-run: log transformed requests to {endpoint}.log without calling upstream",
    )
    parser.add_argument(
        "--debug-strict",
        action="store_true",
        help="With --debug-dry-run, return 502 when pruning did not apply",
    )
    parser.add_argument(
        "--switch-provider",
        action="store_true",
        help=(
            "In hook injection mode, route the agent directly to the configured upstream "
            "(sets ANTHROPIC_* for Claude or Codex -c provider overrides)"
        ),
    )
    parser.add_argument(
        "--proxy",
        action="store_true",
        help=(
            "In hook injection mode, start the reverse proxy and route the agent through it "
            "(use with --debug to capture request logs; cannot combine with --switch-provider)"
        ),
    )


def add_launch_parser(subparsers: argparse._SubParsersAction) -> None:
    launch_parser = subparsers.add_parser(
        "launch",
        help="Launch Claude Code, Codex, or Cursor through CYT",
    )
    add_launch_options(launch_parser)
    launch_parser.add_argument(
        "remainder",
        nargs=argparse.REMAINDER,
        help="Use `-- claude|codex|cursor [agent args...]`",
    )


_AGENT_SHORTCUT_HELP: dict[AgentName, str] = {
    "claude": "Launch Claude Code through CYT (shortcut for cyt launch -- claude)",
    "codex": "Launch Codex through CYT (shortcut for cyt launch -- codex)",
    "cursor": "Launch Cursor through CYT (shortcut for cyt launch -- cursor)",
}


def add_agent_launch_shortcut_parsers(subparsers: argparse._SubParsersAction) -> None:
    for agent in LAUNCH_AGENTS:
        parser = subparsers.add_parser(agent, help=_AGENT_SHORTCUT_HELP[agent])
        add_launch_options(parser)


def launch_args_from_shortcut(args: argparse.Namespace, agent: AgentName) -> argparse.Namespace:
    args.command = "launch"
    args.remainder = ["--", agent]
    return args


def _validate_upstream_kind_args(args: argparse.Namespace) -> None:
    upstream_url = getattr(args, "upstream", None)
    upstream_kind = getattr(args, "upstream_kind", None)
    upstream_name = getattr(args, "upstream_name", None)
    if upstream_kind is not None and upstream_url is None:
        raise SystemExit("--upstream-kind requires --upstream")
    if upstream_name is not None and upstream_url is None:
        raise SystemExit("--upstream-name requires --upstream")


def _launch_switch_provider(args: argparse.Namespace) -> bool:
    return getattr(args, "switch_provider", False) is True


def _launch_force_proxy(args: argparse.Namespace) -> bool:
    return getattr(args, "proxy", False) is True


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
    launch_before_env: dict[str, str] | None = None,
) -> tuple[RuntimeContext, AgentAuthBinding | None]:
    if endpoint is None:
        return runtime, None
    runtime.config, binding = ensure_agent_upstream_auth(
        agent=agent,
        config=runtime.config,
        config_path=runtime.config_path,
        endpoint=endpoint,
        credential_sources=runtime.credential_sources,
        launch_before_env=launch_before_env,
    )
    return runtime, binding


def _validate_cursor_launch_args(args: argparse.Namespace) -> None:
    if (
        args.upstream is not None
        or args.upstream_kind is not None
        or args.upstream_name is not None
    ):
        raise SystemExit("Cursor launch does not use CYT upstream routing; omit --upstream flags.")
    if args.configure or args.restore:
        raise SystemExit("--configure and --restore are not supported for cursor.")
    if _launch_switch_provider(args) or _launch_force_proxy(args):
        raise SystemExit(
            "Cursor launch uses hook injection only; omit --proxy and --switch-provider.",
        )


def _run_launched_agent(
    *,
    agent: AgentName,
    runtime: RuntimeContext,
    endpoint: str,
    agent_args: list[str],
    auth_binding: AgentAuthBinding | None = None,
    use_proxy: bool = True,
    switch_provider: bool = False,
) -> int:
    return get_agent(agent).launch.run(
        config=runtime.config,
        port=runtime.port,
        endpoint=endpoint,
        agent_args=agent_args,
        auth_binding=auth_binding,
        use_proxy=use_proxy,
        switch_provider=switch_provider,
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
    use_proxy: bool,
    switch_provider: bool,
) -> dict[str, str] | None:
    from cyt.hook.port import hook_url_for_port

    launch_env: dict[str, str] | None = None
    if agent == "claude" and (use_proxy or switch_provider):
        _, launch_env = build_claude_env(
            config=runtime.config,
            port=runtime.port,
            endpoint=endpoint,
            auth_binding=auth_binding,
            use_proxy=use_proxy,
            switch_provider=switch_provider,
        )
    elif agent == "codex" and (use_proxy or switch_provider) and auth_binding is not None:
        launch_env = {auth_binding.agent_env_var: auth_binding.source}

    hook_env = {"CYT_HOOK_URL": hook_url_for_port(runtime.port)}
    if launch_env is None:
        return hook_env
    launch_env.update(hook_env)
    return launch_env


def _ensure_hook_credentials_for_launch(
    config: dict[str, Any],
    *,
    credential_sources: dict[str, str] | None = None,
) -> None:
    """Resolve hook credentials before starting or restarting the hook daemon."""
    from cyt.config import inject_via

    if inject_via(config) != "hook":
        return

    names = list(
        dict.fromkeys(
            [
                *required_tools_hook_env_var_names(config),
                *required_proxy_env_var_names(config),
            ],
        ),
    )
    if not names:
        return
    ensure_named_credentials(
        names,
        credential_sources=credential_sources,
        allow_prompt=sys.stdin.isatty(),
        require_all=False,
    )


def _ensure_hook_server(
    *,
    runtime: RuntimeContext,
) -> None:
    from cyt.hook.daemon import daemon_start

    config = runtime.config
    _ensure_hook_credentials_for_launch(
        config,
        credential_sources=runtime.credential_sources,
    )

    result = daemon_start(
        config_path=runtime.config_path,
        verbose=False,
        unattended=True,
    )
    runtime.port = result.port


def _run_cursor_launch_session(
    *,
    args: argparse.Namespace,
    agent_args: list[str],
    runtime: RuntimeContext,
) -> int:
    debug, debug_dry_run, debug_strict = _launch_debug_flags(args)
    if debug or debug_dry_run or debug_strict:
        raise SystemExit("Cursor launch does not support --debug flags.")

    config = runtime.config
    if sys.stdin.isatty():
        config = ensure_tools_hook_file_interactive(runtime.config_path, config)
        config = ensure_cursor_inject_via_hook(runtime.config_path, config)
        ensure_cursor_hooks_for_launch()
        runtime.config = config
    elif inject_via(config) != "hook":
        raise SystemExit(
            "Cursor only supports hook injection (pruning.inject_via: hook). "
            "Update your CYT config and retry.",
        )
    else:
        ensure_cursor_hooks_for_launch(quiet=True)

    from cyt.tools.sources.executor_http import schedule_executor_catalog_refresh

    if (
        tools_enabled(config)
        and tools_hook_tools_from(config) == "executor"
        and inject_via(config) == "hook"
    ):
        schedule_executor_catalog_refresh(config, allow_prompt=False, force=True)

    from cyt.cache import warm_caches

    warm_caches(config)
    os.environ.setdefault("CYT_HOOK_CWD", str(Path.cwd()))

    os.environ.update(launch_agent_env("cursor"))
    _ensure_hook_server(runtime=runtime)

    from cyt.hook.port import hook_url_for_port

    print_runtime_env_report(
        quiet=False,
        credential_sources=runtime.credential_sources,
        port=runtime.port,
        endpoint=None,
        upstream_url=None,
        include_agent_recipe=True,
        agent="cursor",
        launch_env={"CYT_HOOK_URL": hook_url_for_port(runtime.port)},
        config=runtime.config,
        config_path=runtime.config_path,
        auth_binding=None,
        debug=False,
        debug_dry_run=False,
        debug_strict=False,
        hook_mode=True,
        switch_provider=False,
    )

    return get_agent("cursor").launch.run(agent_args=agent_args)


def _run_launch_session(
    *,
    args: argparse.Namespace,
    agent: AgentName,
    agent_args: list[str],
    runtime: RuntimeContext,
    endpoint: str,
) -> int:
    debug, debug_dry_run, debug_strict = _launch_debug_flags(args)
    switch_provider = _launch_switch_provider(args)
    force_proxy = _launch_force_proxy(args)

    config = runtime.config
    if sys.stdin.isatty():
        config = ensure_tools_hook_file_interactive(runtime.config_path, config)
        runtime.config = config

    from cyt.tools.sources.executor_http import schedule_executor_catalog_refresh

    if (
        tools_enabled(config)
        and tools_hook_tools_from(config) == "executor"
        and inject_via(config) == "hook"
    ):
        schedule_executor_catalog_refresh(config, allow_prompt=False, force=True)

    from cyt.cache import warm_caches

    warm_caches(config)
    os.environ.setdefault("CYT_HOOK_CWD", str(Path.cwd()))

    inject_via_hook = not launch_needs_proxy(config)
    if switch_provider and not inject_via_hook:
        raise SystemExit("--switch-provider is only supported when pruning.inject_via is hook.")
    if force_proxy and switch_provider:
        raise SystemExit("Pass either --proxy or --switch-provider, not both.")
    if force_proxy and not inject_via_hook:
        raise SystemExit("--proxy is only supported when pruning.inject_via is hook.")

    use_proxy = launch_needs_proxy(config) or force_proxy
    hook_mode = inject_via_hook and not force_proxy

    os.environ.update(launch_agent_env(agent))
    launch_before_env = dict(os.environ)

    auth_binding: AgentAuthBinding | None = None
    if use_proxy or switch_provider:
        runtime, auth_binding = _ensure_launch_agent_auth(
            runtime,
            agent=agent,
            endpoint=endpoint,
            launch_before_env=launch_before_env,
        )

    if use_proxy:
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
    else:
        proxy_guard = ProxyGuard(process=None, started_by_launch=False, port=runtime.port)
        _ensure_hook_server(runtime=runtime)

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
            use_proxy=use_proxy,
            switch_provider=switch_provider,
        ),
        config=runtime.config,
        config_path=runtime.config_path,
        auth_binding=auth_binding,
        debug=debug,
        debug_dry_run=debug_dry_run,
        debug_strict=debug_strict,
        hook_mode=hook_mode,
        switch_provider=switch_provider,
    )

    try:
        return _run_launched_agent(
            agent=agent,
            runtime=runtime,
            endpoint=endpoint,
            agent_args=agent_args,
            auth_binding=auth_binding,
            use_proxy=use_proxy,
            switch_provider=switch_provider,
        )
    finally:
        proxy_guard.terminate_if_started()


def run(args: argparse.Namespace) -> None:
    """Run ``cyt launch`` after argparse."""
    _validate_upstream_kind_args(args)
    agent, agent_args = parse_launch_remainder(getattr(args, "remainder", []))

    if args.configure and args.restore:
        raise SystemExit("Pass either --configure or --restore, not both.")

    if args.configure or args.restore:
        if agent != "codex":
            raise SystemExit("--configure and --restore are only supported for codex.")

    if agent == "cursor":
        _validate_cursor_launch_args(args)

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

    if agent == "cursor":
        raise SystemExit(
            _run_cursor_launch_session(
                args=args,
                agent_args=agent_args,
                runtime=runtime,
            ),
        )

    raise SystemExit(
        _run_launch_session(
            args=args,
            agent=agent,
            agent_args=agent_args,
            runtime=runtime,
            endpoint=endpoint,
        ),
    )
