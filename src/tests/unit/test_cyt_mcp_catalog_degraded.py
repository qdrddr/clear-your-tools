"""Tests for preserving cached tools when cyt-mcp backends are degraded."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cyt.cyt_mcp.catalog import (
    _merge_degraded_backend_tools,
    apply_fetched_catalog,
    clear_cyt_mcp_catalog_cache,
)


def _tool(name: str, *, server_key: str) -> dict[str, Any]:
    return {
        "name": name,
        "server_key": server_key,
        "input_schema": {"type": "object", "properties": {}},
        "cyt_catalog_source": "cyt_mcp",
    }


def test_merge_degraded_backend_tools_preserves_missing_backend() -> None:
    existing = [
        _tool("mlflow-mcp_search_traces", server_key="mlflow-mcp"),
        _tool("mlflow-mcp_list_runs", server_key="mlflow-mcp"),
        _tool("atlassian-jira-dc_jira_searchIssues", server_key="atlassian-jira-dc"),
    ]
    partial_live = [
        _tool("atlassian-jira-dc_jira_searchIssues", server_key="atlassian-jira-dc"),
        _tool("context7_query-docs", server_key="context7"),
    ]

    merged = _merge_degraded_backend_tools(
        partial_live,
        existing,
        ["mlflow-mcp"],
    )

    names = {tool["name"] for tool in merged}
    assert "mlflow-mcp_search_traces" in names
    assert "mlflow-mcp_list_runs" in names
    assert "context7_query-docs" in names
    assert len(merged) == 4


def test_merge_degraded_backend_tools_preserves_from_disk_when_memory_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cyt.cyt_mcp.catalog_disk.cyt_mcp_catalog_cache_dir",
        lambda: tmp_path,
    )
    from cyt.cyt_mcp.catalog_disk import write_disk_catalog

    disk_tools = [
        _tool("mlflow-mcp_search_traces", server_key="mlflow-mcp"),
        _tool("mlflow-mcp_list_runs", server_key="mlflow-mcp"),
        _tool("atlassian-jira-dc_jira_searchIssues", server_key="atlassian-jira-dc"),
    ]
    write_disk_catalog(
        "cursor",
        agent="cursor",
        tools=disk_tools,
        content_hash="abc",
    )
    partial_live = [
        _tool("atlassian-jira-dc_jira_searchIssues", server_key="atlassian-jira-dc"),
        _tool("context7_query-docs", server_key="context7"),
    ]

    merged = _merge_degraded_backend_tools(
        partial_live,
        [],
        ["mlflow-mcp"],
        disk_tools=disk_tools,
    )

    names = {tool["name"] for tool in merged}
    assert "mlflow-mcp_search_traces" in names
    assert "mlflow-mcp_list_runs" in names
    assert len(merged) == 4


def test_hydrate_missing_servers_from_disk_restores_mlflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt.cyt_mcp.catalog import _CytMcpCacheKey, _hydrate_missing_servers_from_disk

    monkeypatch.setattr(
        "cyt.cyt_mcp.catalog_disk.cyt_mcp_catalog_cache_dir",
        lambda: tmp_path,
    )
    from cyt.cyt_mcp.catalog_disk import write_disk_catalog

    disk_tools = [
        _tool("mlflow-mcp_search_traces", server_key="mlflow-mcp"),
        _tool("atlassian-jira-dc_jira_searchIssues", server_key="atlassian-jira-dc"),
    ]
    write_disk_catalog(
        "cursor",
        agent="cursor",
        tools=disk_tools,
        content_hash="abc",
    )
    memory_tools = [
        _tool("atlassian-jira-dc_jira_searchIssues", server_key="atlassian-jira-dc"),
        _tool("context7_query-docs", server_key="context7"),
    ]

    hydrated = _hydrate_missing_servers_from_disk(
        _CytMcpCacheKey(agent="cursor", slug="cursor"),
        memory_tools,
    )

    names = {tool["name"] for tool in hydrated}
    assert "mlflow-mcp_search_traces" in names
    assert len(hydrated) == 3


def test_apply_fetched_catalog_keeps_mlflow_when_backend_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt.cyt_mcp.catalog_disk import read_disk_catalog

    monkeypatch.setattr(
        "cyt.cyt_mcp.catalog_disk.cyt_mcp_catalog_cache_dir",
        lambda: tmp_path,
    )
    clear_cyt_mcp_catalog_cache()

    config: dict[str, Any] = {
        "pruning": {
            "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
            "tools": {
                "enabled": True,
                "hook": {"tools_from": ["cyt_mcp"], "cyt_mcp": {"agent": "cursor"}},
            },
        },
    }
    full_catalog = [
        _tool("mlflow-mcp_search_traces", server_key="mlflow-mcp"),
        _tool("atlassian-jira-dc_jira_searchIssues", server_key="atlassian-jira-dc"),
    ]
    apply_fetched_catalog(config, full_catalog)

    partial_live = [
        _tool("atlassian-jira-dc_jira_searchIssues", server_key="atlassian-jira-dc"),
    ]
    apply_fetched_catalog(config, partial_live, degraded_servers=["mlflow-mcp"])

    from cyt.cyt_mcp.catalog import _cache_key_for_config

    cache_key = _cache_key_for_config(config)
    envelope = read_disk_catalog(cache_key.slug)
    assert envelope is not None
    names = [tool["name"] for tool in envelope["tools"]]
    assert "mlflow-mcp_search_traces" in names
    assert "atlassian-jira-dc_jira_searchIssues" in names
