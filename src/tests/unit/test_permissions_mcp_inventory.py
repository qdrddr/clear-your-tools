"""Tests for MCP permissions inventory listing."""

from __future__ import annotations

from unittest.mock import patch

from cyt.permissions.inventory.mcp import list_mcp_tools_for_server
from cyt.permissions.match import explicit_denied_tools_for_server
from cyt.permissions.schema import EffectivePermissions, McpPermissions


def test_explicit_denied_tools_for_server() -> None:
    assert explicit_denied_tools_for_server("fff", ("fff/find_files", "other/tool")) == [
        "find_files",
    ]


def test_list_mcp_tools_includes_deny_only_tools() -> None:
    catalog_tools = [
        {"name": "fff_grep"},
        {"name": "fff_multi_grep"},
    ]
    effective = EffectivePermissions(
        mcp=McpPermissions(deny=("fff/find_files",), allow=()),
    )
    with patch("cyt.permissions.inventory.mcp.effective_permissions", return_value=effective):
        enabled, disabled = list_mcp_tools_for_server(
            "fff",
            agent="cursor",
            catalog_tools=catalog_tools,
            policy_agent="cursor",
        )
    assert [item.tool for item in enabled] == ["grep", "multi_grep"]
    assert [item.tool for item in disabled] == ["find_files"]
