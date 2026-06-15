"""Agent-specific skills directory helpers."""

from __future__ import annotations

import os
from pathlib import Path

from cyt.launch.upstream import AgentName, parse_agent_name

SYSTEM_SKILLS_DIR_NAME = ".system"
CYT_LAUNCH_AGENT_ENV = "CYT_LAUNCH_AGENT"

_UPSTREAM_KIND_TO_AGENT: dict[str, AgentName] = {
    "anthropic": "claude",
    "openai": "codex",
}


def agent_from_upstream_kind(upstream_kind: str | None) -> AgentName | None:
    """Map a proxy upstream kind (``anthropic``/``openai`` or aliases) to a launch agent."""
    if upstream_kind is None or not str(upstream_kind).strip():
        return None
    from cyt.proxy.setup import normalize_upstream_kind

    try:
        normalized = normalize_upstream_kind(str(upstream_kind))
    except ValueError:
        return None
    return _UPSTREAM_KIND_TO_AGENT.get(normalized)


def resolve_skills_agent(
    *,
    agent: AgentName | None = None,
    upstream_kind: str | None = None,
) -> AgentName | None:
    """Resolve the active launch agent for skills filtering."""
    if agent is not None:
        return agent

    env_value = os.environ.get(CYT_LAUNCH_AGENT_ENV)
    if isinstance(env_value, str) and env_value.strip():
        try:
            return parse_agent_name(env_value.strip())
        except ValueError:
            pass

    if upstream_kind is not None:
        mapped = agent_from_upstream_kind(upstream_kind)
        if mapped is not None:
            return mapped

    return None


def _normalize_agent_dir_name(name: str) -> AgentName | None:
    normalized = name.removeprefix(".")
    if normalized == "claude":
        return "claude"
    if normalized == "codex":
        return "codex"
    return None


def agent_system_skill_owner(source_path: Path | str) -> AgentName | None:
    """Return the agent that owns a path under ``*/skills/.system``, if any."""
    resolved = Path(source_path).expanduser().resolve()
    parts = resolved.parts
    for index, part in enumerate(parts):
        if part != SYSTEM_SKILLS_DIR_NAME:
            continue
        if index < 2:
            continue
        if parts[index - 1] != "skills":
            continue
        owner = _normalize_agent_dir_name(parts[index - 2])
        if owner is not None:
            return owner
    return None


def is_excluded_agent_system_skill(
    source_path: Path | str,
    *,
    active_agent: AgentName | None,
) -> bool:
    """True when *source_path* is another agent's ``skills/.system`` skill."""
    if active_agent is None:
        return False
    owner = agent_system_skill_owner(source_path)
    return owner is not None and owner != active_agent


def launch_agent_env(agent: AgentName) -> dict[str, str]:
    """Environment overlay for a launched agent process or child proxy."""
    return {CYT_LAUNCH_AGENT_ENV: agent}
