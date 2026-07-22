"""Tests for MCPC catalog normalization and disk cache."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.mcpc.catalog import (
    _fetch_catalog_from_cli,
    _normalize_tool,
    clear_mcpc_catalog_cache,
    get_mcpc_catalog,
    load_mcpc_catalog_from_disk,
)
from cyt.mcpc.catalog_disk import raw_catalog_content_hash, read_disk_catalog, write_disk_catalog
from cyt.mcpc.session_health import clear_session_health_cache

_CONFIG = {
    "pruning": {
        "inject_via": "hook",
        "tools": {
            "enabled": True,
            "hook": {
                "tools_from": "mcpc",
                "mcpc": {"executable": "mcpc"},
            },
        },
    },
}

_SESSIONS_PAYLOAD = {
    "sessions": [
        {"name": "@ctx7", "status": "live"},
    ],
}

_TOOLS_PAYLOAD = [
    {
        "name": "resolve-library-id",
        "title": "Resolve Context7 Library ID",
        "description": "Resolve library id",
        "inputSchema": {
            "type": "object",
            "properties": {
                "libraryName": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["libraryName", "query"],
        },
    },
]

_SESSION_INFO = {
    "instructions": "Use this server for docs.",
    "serverInfo": {
        "name": "Context7",
        "version": "3.2.3",
        "description": "Context7 documentation server.",
    },
}


def setup_function() -> None:
    clear_mcpc_catalog_cache()
    clear_session_health_cache()


def test_normalize_tool_builds_composite_name() -> None:
    tool = _normalize_tool(
        session_name="@ctx7",
        tool={
            "name": "resolve-library-id",
            "title": "Resolve Context7 Library ID",
            "description": "Resolve library id",
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True},
            "execution": {"taskSupport": "forbidden"},
        },
        server_name="Context7",
        server_instructions="Use docs.",
    )
    assert tool is not None
    assert tool["name"] == "@ctx7/resolve-library-id"
    assert tool["tool_name"] == "resolve-library-id"
    assert tool["annotations"] == {"readOnlyHint": True}
    assert tool["execution"] == {"taskSupport": "forbidden"}
    assert tool["mcpc_session"] == "@ctx7"


def test_normalize_tool_includes_server_description() -> None:
    tool = _normalize_tool(
        session_name="@ctx7",
        tool={
            "name": "resolve-library-id",
            "description": "Resolve library id",
            "inputSchema": {"type": "object", "properties": {}},
        },
        server_name="Context7",
        server_instructions="Use docs.",
        server_description="Context7 documentation server.",
    )
    assert tool is not None
    assert tool["server_description"] == "Context7 documentation server."


def test_fetch_catalog_from_cli_uses_live_sessions_only() -> None:
    def fake_json(executable: str, args: list[str], **_kwargs: object) -> object:
        if args == []:
            return _SESSIONS_PAYLOAD
        if args == ["@ctx7", "tools-list"]:
            return _TOOLS_PAYLOAD
        if args == ["@ctx7"]:
            return _SESSION_INFO
        return None

    with patch("cyt.mcpc.catalog.run_mcpc_json", side_effect=fake_json):
        with patch("cyt.mcpc.session_health.run_mcpc_json", side_effect=fake_json):
            tools, sessions = _fetch_catalog_from_cli("mcpc", "mcpc", config=_CONFIG)
    assert len(tools) == 1
    assert tools[0]["server_name"] == "Context7"
    assert tools[0]["server_description"] == "Context7 documentation server."
    assert "@ctx7" in sessions


def test_write_disk_catalog_skips_when_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cyt.mcpc.catalog_disk._MCPC_CATALOG_CACHE_DIR",
        tmp_path,
    )
    tools = [
        {
            "name": "@ctx7/resolve-library-id",
            "mcpc_session": "@ctx7",
            "description": "Resolve",
            "input_schema": {"type": "object"},
        },
    ]
    content_hash = raw_catalog_content_hash(tools)
    action = write_disk_catalog(
        "mcpc",
        mcpc_executable="mcpc",
        tools=tools,
        content_hash=content_hash,
    )
    assert action == "disk_write_created"
    action2 = write_disk_catalog(
        "mcpc",
        mcpc_executable="mcpc",
        tools=tools,
        content_hash=content_hash,
    )
    assert action2 == "disk_write_skipped"
    envelope = read_disk_catalog("mcpc")
    assert envelope is not None
    assert envelope["tool_count"] == 1


def test_get_mcpc_catalog_memory_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cyt.mcpc.catalog_disk._MCPC_CATALOG_CACHE_DIR", tmp_path)
    tools = [
        {
            "name": "@ctx7/resolve-library-id",
            "tool_name": "resolve-library-id",
            "mcpc_session": "@ctx7",
            "title": "Resolve Context7 Library ID",
            "description": "Resolve",
            "input_schema": {"type": "object", "properties": {}},
            "server_name": "Context7",
            "server_instructions": "Use docs.",
        },
    ]
    write_disk_catalog(
        "mcpc",
        mcpc_executable="mcpc",
        tools=tools,
        content_hash=raw_catalog_content_hash(tools),
    )
    assert load_mcpc_catalog_from_disk(_CONFIG) is True
    with patch("cyt.mcpc.catalog._ensure_scheduler_started"):
        catalog = get_mcpc_catalog(_CONFIG, blocking=False)
    assert catalog is not None
    assert len(catalog) == 1
