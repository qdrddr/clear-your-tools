"""Integration tests for tool examples capture, storage, and injection."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import cast
from unittest.mock import patch
from urllib.request import Request

import httpx
import pytest
from httpx import ASGITransport

from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.proxy.reverse import create_app
from cyt.tiers.manager import _managers
from cyt.tool_examples.enrich import enrich_tools_with_examples
from cyt.tool_examples.record import record_tool_examples_capture
from cyt.tool_examples.store import ToolExamplesStore


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    return root


def _examples_config(db_path: Path, workspace: Path) -> dict:
    return set_hook_workspace_in_config(
        {
            "tools": {
                "enabled": True,
                "tiers": {
                    "mode": "shadow",
                    "database": {"path": str(workspace / "tier_state.db")},
                },
                "sequence": ["bm25"],
                "policy": {
                    "system_tool": "prune_optional",
                    "mcp_tool": "prune_all",
                    "minimum_tools": 1,
                    "per_tool": {},
                },
                "examples": {
                    "enabled": True,
                    "database": {"path": str(db_path)},
                    "inject": {
                        "max_per_property": 3,
                        "full_call_examples": True,
                        "max_full_call_examples": 1,
                    },
                },
            },
        },
        workspace,
    )


def test_maintenance_then_enrich_still_serves_examples(project_root: Path, tmp_path: Path) -> None:
    """Retention maintenance must not delete all examples needed for injection."""
    from cyt.tool_examples.maintenance import run_tool_examples_maintenance

    db = tmp_path / "tool_examples.db"
    config = _examples_config(db, project_root)
    tools_examples = dict(config["tools"]["examples"])
    tools_examples["retention"] = {
        "max_per_path": 20,
        "min_per_path": 1,
        "max_captures_per_tool": 50,
        "min_captures_per_tool": 1,
        "max_age_days": 365,
    }
    config["tools"]["examples"] = tools_examples

    schema = {
        "type": "object",
        "properties": {"project": {"type": "string", "description": "Project"}},
    }
    record_tool_examples_capture(
        workspace=project_root,
        mcp_server="codebase-memory-mcp",
        tool_name="search_graph",
        input_schema=schema,
        args={"project": "retained-project"},
        config=config,
    )
    run_tool_examples_maintenance(config)

    tool = {
        "name": "codebase-memory-mcp_search_graph",
        "server_key": "codebase-memory-mcp",
        "tool_name": "search_graph",
        "input_schema": schema,
    }
    enriched = enrich_tools_with_examples([tool], "retained project", config)
    examples = enriched[0]["cyt_injection_examples"]
    assert any(example.get("project") == "retained-project" for example in examples)


def test_capture_to_enrich_pipeline(project_root: Path, tmp_path: Path) -> None:
    """Record a capture, then verify enrichment injects stored examples."""
    db = tmp_path / "tool_examples.db"
    config = _examples_config(db, project_root)
    schema = {
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "Project name"},
            "query": {"type": "string", "description": "Search query"},
        },
    }
    args = {"project": "clear-your-tools", "query": "bm25 ranking"}

    schema_id = record_tool_examples_capture(
        workspace=project_root,
        mcp_server="codebase-memory-mcp",
        tool_name="search_graph",
        input_schema=schema,
        args=args,
        config=config,
    )
    assert schema_id is not None

    tool = {
        "name": "codebase-memory-mcp_search_graph",
        "server_key": "codebase-memory-mcp",
        "tool_name": "search_graph",
        "description": "Search graph",
        "input_schema": schema,
        "cyt_catalog_source": "cyt_mcp",
    }
    enriched = enrich_tools_with_examples([tool], "bm25 ranking project", config)
    examples = enriched[0]["cyt_injection_examples"]
    assert any(example.get("project") == "clear-your-tools" for example in examples)
    assert enriched[0]["input_schema"]["properties"]["project"]["description"] == "Project name"


@pytest.mark.asyncio
async def test_asgi_record_endpoint_round_trip(project_root: Path, tmp_path: Path) -> None:
    """POST /hook/tool-examples/record persists capture via the ASGI app."""
    db = tmp_path / "tool_examples.db"
    config = _examples_config(db, project_root)
    app = create_app(routes={}, config=config)
    transport = ASGITransport(app=app)
    schema = {"type": "object", "properties": {"repo_path": {"type": "string"}}}
    args = {"repo_path": str(project_root)}

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.post(
            "/hook/tool-examples/record",
            json={
                "workspace_root": str(project_root),
                "mcp_server": "codebase-memory",
                "tool_name": "index_repository",
                "input_schema": schema,
                "args": args,
            },
        )
    assert response.status_code == 204

    resolved_root = str(project_root)
    store = ToolExamplesStore.open(str(db))
    try:
        project_id = store.get_or_create_project(resolved_root)
        captures = store.list_captures(project_id, "codebase-memory", "index_repository")
        assert len(captures) == 1
        assert captures[0].input_json == args
    finally:
        store.close()


def test_filter_tools_injects_examples(project_root: Path, tmp_path: Path) -> None:
    """filter_tools_for_query enriches pruned tools when examples are enabled."""
    from cyt.pruners.tools_filter import filter_tools_for_query

    db = tmp_path / "tool_examples.db"
    config = _examples_config(db, project_root)
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
        },
    }
    record_tool_examples_capture(
        workspace=project_root,
        mcp_server="demo-server",
        tool_name="search",
        input_schema=schema,
        args={"query": "cached-query-value"},
        config=config,
    )

    tool = {
        "name": "demo-server_search",
        "server_key": "demo-server",
        "tool_name": "search",
        "description": "Demo search",
        "input_schema": schema,
        "cyt_catalog_source": "cyt_mcp",
    }

    with patch("cyt.pruners.tools_filter._run_catalog_pruning") as prune_mock:
        prune_mock.return_value = (
            [{"name": "demo-server_search", "input_schema": schema, "description": "Demo search"}],
            {},
            {},
            None,
            {},
            {},
            1,
            1,
        )
        result = filter_tools_for_query(
            [tool],
            "cached query search",
            ["bm25"],
            config=config,
            for_hook=True,
        )

    assert result.tools is not None
    assert len(result.tools) == 1
    examples = result.tools[0]["cyt_injection_examples"]
    assert any(example.get("query") == "cached-query-value" for example in examples)


def test_client_notify_record_enrich_pipeline(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cyt-client notify → daemon record → enrich uses persisted examples."""
    db = tmp_path / "tool_examples.db"
    config = _examples_config(db, project_root)

    log_path = tmp_path / "session.jsonl"
    schema = {"type": "object", "properties": {"project": {"type": "string"}}}
    log_path.write_text(
        json.dumps(
            {
                "kind": "session_state",
                "key": "session_state:inject",
                "tools_inject_enabled": True,
            },
        )
        + "\n"
        + json.dumps(
            {
                "kind": "tool_catalog",
                "key": "tool_catalog:cyt_mcp",
                "catalog": "cyt_mcp",
                "hash": "test-hash",
                "tools": [
                    {
                        "name": "codebase-memory-mcp_search_graph",
                        "server_key": "codebase-memory-mcp",
                        "tool_name": "search_graph",
                        "input_schema": schema,
                    },
                ],
            },
        )
        + "\n",
        encoding="utf-8",
    )

    captured_urls: list[str] = []

    class FakeResponse:
        def read(self) -> bytes:
            return b""

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(request: Request, timeout: int = 0) -> FakeResponse:
        captured_urls.append(request.full_url)
        body = json.loads(cast(bytes, request.data or b"").decode())
        schema_id = record_tool_examples_capture(
            workspace=Path(str(body["workspace_root"])),
            mcp_server=str(body["mcp_server"]),
            tool_name=str(body["tool_name"]),
            input_schema=body["input_schema"],
            args=body["args"],
            config=config,
        )
        assert schema_id is not None
        return FakeResponse()

    payload = {
        "hook_event_name": "postToolUse",
        "workspace_roots": [str(project_root)],
        "tool_name": "MCP:codebase-memory-mcp_search_graph",
        "tool_input": {"project": "integration-project"},
        "tool_output": json.dumps({"ok": True}),
    }

    monkeypatch.setattr("cyt_client.tool_gate.session_log_path", lambda _payload: log_path)
    with patch(
        "cyt_client.tool_examples_capture.resolve_hook_url",
        return_value="http://127.0.0.1/hook/connect",
    ):
        with patch("cyt_client.tool_examples_capture.urlopen", side_effect=fake_urlopen):
            from cyt_client.tool_examples_capture import notify_tool_examples_capture

            notify_tool_examples_capture(payload)

    assert captured_urls == ["http://127.0.0.1/hook/tool-examples/record"]

    tool = {
        "name": "codebase-memory-mcp_search_graph",
        "server_key": "codebase-memory-mcp",
        "tool_name": "search_graph",
        "input_schema": schema,
        "description": "Search",
    }
    enriched = enrich_tools_with_examples([tool], "integration project", config)
    examples = enriched[0]["cyt_injection_examples"]
    assert any(example.get("project") == "integration-project" for example in examples)


