"""Tests for Anthropic proxy request transform."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from cyt.proxy.anthropic import (
    PruneResult,
    clean_messages,
    extract_last_assistant_message,
    extract_user_query,
    format_search_query,
    transform_anthropic_request,
)


def test_clean_messages_drops_system_reminder_and_redacted_thinking() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "<system-reminder>\nnoise\n</system-reminder>"},
                {"type": "text", "text": "real task"},
                {"type": "redacted_thinking", "data": "x"},
            ],
        },
    ]
    cleaned = clean_messages(messages)
    assert len(cleaned) == 1
    assert cleaned[0]["content"] == "real task"


def test_clean_messages_drops_whitespace_only_text() -> None:
    messages = [{"role": "user", "content": [{"type": "text", "text": " "}]}]
    assert clean_messages(messages) == []


def test_clean_messages_keeps_tool_result_string_drops_array() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
                {"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "text"}]},
            ],
        },
    ]
    cleaned = clean_messages(messages)
    assert len(cleaned) == 1
    blocks = cleaned[0]["content"]
    assert len(blocks) == 1
    assert blocks[0]["type"] == "tool_result"


def test_extract_user_query_skips_tool_result_only_user_turn() -> None:
    messages = [
        {
            "role": "user",
            "content": [{"type": "tool_result", "content": "file contents"}],
        },
        {"role": "user", "content": [{"type": "text", "text": "search for cats"}]},
    ]
    cleaned = clean_messages(messages)
    assert extract_user_query(cleaned) == "search for cats"


def test_extract_user_query_prefers_longest_substantive_turn() -> None:
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "short"}]},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "update src/retrieve_catalog.py to filter json scores above 0.5",
                },
            ],
        },
    ]
    cleaned = clean_messages(messages)
    assert extract_user_query(cleaned) == (
        "update src/retrieve_catalog.py to filter json scores above 0.5"
    )


def test_extract_user_query_skips_malformed_retry_turn() -> None:
    messages = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "update src/retrieve_catalog.py"}],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Your tool call was malformed and could not be parsed. Please retry.",
                },
            ],
        },
    ]
    cleaned = clean_messages(messages)
    assert extract_user_query(cleaned) == "update src/retrieve_catalog.py"


def test_format_search_query_without_assistant() -> None:
    assert format_search_query("say hi!") == "User_Asks: say hi!"


def test_format_search_query_with_assistant() -> None:
    assert (
        format_search_query("say hi!", "hi back") == "User_Asks: say hi!; Assistant_Says: hi back"
    )


def test_extract_last_assistant_message_prefers_latest_turn() -> None:
    messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "older"}]},
        {"role": "user", "content": [{"type": "text", "text": "again"}]},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "thinking",
                    "thinking": "**Greeting**\n\nPreparing hi.",
                },
                {"type": "text", "text": "hi!"},
            ],
        },
    ]
    cleaned = clean_messages(messages)
    assert extract_last_assistant_message(cleaned) == "**Greeting**\n\nPreparing hi.\nhi!"


def test_extract_user_query_skips_recap_meta_turn() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "update src/retrieve_catalog.py with score filtering",
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "The user stepped away and is coming back. "
                        "Recap in under 40 words, 1-2 plain sentences."
                    ),
                },
            ],
        },
    ]
    cleaned = clean_messages(messages)
    assert extract_user_query(cleaned) == "update src/retrieve_catalog.py with score filtering"


def test_transform_anthropic_request_only_changes_tools() -> None:
    body = {
        "model": "claude-test",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "<system-reminder>x</system-reminder>"},
                    {"type": "text", "text": "find tools"},
                ],
            },
        ],
        "tools": [
            {
                "name": "mcp__srv__tool_a",
                "description": "A",
                "input_schema": {"type": "object", "properties": {}},
            },
        ],
        "stream": True,
        "metadata": {"k": "v"},
    }
    pruned_tools = [
        {
            "name": "mcp__srv__tool_a",
            "description": "A",
            "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
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
        decomposed={"build_index": 5, "rerank": 4, "llm": 2},
    )

    with patch("cyt.proxy.anthropic.filter_tools_for_query", return_value=prune_result):
        out, meta, _ = transform_anthropic_request(body)

    assert out["tools"] == pruned_tools
    assert out["messages"] == body["messages"]
    assert out["stream"] is True
    assert out["metadata"] == {"k": "v"}
    assert out["model"] == "claude-test"
    assert meta is not None
    assert meta.status == "applied"


def test_prune_result_to_dict_includes_decomposed_catalog_when_set() -> None:
    result = PruneResult(
        tools=None,
        status="applied",
        query="q",
        tools_in=1,
        mcp_tools_in=1,
        tools_out=1,
        error=None,
        decomposed={"build_index": 1},
        decomposed_catalog={"build_index": {"json": [], "md": []}},
    )
    d = result.to_dict()
    assert d["decomposed_catalog"] == {"build_index": {"json": [], "md": []}}
    assert (
        "decomposed_catalog"
        not in PruneResult(
            tools=None,
            status="skipped",
            query=None,
            tools_in=0,
            mcp_tools_in=0,
            tools_out=None,
            error="x",
        ).to_dict()
    )


def test_transform_anthropic_request_passthrough_when_no_prune() -> None:
    body = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "mcp__a__b", "input_schema": {}}],
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
    with patch("cyt.proxy.anthropic.filter_tools_for_query", return_value=failed):
        out, meta, _ = transform_anthropic_request(body)
    assert out == body
    assert meta is not None
    assert meta.status == "failed"


def test_transform_anthropic_request_proxy_appends_to_system(tmp_path: Path) -> None:
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
        "model": "claude-test",
        "messages": [
            {"role": "system", "content": "# MCP Server Instructions"},
            {"role": "user", "content": "configure agent hooks for sessions"},
        ],
    }
    out, _, skills_meta = transform_anthropic_request(body, config=config)
    assert skills_meta is not None
    assert skills_meta.skills_in > 0
    assert "<agent-skills>" in out["messages"][0]["content"]


def test_snapshot_catalog_omits_tools() -> None:
    from cyt.proxy.anthropic import _snapshot_catalog

    data = {"json": [{"id": "a"}], "md": [], "tools": [{"name": "t"}]}
    snap = _snapshot_catalog(data)
    assert "tools" not in snap
    assert snap["json"] == [{"id": "a"}]
    assert data["tools"] == [{"name": "t"}]


def test_input_tools_from_payload_deep_copies_tools() -> None:
    from typing import Any

    from cyt.proxy.reverse import _input_tools_from_payload, _pruning_meta_for_debug

    original: list[dict[str, Any]] = [{"name": "a", "input_schema": {"x": 1}}]
    payload = {"tools": original}
    copied = _input_tools_from_payload(payload)
    assert copied == original
    assert copied is not original
    schema = copied[0]["input_schema"]
    assert isinstance(schema, dict)
    schema["x"] = 2
    original_schema = original[0]["input_schema"]
    assert isinstance(original_schema, dict)
    assert original_schema["x"] == 1

    meta = _pruning_meta_for_debug({"status": "applied"}, copied)
    assert meta is not None
    assert meta["input"]["tools"] == [{"name": "a", "input_schema": {"x": 2}}]
    assert _pruning_meta_for_debug(None, copied) is None


def test_format_removed_chunks_lines_lists_rerank_pruned_paths() -> None:
    from cyt.proxy.pruning_debug import format_removed_chunks_lines

    pruning = {
        "decomposed_catalog": {
            "build_index": {
                "json": [
                    {"file_path": "schemas/decomposed/Keep.json"},
                    {"file_path": "schemas/decomposed/Drop.json"},
                ],
                "md": [],
            },
            "rerank": {
                "json": [
                    {"file_path": "schemas/decomposed/Keep.json"},
                    {"file_path": "schemas/decomposed/Drop.json"},
                ],
                "md": [],
            },
            "rerank_pruned": {
                "json": [{"file_path": "schemas/decomposed/Keep.json"}],
                "md": [],
            },
        },
    }
    lines = format_removed_chunks_lines(pruning)
    text = "\n".join(lines)
    assert "Decomposed removed" in text
    assert "rerank pruned away" in text
    assert "schemas/decomposed/Drop.json" in text
    assert "removed since build_index" in text
