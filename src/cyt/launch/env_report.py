"""Runtime environment summary printed before proxy or agent start."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

from cyt.config import reverse_debug_log_dir
from cyt.launch.agent_credentials import AgentAuthBinding
from cyt.launch.config import codex_env_key_name
from cyt.launch.upstream import AgentName

AgentRecipe = Literal["claude", "codex"]


def _print_section(title: str, lines: list[str]) -> None:
    print(f"\n{title}", file=sys.stderr)
    for line in lines:
        print(line, file=sys.stderr)


def _format_sources(credential_sources: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for name, source in sorted(credential_sources.items()):
        lines.append(f"  {name}: {source}")
    return lines


def _proxy_summary_lines(
    *,
    port: int,
    endpoint: str | None,
    agent: AgentName | None = None,
) -> list[str]:
    from cyt.hook.port import hook_url_for_port

    lines = [f"  port: {port}"]
    if agent is not None:
        lines.append(f"  agent: {agent}")
    lines.append(f"  health: http://localhost:{port}/health")
    lines.append(f"  hook: {hook_url_for_port(port)}")
    if endpoint is not None:
        lines.append(f"  endpoint: http://localhost:{port}/{endpoint}")
    return lines


def _credential_export_lines(credential_sources: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for name, source in sorted(credential_sources.items()):
        lines.append(f"export {name}=...  # {source}")
    return lines


def _proxy_recipe_credential_sources(
    credential_sources: dict[str, str],
    *,
    auth_binding: AgentAuthBinding | None = None,
) -> dict[str, str]:
    """Proxy-only credentials (exclude agent-facing auth copied from upstream keys)."""
    agent_var = auth_binding.agent_env_var if auth_binding is not None else None
    return {name: source for name, source in credential_sources.items() if name != agent_var}


def _proxy_recipe_lines(
    *,
    port: int,
    endpoint: str | None,
    upstream_url: str | None,
    credential_sources: dict[str, str],
    config_path: Path | None = None,
    agent: AgentName | None = None,
    auth_binding: AgentAuthBinding | None = None,
    debug: bool = False,
    debug_dry_run: bool = False,
    debug_strict: bool = False,
) -> list[str]:
    lines = ["# Manual proxy recipe (reproduce this launch)"]
    proxy_sources = _proxy_recipe_credential_sources(
        credential_sources,
        auth_binding=auth_binding,
    )
    lines.extend(_credential_export_lines(proxy_sources))
    if lines == ["# Manual proxy recipe (reproduce this launch)"]:
        lines.append("# (no credential env vars required for this run)")
    proxy_cmd = f"cyt proxy --port {port}"
    if config_path is not None:
        proxy_cmd += f" --config {config_path}"
    if upstream_url:
        proxy_cmd += f" --upstream {upstream_url}"
    if agent is not None:
        proxy_cmd += f" --launch-agent {agent}"
    if debug:
        proxy_cmd += " --debug"
    if debug_dry_run:
        proxy_cmd += " --debug-dry-run"
        if debug_strict:
            proxy_cmd += " --debug-strict"
    lines.append(proxy_cmd)
    lines.append(f"curl -s http://localhost:{port}/health")
    if endpoint is not None:
        lines.append(f"# Proxy endpoint: http://localhost:{port}/{endpoint}")
    return lines


def _claude_recipe_lines(
    *,
    port: int,
    endpoint: str,
    auth_binding: AgentAuthBinding | None = None,
    key_var_name: str | None = None,
) -> list[str]:
    lines = ["# Manual Claude Code recipe"]
    if auth_binding is not None:
        lines.append(f"export {auth_binding.agent_env_var}=...  # {auth_binding.source}")
        lines.append('export ANTHROPIC_API_KEY=""')
    elif key_var_name:
        lines.append('export ANTHROPIC_API_KEY=""')
        lines.append(f'export ANTHROPIC_AUTH_TOKEN="${key_var_name}"')
    else:
        lines.append('export ANTHROPIC_AUTH_TOKEN="$ANTHROPIC_API_KEY"')
    lines.extend(
        [
            f'export ANTHROPIC_BASE_URL="http://localhost:{port}/{endpoint}"',
            'claude --model haiku -p "say hi"',
        ],
    )
    return lines


def _codex_recipe_lines(
    *,
    port: int,
    endpoint: str,
    env_key: str,
    auth_binding: AgentAuthBinding | None = None,
) -> list[str]:
    base_url = f"http://127.0.0.1:{port}/{endpoint}/v1"
    source = auth_binding.source if auth_binding is not None else "resolved"
    return [
        "# Manual Codex recipe",
        f"export {env_key}=...  # {source}",
        "codex -m gpt-5.4-mini \\",
        "  -c 'model_provider=\"cyt\"' \\",
        f"  -c 'model_providers.cyt.base_url=\"{base_url}\"' \\",
        "  -c 'model_providers.cyt.wire_api=\"responses\"' \\",
        f"  -c 'model_providers.cyt.env_key=\"{env_key}\"'",
    ]


def _debug_log_lines(*, endpoint: str, debug_log_dir: Path) -> list[str]:
    return [
        "# Debug request snapshots (append-only JSON arrays):",
        f"  {debug_log_dir}/{endpoint}-original.json  # before pruning",
        f"  {debug_log_dir}/{endpoint}.json           # after pruning",
        f"  {debug_log_dir}/{endpoint}-proxy.log      # pruning text log",
    ]


def _launch_env_source_lines(
    launch_env: dict[str, str],
    credential_sources: dict[str, str],
) -> list[str]:
    lines: list[str] = []
    for key, value in sorted(launch_env.items()):
        if key in credential_sources:
            continue
        if key.endswith(("_TOKEN", "_API_KEY")) or "AUTH" in key:
            lines.append(f"  {key}: {value}")
        else:
            lines.append(f"  {key}={value}")
    return lines


def _agent_recipe_lines(
    *,
    agent: AgentName,
    port: int,
    endpoint: str,
    config: dict[str, Any] | None,
    auth_binding: AgentAuthBinding | None,
) -> list[str]:
    if agent == "claude":
        key_var_name = None
        if config is not None:
            from cyt.launch.upstream_credentials import (
                is_canonical_upstream,
                lookup_upstream_key_var,
                upstream_for_endpoint,
            )

            upstream = upstream_for_endpoint(config, endpoint)
            if upstream is not None and not is_canonical_upstream(upstream):
                key_var_name = lookup_upstream_key_var(config, upstream)
        return _claude_recipe_lines(
            port=port,
            endpoint=endpoint,
            auth_binding=auth_binding,
            key_var_name=key_var_name,
        )

    env_key = codex_env_key_name(config or {})
    return _codex_recipe_lines(
        port=port,
        endpoint=endpoint,
        env_key=env_key,
        auth_binding=auth_binding,
    )


def print_runtime_env_report(
    *,
    quiet: bool,
    credential_sources: dict[str, str],
    port: int,
    endpoint: str | None,
    upstream_url: str | None,
    include_agent_recipe: bool,
    agent: AgentName | None = None,
    launch_env: dict[str, str] | None = None,
    config: dict[str, Any] | None = None,
    config_path: Path | None = None,
    auth_binding: AgentAuthBinding | None = None,
    debug: bool = False,
    debug_dry_run: bool = False,
    debug_strict: bool = False,
) -> None:
    """Print env summary and manual recipes to stderr."""
    if quiet:
        return

    source_lines: list[str] = []
    if config is not None and endpoint is not None:
        from cyt.launch.upstream_credentials import (
            describe_upstream_key_var_resolution,
            format_upstream_key_var_resolution_line,
            upstream_for_endpoint,
        )

        upstream = upstream_for_endpoint(config, endpoint)
        if resolution := describe_upstream_key_var_resolution(config, upstream, agent=agent):
            source_lines.append(format_upstream_key_var_resolution_line(resolution))

    source_lines.extend(_format_sources(credential_sources))
    if launch_env:
        source_lines.extend(_launch_env_source_lines(launch_env, credential_sources))

    _print_section(
        "Proxy:",
        _proxy_summary_lines(port=port, endpoint=endpoint, agent=agent),
    )
    _print_section("Vars used this run:", source_lines or ["  (none)"])
    _print_section(
        "Manual proxy recipe:",
        _proxy_recipe_lines(
            port=port,
            endpoint=endpoint,
            upstream_url=upstream_url,
            credential_sources=credential_sources,
            config_path=config_path,
            agent=agent,
            auth_binding=auth_binding,
            debug=debug,
            debug_dry_run=debug_dry_run,
            debug_strict=debug_strict,
        ),
    )

    if (debug or debug_dry_run) and endpoint is not None and config is not None:
        _print_section(
            "Debug logs:",
            _debug_log_lines(
                endpoint=endpoint,
                debug_log_dir=reverse_debug_log_dir(config),
            ),
        )

    if not include_agent_recipe or agent is None or endpoint is None:
        return

    _print_section(
        "Manual agent recipe:",
        _agent_recipe_lines(
            agent=agent,
            port=port,
            endpoint=endpoint,
            config=config,
            auth_binding=auth_binding,
        ),
    )
