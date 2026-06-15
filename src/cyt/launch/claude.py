"""Claude Code launcher through the CYT reverse proxy."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from cyt.launch.config import launch_claude_models
from cyt.proxy.setup import normalize_upstream_url, upstream_entry_endpoint

_CLAUDE_CANDIDATES = (
    Path.home() / ".local" / "bin" / "claude",
    Path.home() / ".claude" / "local" / "claude",
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


def _upstream_for_endpoint(config: dict[str, Any], endpoint: str) -> dict[str, Any] | None:
    reverse = config.get("network", {}).get("proxy", {}).get("reverse", {})
    upstreams = reverse.get("upstreams", [])
    if not isinstance(upstreams, list):
        return None
    for entry in upstreams:
        if isinstance(entry, dict) and upstream_entry_endpoint(entry) == endpoint:
            return entry
    return None


def _is_openrouter_upstream(entry: dict[str, Any] | None) -> bool:
    if entry is None:
        return False
    url = entry.get("url") or entry.get("host_url") or entry.get("base_url") or ""
    return "openrouter" in normalize_upstream_url(str(url)).lower()


def build_claude_env(
    *,
    config: dict[str, Any],
    port: int,
    endpoint: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Build process env for Claude Code; return (env, reportable non-secrets)."""
    env = dict(os.environ)
    base_url = f"http://localhost:{port}/{endpoint}"
    env["ANTHROPIC_BASE_URL"] = base_url
    reportable = {"ANTHROPIC_BASE_URL": base_url}

    upstream = _upstream_for_endpoint(config, endpoint)
    if _is_openrouter_upstream(upstream):
        token = os.environ.get("OPENROUTER_API_KEY", "")
        env["ANTHROPIC_AUTH_TOKEN"] = token
        env.setdefault("ANTHROPIC_API_KEY", "")
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
) -> int:
    """Exec Claude Code with proxy env wiring."""
    claude = find_claude()
    env, _reportable = build_claude_env(config=config, port=port, endpoint=endpoint)
    try:
        result = subprocess.run([claude, *agent_args], env=env, check=False)
    except KeyboardInterrupt:
        return 130
    return int(result.returncode)
