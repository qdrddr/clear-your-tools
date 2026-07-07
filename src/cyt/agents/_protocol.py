"""Agent capability protocol (types only; no registry imports)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from cyt.agents._types import AgentName


@dataclass(frozen=True)
class LaunchCapability:
    run: Callable[..., int]


@dataclass(frozen=True)
class HookCapability:
    """Hook config install/uninstall (not runtime injection)."""

    settings_path: Path
    skills_dir: Path
    install_hooks: Callable[..., bool] | None = None


@dataclass(frozen=True)
class ProxyCapability:
    """Launch-time proxy wiring (env vars, config.toml blocks)."""

    configure: Callable[..., None] | None = None
    restore: Callable[..., None] | None = None


@dataclass(frozen=True)
class SkillsHookCapability:
    skills_dir: Path
    normalize_payload: Callable[[dict[str, Any]], dict[str, Any]]
    transcript_agent: Literal["claude", "codex", "cursor"] | None
    parse_last_assistant: Callable[[list[Any]], str | None] | None = None
    parse_model_from_records: Callable[[list[Any]], str | None] | None = None


@dataclass(frozen=True)
class SkillsProxyCapability:
    upstream_kind: str
    inject_matches_into_body: Callable[..., tuple[dict[str, Any], Any]]
    finish_deferred: Callable[..., tuple[dict[str, Any], Any]]
    skills_search_query: Callable[[dict[str, Any]], str | None]


@dataclass(frozen=True)
class AgentCapabilities:
    name: AgentName
    launch: LaunchCapability
    hook: HookCapability
    proxy: ProxyCapability | None
    skills_hook: SkillsHookCapability
    skills_proxy: SkillsProxyCapability | None
