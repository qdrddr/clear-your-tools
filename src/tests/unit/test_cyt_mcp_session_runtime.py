"""Tests for per-session workspace runtime scoping."""

from __future__ import annotations

from pathlib import Path

from cyt_mcp.config import sample_aggregator_config
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.catalog_build import build_catalog_from_tools
from cyt_mcp.session_runtime import (
    WorkspaceSessionRuntime,
    root_uri_to_path,
    tool_belongs_to_runtime,
)
from cyt_mcp.tool_identity import tool_name_allowed_for_servers
from fastmcp.tools.base import Tool


def test_root_uri_to_path_windows_file_url() -> None:
    path = root_uri_to_path("file:///C:/Users/me/git/tra")
    assert path == Path("C:/Users/me/git/tra")


def test_root_uri_to_path_bare_windows_path() -> None:
    path = root_uri_to_path("C:/Users/me/git/tra")
    assert path == Path("C:/Users/me/git/tra")


def test_tool_belongs_to_runtime_filters_by_server_prefix() -> None:
    config = sample_aggregator_config(
        mcp_servers={"fff": {}, "semble": {}},
        workspace_root=Path("/tmp/tra"),
    )
    runtime = WorkspaceSessionRuntime(
        workspace_root=Path("/tmp/tra"),
        config=config,
        cache=RuntimeToolCache(),
        config_holder=ConfigHolder(config),
    )
    assert tool_belongs_to_runtime("fff_find_files", runtime) is True
    assert tool_belongs_to_runtime("gitnexus_query", runtime) is False
    assert tool_belongs_to_runtime("get-tool-definitions", runtime) is True


def test_tool_name_allowed_for_servers() -> None:
    keys = ["fff", "semble"]
    assert tool_name_allowed_for_servers("fff_find_files", keys) is True
    assert tool_name_allowed_for_servers("gitnexus_query", keys) is False
    assert tool_name_allowed_for_servers("get-tool-definitions", keys) is True


def test_build_catalog_from_tools_filters_by_mcp_server_keys() -> None:
    tools = [
        Tool.from_function(lambda: None, name="fff_find_files"),
        Tool.from_function(lambda: None, name="gitnexus_query"),
        Tool.from_function(lambda: None, name="get-tool-definitions"),
    ]
    catalog, index = build_catalog_from_tools(tools, mcp_server_keys=["fff", "semble"])
    names = {entry["name"] for entry in catalog}
    assert names == {"fff_find_files"}
    assert "gitnexus_query" not in index
    assert "get-tool-definitions" not in names
