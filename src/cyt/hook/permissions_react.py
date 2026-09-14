"""Hook-side reactions when MCP/skills permission overlays change."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _hook_config_for_workspace(*, agent: str, workspace_root: Path) -> dict[str, Any]:
    from cyt.hook.workspace_config import set_hook_workspace_in_config
    from cyt.permissions.merge import merged_hook_config

    normalized_agent = (agent or "cursor").strip().lower() or "cursor"
    merged = merged_hook_config(normalized_agent, workspace_root=workspace_root)
    return set_hook_workspace_in_config(merged, workspace_root)


def react_to_permissions_changed(*, agent: str, workspace_root: str | Path) -> None:
    """Invalidate hook caches and tier tracking after permissions overlays change."""
    from cyt.skills.catalog import clear_registry_cache

    clear_registry_cache()

    workspace = Path(workspace_root).expanduser()
    normalized_agent = (agent or "cursor").strip().lower() or "cursor"

    try:
        from cyt.cyt_mcp.catalog import invalidate_cyt_mcp_catalog_for_workspace

        invalidate_cyt_mcp_catalog_for_workspace(normalized_agent, workspace)
    except Exception as exc:
        logger.warning(
            "permissions react: cyt-mcp catalog invalidation failed for %s: %s",
            workspace,
            exc,
        )

    try:
        config = _hook_config_for_workspace(agent=normalized_agent, workspace_root=workspace)
    except Exception as exc:
        logger.warning(
            "permissions react: could not build hook config for %s: %s",
            workspace,
            exc,
        )
        return

    try:
        from cyt.tiers.manager import get_tier_manager

        manager = get_tier_manager(config, workspace=workspace)
        purge = getattr(manager, "purge_inactive_tool_sources", None)
        if callable(purge):
            purge(config)
    except Exception as exc:
        logger.warning(
            "permissions react: tier purge failed for %s: %s",
            workspace,
            exc,
        )
