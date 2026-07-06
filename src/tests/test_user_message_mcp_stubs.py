"""Tests for minimal MCP tool stubs in proxy user-message inject mode."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from cyt.proxy.anthropic import PruneResult, transform_anthropic_request
from cyt.proxy.user_message_inject import (
    anthropic_mcp_tool_stub,
    anthropic_root_tools_with_mcp_stubs,
    openai_append_mcp_stubs,
    openai_mcp_namespace_stub,
)


def test_anthropic_mcp_tool_stub_strips_description_and_schema() -> None:
    tool = {
        "name": "mcp__context7__resolve-library-id",
        "description": "Resolve a library ID",
        "input_schema": {
            "type": "object",
            "properties": {
                "libraryName": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["libraryName", "query"],
        },
    }
    stub = anthropic_mcp_tool_stub(tool)
    assert stub == {
        "name": "mcp__context7__resolve-library-id",
        "input_schema": {"type": "object", "properties": {}},
    }
    assert "description" not in stub


def test_anthropic_root_tools_with_mcp_stubs_preserves_order() -> None:
    original = [
        {"name": "Read", "description": "Read", "input_schema": {"type": "object"}},
        {"name": "mcp__a__one", "description": "One", "input_schema": {"type": "object"}},
        {"name": "Write", "description": "Write", "input_schema": {"type": "object"}},
        {"name": "mcp__b__two", "description": "Two", "input_schema": {"type": "object"}},
    ]
    system_tools = [original[0], original[2]]
    out = anthropic_root_tools_with_mcp_stubs(system_tools, original)
    assert [t["name"] for t in out] == ["Read", "Write", "mcp__a__one", "mcp__b__two"]
    assert "description" not in out[2]
    assert out[2]["input_schema"] == {"type": "object", "properties": {}}


def test_openai_mcp_namespace_stub_preserves_structure() -> None:
    namespace: dict[str, Any] = {
        "type": "namespace",
        "name": "mcp__lean_ctx",
        "description": "Use lean-ctx MCP tools",
        "tools": [
            {
                "type": "function",
                "name": "ctx_edit",
                "description": "Edit a file",
                "strict": False,
                "defer_loading": True,
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        ],
    }
    stub = openai_mcp_namespace_stub(namespace)
    assert stub["type"] == "namespace"
    assert stub["name"] == "mcp__lean_ctx"
    assert "description" not in stub
    assert stub["tools"] == [
        {
            "type": "function",
            "name": "ctx_edit",
            "strict": False,
            "defer_loading": True,
            "parameters": {"type": "object", "properties": {}},
        },
    ]


def test_openai_append_mcp_stubs_flat_and_namespace() -> None:
    original: list[dict[str, Any]] = [
        {
            "type": "function",
            "name": "Read",
            "description": "Read",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "type": "function",
            "name": "mcp__a__grep",
            "description": "Grep",
            "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}},
        },
        {
            "type": "namespace",
            "name": "mcp__context7",
            "description": "Context7",
            "tools": [
                {
                    "type": "function",
                    "name": "query_docs",
                    "description": "Query docs",
                    "strict": False,
                    "parameters": {"type": "object", "properties": {}},
                },
            ],
        },
    ]
    system_only: list[dict[str, Any]] = [original[0]]
    out = openai_append_mcp_stubs(system_only, original)
    assert out[0]["name"] == "Read"
    assert out[1]["name"] == "mcp__a__grep"
    assert out[1]["parameters"] == {"type": "object", "properties": {}}
    assert out[2]["type"] == "namespace"
    assert out[2]["tools"][0]["name"] == "query_docs"


def test_transform_anthropic_inject_keeps_mcp_stubs_not_inject_via_hook() -> None:
    config = {"pruning": {"inject_into_user_message": True, "inject_via": "proxy"}}
    body = {
        "model": "claude-test",
        "messages": [{"role": "user", "content": "use context7 docs"}],
        "tools": [
            {"name": "Read", "description": "Read", "input_schema": {"type": "object"}},
            {"name": "mcp__ctx7__query-docs", "description": "Docs", "input_schema": {}},
        ],
    }
    pruned_tools = [
        {
            "name": "mcp__ctx7__query-docs",
            "description": "Docs pruned",
            "input_schema": {"type": "object", "properties": {"libraryId": {"type": "string"}}},
        },
    ]
    prune_result = PruneResult(
        tools=pruned_tools,
        status="applied",
        query="User_Asks: use context7 docs",
        tools_in=2,
        mcp_tools_in=1,
        tools_out=1,
        error=None,
    )
    with patch("cyt.pruning.coordinator.filter_tools_for_query", return_value=prune_result):
        out, _, _ = transform_anthropic_request(body, config=config)

    assert [t["name"] for t in out["tools"]] == ["Read", "mcp__ctx7__query-docs"]
    mcp_stub = out["tools"][1]
    assert "description" not in mcp_stub
    assert mcp_stub["input_schema"] == {"type": "object", "properties": {}}
    user_text = out["messages"][-1]["content"]
    assert "<agent-tools" in user_text
    assert "Docs pruned" in user_text


def test_transform_anthropic_inject_false_has_no_stubs() -> None:
    config = {"pruning": {"inject_into_user_message": False, "inject_via": "proxy"}}
    body = {
        "model": "claude-test",
        "messages": [{"role": "user", "content": "grep files"}],
        "tools": [{"name": "mcp__a__grep", "input_schema": {}}],
    }
    pruned_tools = [{"name": "mcp__a__grep", "description": "Grep", "input_schema": {}}]
    prune_result = PruneResult(
        tools=pruned_tools,
        status="applied",
        query="User_Asks: grep files",
        tools_in=1,
        mcp_tools_in=1,
        tools_out=1,
        error=None,
    )
    with patch("cyt.pruning.coordinator.filter_tools_for_query", return_value=prune_result):
        out, _, _ = transform_anthropic_request(body, config=config)

    assert out["tools"] == pruned_tools
    user_text = out["messages"][-1]["content"]
    assert "<agent-tools" not in user_text
