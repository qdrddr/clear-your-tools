"""Shared per-agent inject_via maps for tests (no global scalar)."""

from __future__ import annotations

from typing import Any

from cyt.config import inject_via_agents

_agents = inject_via_agents()
INJECT_VIA_ALL_HOOK: dict[str, str] = dict.fromkeys(_agents, "hook")
INJECT_VIA_DEFAULT: dict[str, str] = dict(_agents)
INJECT_VIA_ALL_PROXY: dict[str, str] = dict.fromkeys(_agents, "proxy")


def apply_inject_via_overlay(config: dict[str, Any], inject_via: dict[str, str]) -> None:
    """Write per-agent ``agents.<agent>.tools.inject_via`` (canonical config path)."""
    agents = config.setdefault("agents", {})
    for agent, mode in inject_via.items():
        agents.setdefault(agent, {}).setdefault("tools", {})["inject_via"] = mode
