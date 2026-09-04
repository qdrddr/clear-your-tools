"""Ensure workspace config lives at the canonical .agents/cyt path before migration."""

from __future__ import annotations

import logging
from pathlib import Path

from cyt.hook.install_scope import CytInstallScope, HookAgentName

logger = logging.getLogger(__name__)

_AGENTS: tuple[HookAgentName, ...] = ("cursor", "claude", "codex")


def ensure_canonical_workspace_config(scope: CytInstallScope) -> Path | None:
    """Copy or rename legacy workspace config.yaml to ``.agents/cyt/config/config.yaml``.

    When the canonical file already exists, legacy paths are left unchanged.
    """
    canonical = scope.workspace_all_agents_cyt_config_path()
    if canonical is None:
        return None
    if canonical.is_file():
        return canonical

    for agent in _AGENTS:
        for legacy in scope._legacy_workspace_cyt_config_paths(agent):
            if legacy.is_file() and legacy.resolve() != canonical.resolve():
                canonical.parent.mkdir(parents=True, exist_ok=True)
                legacy.rename(canonical)
                logger.info("Moved workspace config %s -> %s", legacy, canonical)
                return canonical
    return canonical


def ensure_canonical_workspace_aggregator(scope: CytInstallScope) -> Path | None:
    """Copy or rename legacy mcp-aggregator.yaml to ``.agents/cyt/config/mcp-aggregator.yaml``.

    When the canonical file already exists, legacy paths are left unchanged.
    """
    canonical = scope.workspace_all_agents_cyt_aggregator_path()
    if canonical is None:
        return None
    if canonical.is_file():
        return canonical

    for agent in _AGENTS:
        for legacy in scope._legacy_workspace_aggregator_paths(agent):
            if legacy.is_file() and legacy.resolve() != canonical.resolve():
                canonical.parent.mkdir(parents=True, exist_ok=True)
                legacy.rename(canonical)
                logger.info("Moved workspace aggregator %s -> %s", legacy, canonical)
                return canonical
    return canonical


def ensure_canonical_workspace_server_defs(
    scope: CytInstallScope,
    agent: str,
) -> Path | None:
    """Copy or rename legacy MCP server defs to ``.agents/cyt/config/mcp/<agent>.json``.

    When the canonical file already exists, legacy paths are left unchanged.
    """
    canonical = scope.workspace_all_agents_cyt_mcp_defs_path(agent)
    if canonical is None:
        return None
    if canonical.is_file():
        return canonical

    for legacy in scope._legacy_workspace_server_defs_paths(agent):
        if legacy.is_file() and legacy.resolve() != canonical.resolve():
            canonical.parent.mkdir(parents=True, exist_ok=True)
            legacy.rename(canonical)
            logger.info("Moved workspace MCP server defs %s -> %s", legacy, canonical)
            return canonical
    return canonical


def resolve_workspace_config_path(scope: CytInstallScope | None = None) -> Path | None:
    """Return canonical workspace config path, promoting legacy files when needed."""
    resolved = scope or CytInstallScope.from_cwd()
    if not resolved.has_workspace:
        return None
    return ensure_canonical_workspace_config(resolved)
