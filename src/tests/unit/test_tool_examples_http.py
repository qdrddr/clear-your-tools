"""Tests for hook daemon tool examples record endpoint."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cyt.tool_examples.store import ToolExamplesStore


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.fixture
def base_config() -> dict:
    from cyt.config import load_config

    return load_config()


@pytest.mark.asyncio
async def test_hook_tool_examples_record_stores_capture(
    project_root: Path,
    base_config: dict,
) -> None:
    from cyt.hook.http_server import hook_tool_examples_record

    db_path = project_root / "tool_examples.db"
    config = dict(base_config)
    tools = dict(config.get("tools") or {})
    examples = dict(tools.get("examples") or {})
    examples["enabled"] = True
    examples["database"] = {"path": str(db_path)}
    tools["examples"] = examples
    config["tools"] = tools

    schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    args = {"query": "bm25"}
    request = MagicMock()
    request.client = MagicMock(host="127.0.0.1")
    request.body = AsyncMock(
        return_value=json.dumps(
            {
                "workspace_root": str(project_root),
                "mcp_server": "codebase-memory-mcp",
                "tool_name": "search_graph",
                "input_schema": schema,
                "args": args,
            },
        ).encode(),
    )
    request.app = MagicMock()
    request.app.state.cyt_config = config

    with patch("cyt.hook.http_server._is_localhost_request", return_value=True):
        response = await hook_tool_examples_record(request)
    assert response.status_code == 204

    store = ToolExamplesStore.open(str(db_path))
    try:
        project_id = store.get_or_create_project(str(project_root))
        captures = store.list_captures(project_id, "codebase-memory-mcp", "search_graph")
        assert len(captures) == 1
        assert captures[0].input_json == args
    finally:
        store.close()


@pytest.mark.asyncio
async def test_hook_tool_examples_record_rejects_non_localhost() -> None:
    from cyt.hook.http_server import hook_tool_examples_record

    request = MagicMock()
    request.client = MagicMock(host="203.0.113.1")
    response = await hook_tool_examples_record(request)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_hook_tool_examples_record_validates_payload(project_root: Path, base_config: dict) -> None:
    from cyt.hook.http_server import hook_tool_examples_record

    db_path = project_root / "tool_examples.db"
    config = dict(base_config)
    tools = dict(config.get("tools") or {})
    examples = dict(tools.get("examples") or {})
    examples["enabled"] = True
    examples["database"] = {"path": str(db_path)}
    tools["examples"] = examples
    config["tools"] = tools

    request = MagicMock()
    request.client = MagicMock(host="127.0.0.1")
    request.body = AsyncMock(
        return_value=json.dumps({"workspace_root": str(project_root)}).encode(),
    )
    request.app = MagicMock()
    request.app.state.cyt_config = config

    with patch("cyt.hook.http_server._is_localhost_request", return_value=True):
        response = await hook_tool_examples_record(request)
    assert response.status_code == 400
