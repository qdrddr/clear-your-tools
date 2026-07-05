"""Tests for proxy user-turn injection helpers."""

from __future__ import annotations

from typing import Any

from cyt.proxy.user_message_inject import (
    already_has_user_turn_injection,
    anthropic_append_to_user_turn,
    anthropic_tools_for_user_message_inject,
    combine_injection_parts,
    openai_append_to_user_turn,
    openai_tools_keep_system_only,
    split_tools_for_root_and_inject,
)


def _tool(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"Tool {name}",
        "input_schema": {"type": "object", "properties": {}},
    }


def test_combine_injection_parts_skips_empty() -> None:
    assert combine_injection_parts(["skills", "", "tools"]) == "skills\n\ntools"


def test_split_tools_for_root_and_inject() -> None:
    tools = [_tool("Read"), _tool("mcp__srv__grep")]
    mcp, system = split_tools_for_root_and_inject(tools)
    assert [t["name"] for t in system] == ["Read"]
    assert [t["name"] for t in mcp] == ["mcp__srv__grep"]


def test_anthropic_tools_for_user_message_inject_keeps_original_system() -> None:
    original = [_tool("Read"), _tool("Write"), _tool("mcp__ctx7__query-docs")]
    pruned = [
        _tool("Read"),
        {
            "name": "mcp__ctx7__query-docs",
            "description": "Query docs pruned",
            "input_schema": {
                "type": "object",
                "properties": {"libraryId": {"type": "string"}},
            },
        },
    ]
    mcp, system = anthropic_tools_for_user_message_inject(original, pruned)
    assert [t["name"] for t in system] == ["Read", "Write"]
    assert len(mcp) == 1
    assert mcp[0]["name"] == "mcp__ctx7__query-docs"
    assert mcp[0]["description"] == "Query docs pruned"


def test_anthropic_append_to_user_turn_string_content() -> None:
    body = {
        "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "latest"},
        ],
    }
    out = anthropic_append_to_user_turn(body, "<agent-skills>skills</agent-skills>")
    assert out["messages"][2]["content"] == "latest\n\n<agent-skills>skills</agent-skills>"


def test_anthropic_append_to_user_turn_inserts_when_missing() -> None:
    body: dict[str, object] = {"messages": [{"role": "assistant", "content": "hi"}]}
    out = anthropic_append_to_user_turn(body, "injected")
    assert out["messages"][-1]["role"] == "user"
    assert out["messages"][-1]["content"] == "injected"


def test_anthropic_append_to_user_turn_block_content() -> None:
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
                ],
            },
        ],
    }
    out = anthropic_append_to_user_turn(body, "injected")
    blocks = out["messages"][0]["content"]
    assert blocks[-1] == {"type": "text", "text": "injected"}


def test_openai_append_to_user_turn_appends_input_text() -> None:
    body = {
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "first"}],
            },
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "last"}],
            },
        ],
    }
    out = openai_append_to_user_turn(body, "<agent-tools>tools</agent-tools>")
    last = out["input"][1]["content"]
    assert last[-1]["text"] == "<agent-tools>tools</agent-tools>"


def test_already_has_user_turn_injection_anthropic() -> None:
    body = {"messages": [{"role": "user", "content": "hello <agent-tools>x</agent-tools>"}]}
    assert already_has_user_turn_injection(body, "anthropic") is True


def test_openai_tools_keep_system_only_strips_namespace_mcp() -> None:
    original: list[dict[str, Any]] = [
        {"type": "tool_search", "query": "x"},
        {
            "type": "namespace",
            "name": "mcp__ctx7",
            "tools": [
                {
                    "type": "function",
                    "name": "resolve_library_id",
                    "description": "Resolve",
                    "parameters": {"type": "object", "properties": {}},
                },
            ],
        },
        {
            "type": "function",
            "name": "Read",
            "description": "Read file",
            "parameters": {"type": "object", "properties": {}},
        },
    ]
    pruned = [
        _tool("mcp__ctx7__resolve_library_id"),
        _tool("Read"),
    ]
    kept = openai_tools_keep_system_only(original, pruned)
    assert kept[0]["type"] == "tool_search"
    assert len(kept) == 2
    assert kept[1]["name"] == "Read"
