"""Claude Code launcher through the CYT reverse proxy."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from cyt.launch.agent_credentials import AgentAuthBinding
from cyt.launch.config import launch_claude_models
from cyt.launch.upstream_credentials import (
    is_canonical_upstream,
    upstream_for_endpoint,
)

_CLAUDE_CANDIDATES = (
    Path.home() / ".local" / "bin" / "claude",
    Path.home() / ".claude" / "local" / "claude",
)

# OAuth env vars override ANTHROPIC_AUTH_TOKEN in interactive Claude Code sessions.
_CLAUDE_OAUTH_ENV_VARS = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
)

# Hook mode launches Claude with default upstream routing; strip CYT proxy wiring.
_HOOK_MODE_UNSET_ENV_VARS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
)


def find_claude() -> str:
    """Locate the Claude Code CLI binary."""
    if found := shutil.which("claude"):
        return found
    for candidate in _CLAUDE_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    raise SystemExit(
        "Claude Code CLI not found. Install it or add `claude` to PATH.",
    )


def build_claude_env(
    *,
    config: dict[str, Any],
    port: int,
    endpoint: str,
    auth_binding: AgentAuthBinding | None = None,
    use_proxy: bool = True,
    switch_provider: bool = False,
) -> tuple[dict[str, str], dict[str, str]]:
    """Build process env for Claude Code; return (env, reportable non-secrets)."""
    from cyt.launch.upstream import direct_upstream_base_url

    env = dict(os.environ)
    reportable: dict[str, str] = {}
    if not use_proxy and not switch_provider:
        for name in _HOOK_MODE_UNSET_ENV_VARS:
            env.pop(name, None)
        return env, reportable

    if use_proxy:
        base_url = f"http://localhost:{port}/{endpoint}"
    else:
        base_url = direct_upstream_base_url(config, endpoint)
    env["ANTHROPIC_BASE_URL"] = base_url
    reportable["ANTHROPIC_BASE_URL"] = base_url

    upstream = upstream_for_endpoint(config, endpoint)
    if upstream is not None and not is_canonical_upstream(upstream):
        if auth_binding is not None:
            env[auth_binding.agent_env_var] = auth_binding.token
            reportable[auth_binding.agent_env_var] = auth_binding.source
        elif token := os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            env["ANTHROPIC_AUTH_TOKEN"] = token
            reportable["ANTHROPIC_AUTH_TOKEN"] = "env"
        # Claude Code must use ANTHROPIC_AUTH_TOKEN for third-party gateways; clear API key.
        env["ANTHROPIC_API_KEY"] = ""
        reportable["ANTHROPIC_API_KEY"] = '""'
        for oauth_var in _CLAUDE_OAUTH_ENV_VARS:
            env.pop(oauth_var, None)
    else:
        env.setdefault("ANTHROPIC_AUTH_TOKEN", os.environ.get("ANTHROPIC_API_KEY", ""))

    models = launch_claude_models(config)
    model_env_map = {
        "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "subagent": "CLAUDE_CODE_SUBAGENT_MODEL",
    }
    for key, env_name in model_env_map.items():
        if key in models:
            env[env_name] = models[key]
            reportable[env_name] = models[key]

    return env, reportable


def run(
    *,
    config: dict[str, Any],
    port: int,
    endpoint: str,
    agent_args: list[str],
    auth_binding: AgentAuthBinding | None = None,
    use_proxy: bool = True,
    switch_provider: bool = False,
) -> int:
    """Exec Claude Code with proxy or direct upstream env wiring."""
    claude = find_claude()
    env, _reportable = build_claude_env(
        config=config,
        port=port,
        endpoint=endpoint,
        auth_binding=auth_binding,
        use_proxy=use_proxy,
        switch_provider=switch_provider,
    )
    try:
        result = subprocess.run([claude, *agent_args], env=env, check=False)
    except KeyboardInterrupt:
        return 130
    return int(result.returncode)
