"""Shared per-agent inject_via maps for tests (no global scalar)."""

from __future__ import annotations

from cyt.config import inject_via_agents

_agents = inject_via_agents()
INJECT_VIA_ALL_HOOK: dict[str, str] = dict.fromkeys(_agents, "hook")
INJECT_VIA_DEFAULT: dict[str, str] = dict(_agents)
INJECT_VIA_ALL_PROXY: dict[str, str] = dict.fromkeys(_agents, "proxy")
