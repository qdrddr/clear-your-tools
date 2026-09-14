"""Unit tests for targeted cyt-mcp catalog invalidation on permissions change."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from cyt.cyt_mcp.catalog import invalidate_cyt_mcp_catalog_for_workspace


def test_invalidate_cyt_mcp_catalog_for_workspace_schedules_refreshes(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()

    with (
        patch(
            "cyt.cyt_mcp.catalog.uses_cyt_mcp_tool_catalog",
            return_value=True,
        ),
        patch(
            "cyt.cyt_mcp.cache_scheduler.schedule_cyt_mcp_catalog_refresh",
        ) as schedule_cyt_mcp,
        patch(
            "cyt.tools.master_cache_scheduler.schedule_master_catalog_refresh",
        ) as schedule_master,
    ):
        invalidate_cyt_mcp_catalog_for_workspace("cursor", workspace)

    schedule_cyt_mcp.assert_called_once()
    assert schedule_cyt_mcp.call_args.kwargs.get("force") is True
    schedule_master.assert_called_once()
