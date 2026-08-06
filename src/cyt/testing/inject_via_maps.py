"""Shared per-agent inject_via maps for tests (no global scalar)."""

from __future__ import annotations

from cyt.config import DEFAULT_INJECT_VIA_BY_AGENT

INJECT_VIA_ALL_HOOK: dict[str, str] = dict.fromkeys(DEFAULT_INJECT_VIA_BY_AGENT, "hook")
INJECT_VIA_DEFAULT: dict[str, str] = dict(DEFAULT_INJECT_VIA_BY_AGENT)
INJECT_VIA_ALL_PROXY: dict[str, str] = dict.fromkeys(DEFAULT_INJECT_VIA_BY_AGENT, "proxy")
