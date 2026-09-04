"""Normalize permissions deny/allow list entries to canonical string form."""

from __future__ import annotations

import logging
from typing import Any

from cyt.migrations.base import (
    ConfigScope,
    deep_copy_config,
    normalize_permission_list,
    set_schema_stamp,
)

revision = "004_permissions_agents_layout"
down_revision = "003_model_provider_registry"
applies_to = "both"

logger = logging.getLogger(__name__)

_GLOBAL_ONLY_TOP_LEVEL = frozenset({"network", "launch", "stats", "cache", "models", "pruning"})


def _normalize_permissions_block(block: dict[str, Any]) -> None:
    permissions = block.get("permissions")
    if not isinstance(permissions, dict):
        return
    for key in ("deny", "allow"):
        normalized = normalize_permission_list(permissions.get(key))
        if normalized is not None:
            permissions[key] = normalized


def _normalize_skills_or_mcp(cfg: dict[str, Any], key: str) -> None:
    section = cfg.get(key)
    if isinstance(section, dict):
        _normalize_permissions_block(section)


def _normalize_agents_permissions(cfg: dict[str, Any]) -> None:
    agents = cfg.get("agents")
    if not isinstance(agents, dict):
        return
    for agent_block in agents.values():
        if not isinstance(agent_block, dict):
            continue
        for key in ("skills", "mcp"):
            sub = agent_block.get(key)
            if isinstance(sub, dict):
                _normalize_permissions_block(sub)


def _warn_workspace_global_keys(cfg: dict[str, Any], *, scope: ConfigScope) -> None:
    if scope != "workspace":
        return
    for key in _GLOBAL_ONLY_TOP_LEVEL:
        if key in cfg:
            logger.warning(
                "Workspace config contains global-only key %r; left unchanged (migrate manually if needed)",
                key,
            )


def upgrade(cfg: dict[str, Any], *, scope: ConfigScope) -> dict[str, Any]:
    result = deep_copy_config(cfg)
    _warn_workspace_global_keys(result, scope=scope)
    _normalize_skills_or_mcp(result, "skills")
    _normalize_skills_or_mcp(result, "mcp")
    _normalize_agents_permissions(result)
    set_schema_stamp(result, revision)
    return result


def downgrade(cfg: dict[str, Any], *, scope: ConfigScope) -> dict[str, Any]:
    del scope
    raise NotImplementedError("downgrade not supported for 004_permissions_agents_layout")
