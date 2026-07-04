"""Launch-specific configuration helpers."""

from __future__ import annotations

from typing import Any

from cyt.common.agents import AgentName
from cyt.config import _config_with_bundled_defaults, required_proxy_env_var_names

_DEFAULT_CODEX_ENV_KEY = "CODEX_OPENAI_API_KEY"


def launch_section(config: dict[str, Any]) -> dict[str, Any]:
    section = config.get("launch")
    return section if isinstance(section, dict) else {}


def agent_launch_config(config: dict[str, Any], agent: AgentName) -> dict[str, Any]:
    section = launch_section(config)
    agent_cfg = section.get(agent)
    return agent_cfg if isinstance(agent_cfg, dict) else {}


def launch_endpoint_override(config: dict[str, Any], agent: AgentName) -> str | None:
    endpoint = agent_launch_config(config, agent).get("endpoint")
    if endpoint is None:
        return None
    text = str(endpoint).strip()
    return text or None


def launch_claude_models(config: dict[str, Any]) -> dict[str, str]:
    models = agent_launch_config(config, "claude").get("models")
    if not isinstance(models, dict):
        return {}
    result: dict[str, str] = {}
    for key in ("opus", "sonnet", "haiku", "subagent"):
        value = models.get(key)
        if value is not None and str(value).strip():
            result[key] = str(value).strip()
    return result


def codex_env_key_name(config: dict[str, Any]) -> str:
    env_key = agent_launch_config(config, "codex").get("env_key")
    if env_key is not None and str(env_key).strip():
        return str(env_key).strip()
    return _DEFAULT_CODEX_ENV_KEY


def _append_unique(names: list[str], seen: set[str], name: str) -> None:
    if name not in seen:
        seen.add(name)
        names.append(name)


def required_launch_env_var_names(
    config: dict[str, Any],
    agent: AgentName,
    *,
    endpoint: str | None = None,
) -> list[str]:
    """Environment variable names required for ``cyt launch``.

    Only tool/skills pruner pipeline keys are collected here. Reverse-proxy
    upstream credentials (Anthropic, OpenRouter, etc.) are supplied by the
    launched agent or existing shell/.env configuration.
    """
    del endpoint  # kept for callers; upstream keys are not resolved at launch
    merged = _config_with_bundled_defaults(config)
    required: list[str] = []
    seen: set[str] = set()

    for name in required_proxy_env_var_names(merged):
        _append_unique(required, seen, name)

    return required
