"""Unit tests for hook permissions change fan-out."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cyt.hook.permissions_react import react_to_permissions_changed


def test_react_to_permissions_changed_fan_out(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    manager = MagicMock()

    with (
        patch("cyt.skills.catalog.clear_registry_cache") as clear_skills,
        patch(
            "cyt.cyt_mcp.catalog.invalidate_cyt_mcp_catalog_for_workspace",
        ) as invalidate_catalog,
        patch("cyt.tiers.manager.get_tier_manager", return_value=manager) as get_manager,
        patch("cyt.hook.permissions_react._hook_config_for_workspace", return_value={"tiers": {}}),
    ):
        react_to_permissions_changed(agent="cursor", workspace_root=workspace)

    clear_skills.assert_called_once()
    invalidate_catalog.assert_called_once_with("cursor", workspace)
    get_manager.assert_called_once()
    manager.purge_inactive_tool_sources.assert_called_once()