def _ranking_config(db_path: Path, workspace: Path) -> dict:
    return set_hook_workspace_in_config(
        {
            "tools": {
                "sequence": ["bm25"],
                "examples": {
                    "enabled": True,
                    "database": {"path": str(db_path)},
                    "inject": {
                        "max_full_call_examples": 3,
                        "ranking": {
                            "pipeline": ["bm25"],
                            "diversity_threshold": 0.6,
                        },
                    },
                },
            },
        },
        workspace,
    )


def test_repeated_captures_boost_ranking_in_enrich_pipeline(
    project_root: Path,
    tmp_path: Path,
) -> None:
    """Repeated successful captures should rank higher than one-off values."""
    db = tmp_path / "tool_examples.db"
    config = _ranking_config(db, project_root)
    schema = {
        "type": "object",
        "properties": {"repo": {"type": "string", "description": "Repository"}},
    }
    record_tool_examples_capture(
        workspace=project_root,
        mcp_server="demo",
        tool_name="search",
        input_schema=schema,
        args={"repo": "rare-repo"},
        config=config,
    )
    for _ in range(6):
        record_tool_examples_capture(
            workspace=project_root,
            mcp_server="demo",
            tool_name="search",
            input_schema=schema,
            args={"repo": "popular-repo"},
            config=config,
        )

    tool = {
        "name": "demo_search",
        "server_key": "demo",
        "tool_name": "search",
        "input_schema": schema,
    }
    enriched = enrich_tools_with_examples([tool], "search repository demo", config)
    repos = [example["repo"] for example in enriched[0]["cyt_injection_examples"]]
    assert repos[0] == "popular-repo"


