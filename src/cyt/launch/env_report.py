"""Runtime environment summary printed before proxy or agent start."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

from cyt.config import reverse_debug_log_dir
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


def _proxy_recipe_lines(
    *,
    port: int,
    endpoint: str | None,
    upstream_url: str | None,
) -> list[str]:
    lines = [
        "# Manual proxy recipe",
        "export OPENROUTER_API_KEY=...   # if required",
        "export DEEPINFRA_API_KEY=...    # if pruning/skills needs it",
    ]
    if upstream_url:
        lines.append(f"cyt proxy --upstream {upstream_url}")
    else:
        lines.append("cyt proxy")
    lines.append(f"curl -s http://localhost:{port}/health")
    if endpoint is not None:
        lines.append(f"# Proxy endpoint: http://localhost:{port}/{endpoint}")
    return lines


def _claude_recipe_lines(*, port: int, endpoint: str) -> list[str]:
    return [
        "# Manual Claude Code recipe",
        'export ANTHROPIC_AUTH_TOKEN="$OPENROUTER_API_KEY"  # OpenRouter upstream',
        f'export ANTHROPIC_BASE_URL="http://localhost:{port}/{endpoint}"',
        'claude --model haiku -p "say hi"',
    ]


def _codex_recipe_lines(*, port: int, endpoint: str, env_key: str) -> list[str]:
    base_url = f"http://127.0.0.1:{port}/{endpoint}/v1"
    return [
        "# Manual Codex recipe",
        f"export {env_key}=...",
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
    debug: bool = False,
    debug_dry_run: bool = False,
) -> None:
    """Print env summary and manual recipes to stderr."""
    if quiet:
        return

    source_lines = _format_sources(credential_sources)
    if launch_env:
        for key, value in sorted(launch_env.items()):
            source_lines.append(f"  {key}={value}")

    _print_section("Vars used this run:", source_lines or ["  (none)"])
    _print_section(
        "Manual proxy recipe:",
        _proxy_recipe_lines(
            port=port,
            endpoint=endpoint,
            upstream_url=upstream_url,
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

    if agent == "claude":
        recipe = _claude_recipe_lines(port=port, endpoint=endpoint)
    else:
        env_key = codex_env_key_name(config or {})
        recipe = _codex_recipe_lines(port=port, endpoint=endpoint, env_key=env_key)

    _print_section("Manual agent recipe:", recipe)
