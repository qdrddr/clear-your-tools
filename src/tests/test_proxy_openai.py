"""Tests for OpenAI Responses API proxy request transform."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from cyt.proxy.anthropic import PruneResult, format_search_query, merge_api_tool_onto_original
from cyt.proxy.openai_responses import (
    _flatten_openai_tools_for_pruning,
    _merge_openai_tools_preserving_order,
    _merge_pruned_openai_tools,
    _merged_tools_to_openai,
    _openai_tool_pass_through,
    clean_input,
    extract_user_query_from_input,
    transform_openai_request,
)


def _user_message(*texts: str) -> dict:
    return {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": text} for text in texts],
    }


def _assistant_message(text: str) -> dict:
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
        "phase": "final_answer",
    }


def _developer_message(text: str) -> dict:
    return {
        "type": "message",
        "role": "developer",
        "content": [{"type": "input_text", "text": text}],
    }


def test_clean_input_drops_system_reminder_blocks() -> None:
    input_items = [
        _user_message("<system-reminder>\nnoise\n</system-reminder>", "real task"),
    ]
    cleaned = clean_input(input_items)
    assert len(cleaned) == 1
    assert cleaned[0]["content"] == "real task"


def test_extract_user_query_from_openai_input_finds_last_user_message() -> None:
    input_items = [
        _developer_message("permissions and skills"),
        _user_message("# AGENTS.md instructions", "<environment_context>"),
        _developer_message("shell hook reminder"),
        _user_message("say hi!"),
    ]
    cleaned = clean_input(input_items)
    assert extract_user_query_from_input(cleaned) == "say hi!"


def test_transform_openai_request_search_query_includes_assistant_reply() -> None:
    tools: list[dict[str, Any]] = [
        {
            "type": "function",
            "name": "mcp__srv__tool_a",
            "description": "A",
            "parameters": {"type": "object", "properties": {}},
        },
    ]
    body = {
        "model": "gpt-test",
        "input": [_user_message("say hi!"), _assistant_message("hi!")],
        "tools": tools,
    }
    prune_result = PruneResult(
        tools=tools,
        status="applied",
        query="unused",
        tools_in=1,
        mcp_tools_in=1,
        tools_out=1,
        error=None,
    )

    with patch(
        "cyt.proxy.openai_responses.filter_tools_for_query",
        return_value=prune_result,
    ) as mock_filter:
        transform_openai_request(body)

    assert mock_filter.call_args.args[1] == format_search_query("say hi!", "hi!")


def test_extract_user_query_from_input_uses_latest_user_turn() -> None:
    input_items = [
        _user_message("update src/retrieve_catalog.py with score filtering"),
        _user_message(
            "The user stepped away and is coming back. "
            "Recap in under 40 words, 1-2 plain sentences.",
        ),
    ]
    cleaned = clean_input(input_items)
    assert (
        extract_user_query_from_input(cleaned)
        == "The user stepped away and is coming back. Recap in under 40 words, 1-2 plain sentences."
    )


def test_merge_api_tool_onto_original_preserves_openai_root_keys() -> None:
    original = {
        "type": "custom",
        "name": "apply_patch",
        "description": "FREEFORM patch tool",
        "format": {
            "type": "grammar",
            "syntax": "lark",
            "definition": "start: patch",
        },
    }
    api_tool = _merged_tools_to_openai(
        [{"name": "apply_patch", "description": "FREEFORM patch tool", "inputSchema": {}}],
    )[0]
    merged = merge_api_tool_onto_original(original, api_tool)
    assert merged["type"] == "custom"
    assert merged["format"] == original["format"]
    assert "parameters" not in merged
    assert merged["name"] == "apply_patch"


def test_merge_api_tool_onto_original_updates_existing_schema_only() -> None:
    original = {
        "type": "function",
        "name": "mcp__srv__tool_a",
        "description": "A",
        "strict": False,
        "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
    }
    api_tool = {
        "type": "function",
        "name": "mcp__srv__tool_a",
        "description": "A pruned",
        "parameters": {"type": "object", "properties": {}},
    }
    merged = merge_api_tool_onto_original(original, api_tool)
    assert merged["strict"] is False
    assert merged["description"] == "A pruned"
    assert merged["parameters"] == {"type": "object", "properties": {}}


def test_openai_tool_pass_through_matches_unnamed_native_tools() -> None:
    tool_search = {
        "type": "tool_search",
        "execution": "client",
        "description": "Search deferred tools",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
    }
    assert _openai_tool_pass_through(tool_search) is True
    assert _openai_tool_pass_through({"type": "function", "name": "grep"}) is False


def test_merge_openai_tools_preserving_order_keeps_unnamed_tools() -> None:
    tool_search = {
        "type": "tool_search",
        "execution": "client",
        "description": "full catalog",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
    }
    original: list[dict[str, Any]] = [
        {"type": "function", "name": "grep", "description": "A", "parameters": {}},
        tool_search,
        {"type": "web_search", "external_web_access": False},
    ]
    pruned_named = [
        {
            "type": "function",
            "name": "grep",
            "description": "A pruned",
            "parameters": {"type": "object", "properties": {}},
        },
    ]
    merged = _merge_openai_tools_preserving_order(original, pruned_named)
    assert merged[0]["description"] == "A pruned"
    assert merged[1] == tool_search
    assert merged[2] == {"type": "web_search", "external_web_access": False}


def test_transform_openai_request_preserves_tool_search_when_pruning() -> None:
    tool_search = {
        "type": "tool_search",
        "execution": "client",
        "description": "full catalog",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
    }
    body = {
        "model": "gpt-test",
        "input": [_user_message("find grep")],
        "tools": [
            {
                "type": "function",
                "name": "mcp__srv__tool_a",
                "description": "A",
                "parameters": {"type": "object", "properties": {}},
            },
            tool_search,
        ],
    }
    pruned_named = [
        {
            "type": "function",
            "name": "mcp__srv__tool_a",
            "description": "A pruned",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
    ]
    prune_result = PruneResult(
        tools=pruned_named,
        status="applied",
        query="find grep",
        tools_in=1,
        mcp_tools_in=1,
        tools_out=1,
        error=None,
    )

    with patch(
        "cyt.proxy.openai_responses.filter_tools_for_query",
        return_value=prune_result,
    ) as mock_filter:
        out, meta, _ = transform_openai_request(body)

    mock_filter.assert_called_once()
    assert mock_filter.call_args.args[0] == [body["tools"][0]]
    assert out["tools"][0]["description"] == "A pruned"
    assert out["tools"][1] == tool_search
    assert meta is not None
    assert meta.status == "applied"
    assert meta.tools_final is not None
    assert meta.tools_final[1] == tool_search
    assert meta.tools_out == 2


def test_transform_openai_request_only_changes_tools() -> None:
    body = {
        "model": "gpt-5.4-mini",
        "instructions": "You are Codex.",
        "input": [
            _developer_message("developer context"),
            _user_message("find tools"),
        ],
        "tools": [
            {
                "type": "function",
                "name": "mcp__srv__tool_a",
                "description": "A",
                "strict": False,
                "parameters": {"type": "object", "properties": {}},
            },
        ],
        "stream": True,
    }
    pruned_tools = [
        {
            "type": "function",
            "name": "mcp__srv__tool_a",
            "description": "A",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
    ]
    prune_result = PruneResult(
        tools=pruned_tools,
        status="applied",
        query="find tools",
        tools_in=1,
        mcp_tools_in=1,
        tools_out=1,
        error=None,
    )

    with patch("cyt.proxy.openai_responses.filter_tools_for_query", return_value=prune_result):
        out, meta, _ = transform_openai_request(body)

    assert out["tools"][0]["strict"] is False
    assert out["tools"][0]["parameters"] == pruned_tools[0]["parameters"]
    assert out["input"] == body["input"]
    assert out["instructions"] == "You are Codex."
    assert out["stream"] is True
    assert out["model"] == "gpt-5.4-mini"
    assert meta is not None
    assert meta.status == "applied"


def test_transform_openai_request_passthrough_when_no_prune() -> None:
    body = {
        "model": "gpt-test",
        "input": [_user_message("hi")],
        "tools": [
            {
                "type": "function",
                "name": "mcp__a__b",
                "parameters": {},
            },
        ],
    }
    failed = PruneResult(
        tools=None,
        status="failed",
        query="hi",
        tools_in=1,
        mcp_tools_in=1,
        tools_out=None,
        error="api error",
    )
    with patch("cyt.proxy.openai_responses.filter_tools_for_query", return_value=failed):
        out, meta, _ = transform_openai_request(body)
    assert out == body
    assert meta is not None
    assert meta.status == "failed"


def _tool_search_output_namespace(*names: str) -> dict[str, Any]:
    return {
        "type": "namespace",
        "name": "mcp__context7",
        "description": "Context7 docs",
        "tools": [
            {
                "type": "function",
                "name": name,
                "description": f"{name} tool",
                "strict": False,
                "parameters": {"type": "object", "properties": {}},
            }
            for name in names
        ],
    }


def test_flatten_openai_tools_for_pruning_expands_namespace_tools() -> None:
    tools = [_tool_search_output_namespace("resolve_library_id", "query_docs")]
    flat = _flatten_openai_tools_for_pruning(tools)
    assert [t["name"] for t in flat] == [
        "mcp__context7__resolve_library_id",
        "mcp__context7__query_docs",
    ]


def test_merge_pruned_openai_tools_rebuilds_namespace_structure() -> None:
    original = [_tool_search_output_namespace("resolve_library_id", "query_docs")]
    pruned_named = [
        {
            "type": "function",
            "name": "mcp__context7__resolve_library_id",
            "description": "resolve pruned",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
    ]
    merged = _merge_pruned_openai_tools(original, pruned_named)
    assert merged[0]["type"] == "namespace"
    assert merged[0]["name"] == "mcp__context7"
    assert [t["name"] for t in merged[0]["tools"]] == ["resolve_library_id"]
    assert merged[0]["tools"][0]["description"] == "resolve pruned"


def test_transform_openai_request_prunes_tool_search_output_in_input() -> None:
    tool_search_output = {
        "call_id": "call_test",
        "type": "tool_search_output",
        "status": "completed",
        "execution": "client",
        "tools": [
            _tool_search_output_namespace("resolve_library_id", "query_docs"),
            {
                "type": "namespace",
                "name": "mcp__semble",
                "description": "Semble search",
                "tools": [
                    {
                        "type": "function",
                        "name": "search",
                        "description": "search tool",
                        "parameters": {"type": "object", "properties": {}},
                    },
                ],
            },
        ],
    }
    body = {
        "model": "gpt-test",
        "input": [_user_message("context7 bm25s repo description"), tool_search_output],
        "tools": [
            {
                "type": "tool_search",
                "execution": "client",
                "description": "Search deferred tools",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        ],
    }
    pruned_named = [
        {
            "type": "function",
            "name": "mcp__context7__resolve_library_id",
            "description": "resolve pruned",
            "parameters": {"type": "object", "properties": {"libraryName": {"type": "string"}}},
        },
    ]
    prune_result = PruneResult(
        tools=pruned_named,
        status="applied",
        query="context7 bm25s repo description",
        tools_in=1,
        mcp_tools_in=1,
        tools_out=1,
        error=None,
    )

    with patch(
        "cyt.proxy.openai_responses.filter_tools_for_query",
        return_value=prune_result,
    ) as mock_filter:
        out, meta, _ = transform_openai_request(body)

    mock_filter.assert_called_once()
    flat_tools = mock_filter.call_args.args[0]
    assert {t["name"] for t in flat_tools} == {
        "mcp__context7__resolve_library_id",
        "mcp__context7__query_docs",
        "mcp__semble__search",
    }
    pruned_output = out["input"][1]
    assert pruned_output["type"] == "tool_search_output"
    assert [ns["name"] for ns in pruned_output["tools"]] == ["mcp__context7"]
    assert [t["name"] for t in pruned_output["tools"][0]["tools"]] == ["resolve_library_id"]
    assert pruned_output["tools"][0]["tools"][0]["description"] == "resolve pruned"
    assert out["tools"][0]["type"] == "tool_search"
    assert meta is not None
    assert meta.status == "applied"


def test_transform_openai_request_prunes_root_and_tool_search_output_separately() -> None:
    body = {
        "model": "gpt-test",
        "input": [
            _user_message("find grep"),
            {
                "type": "tool_search_output",
                "tools": [_tool_search_output_namespace("resolve_library_id")],
            },
        ],
        "tools": [
            {
                "type": "function",
                "name": "mcp__srv__tool_a",
                "description": "A",
                "parameters": {"type": "object", "properties": {}},
            },
        ],
    }
    root_prune = PruneResult(
        tools=[
            {
                "type": "function",
                "name": "mcp__srv__tool_a",
                "description": "A pruned",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            },
        ],
        status="applied",
        query="find grep",
        tools_in=1,
        mcp_tools_in=1,
        tools_out=1,
        error=None,
    )
    input_prune = PruneResult(
        tools=[
            {
                "type": "function",
                "name": "mcp__context7__resolve_library_id",
                "description": "resolve pruned",
                "parameters": {"type": "object", "properties": {}},
            },
        ],
        status="applied",
        query="find grep",
        tools_in=1,
        mcp_tools_in=1,
        tools_out=1,
        error=None,
    )

    with patch(
        "cyt.proxy.openai_responses.filter_tools_for_query",
        side_effect=[root_prune, input_prune],
    ) as mock_filter:
        out, meta, _ = transform_openai_request(body)

    assert mock_filter.call_count == 2
    assert out["tools"][0]["description"] == "A pruned"
    assert out["input"][1]["tools"][0]["tools"][0]["description"] == "resolve pruned"
    assert meta is not None
    assert meta.status == "applied"
    assert meta.tools_in == 2


def test_transform_openai_request_proxy_injects_developer_message(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    catalog_dir = tmp_path / "catalog"
    skills_dir.mkdir()
    (skills_dir / "create-hook.md").write_text(
        "---\nname: create-hook\ndescription: Agent hooks for sessions.\n---\n"
        "# Create Hook\n\nAgent hooks for sessions.\n",
        encoding="utf-8",
    )
    config = {
        "skills": {
            "enabled": True,
            "inject_via": "proxy",
            "pipeline": "bm25",
            "catalog_dir": str(catalog_dir),
            "directories": [str(skills_dir)],
            "max_tokens_per_request": 4000,
            "pageindex": {"enable_bm25_chunking": True},
        },
        "pruning": {"bm25": {"score_skills": 0.0}},
    }
    body = {
        "model": "gpt-test",
        "input": [_user_message("configure agent hooks for sessions")],
    }
    out, _, skills_meta = transform_openai_request(body, config=config)
    assert skills_meta is not None
    assert skills_meta.skills_in > 0
    developers = [item for item in out["input"] if item.get("role") == "developer"]
    assert len(developers) == 1
    assert "<agent-skills>" in developers[0]["content"][0]["text"]


def test_transform_openai_request_hook_mode_leaves_input_unchanged(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    catalog_dir = tmp_path / "catalog"
    skills_dir.mkdir()
    (skills_dir / "create-hook.md").write_text(
        "---\nname: create-hook\ndescription: Agent hooks for sessions.\n---\n"
        "# Create Hook\n\nAgent hooks for sessions.\n",
        encoding="utf-8",
    )
    config = {
        "skills": {
            "enabled": True,
            "inject_via": "hook",
            "pipeline": "bm25",
            "catalog_dir": str(catalog_dir),
            "directories": [str(skills_dir)],
            "max_tokens_per_request": 4000,
            "pageindex": {"enable_bm25_chunking": True},
        },
        "pruning": {"bm25": {"score_skills": 0.0}},
    }
    body = {
        "model": "gpt-test",
        "input": [_user_message("configure agent hooks for sessions")],
    }
    out, _, skills_meta = transform_openai_request(body, config=config)
    assert skills_meta is not None
    assert skills_meta.skills_in == 0
    assert out["input"] == body["input"]