def test_retention_keeps_high_usage_values_for_enrich(
    project_root: Path,
    tmp_path: Path,
) -> None:
    """Path retention should evict low-usage examples before high-usage ones."""
    from cyt.tool_examples.maintenance import run_tool_examples_maintenance

    db = tmp_path / "tool_examples.db"
    config = _ranking_config(db, project_root)
    tools_examples = dict(config["tools"]["examples"])
    tools_examples["retention"] = {
        "max_per_path": 3,
        "min_per_path": 2,
        "max_captures_per_tool": 50,
        "min_captures_per_tool": 1,
        "max_age_days": 365,
    }
    config["tools"]["examples"] = tools_examples
    schema = {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City"}},
    }
    store = ToolExamplesStore.open(str(db))
    try:
        project_id = store.get_or_create_project(str(project_root))
        for value, repeat in (
            ("New York, NY", 5),
            ("San Francisco, CA", 4),
            ("Chicago, IL", 3),
            ("Chicago", 1),
            ("Chicago, Illinois", 1),
        ):
            for _ in range(repeat):
                store.upsert_capture(project_id, "geo", "lookup", schema, {"city": value})
    finally:
        store.close()

    run_tool_examples_maintenance(config)

    tool = {
        "name": "geo_lookup",
        "server_key": "geo",
        "tool_name": "lookup",
        "input_schema": schema,
    }
    enriched = enrich_tools_with_examples([tool], "city address lookup", config)
    examples = enriched[0]["cyt_injection_examples"]
    assert len(examples) == 3
    cities = [example["city"] for example in examples]
    assert len(set(cities)) == 3


def test_capture_to_enrich_applies_diversity(project_root: Path, tmp_path: Path) -> None:
    """End-to-end capture and enrich should inject distinct successful call payloads."""
    db = tmp_path / "tool_examples.db"
    config = _ranking_config(db, project_root)
    schema = {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name"}},
    }
    captures = (
        ("Chicago, IL", 3),
        ("New York, NY", 5),
        ("San Francisco, CA", 4),
        ("Chicago", 1),
        ("Chicago, Illinois", 1),
    )
    for city, repeat in captures:
        for _ in range(repeat):
            record_tool_examples_capture(
                workspace=project_root,
                mcp_server="geo",
                tool_name="lookup",
                input_schema=schema,
                args={"city": city},
                config=config,
            )

    tool = {
        "name": "geo_lookup",
        "server_key": "geo",
        "tool_name": "lookup",
        "input_schema": schema,
    }
    enriched = enrich_tools_with_examples([tool], "city address lookup", config)
    examples = enriched[0]["cyt_injection_examples"]
    assert len(examples) == 3
    cities = [example["city"] for example in examples]
    assert len(set(cities)) == 3
