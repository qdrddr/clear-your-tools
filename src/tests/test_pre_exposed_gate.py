"""Tests for pre-exposed in-session injection gate."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from cyt.injection.pre_exposed import (
    filter_pre_exposed_skills,
    filter_pre_exposed_tools,
    is_pre_exposed,
)
from cyt.injection.session_text import (
    session_text_from_hook_payload,
    session_text_from_proxy_body,
)
from cyt.skills.inject import format_agent_skills, format_skill_item
from cyt.skills.search import MatchedSkill
from cyt.tools.inject import format_agent_tools, format_tool_item

_SKILL_MARKDOWN = (
    "---\nname: create-hook\ndescription: Agent hooks for sessions.\n---\n\n# Create Hook\n\nBody\n"
)


def _skill_match(*, name: str = "create-hook", doc_id: str = "create-hook") -> MatchedSkill:
    return MatchedSkill(
        doc_id=doc_id,
        file_path=f"/home/user/skills/{doc_id}.md",
        markdown=_SKILL_MARKDOWN,
        name=name,
        score=1.0,
        token_count=10,
    )


def _tool(name: str = "mcp__a__grep") -> dict:
    return {
        "name": name,
        "description": f"Tool {name}",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    }


def test_is_pre_exposed_requires_non_empty_inputs() -> None:
    fragment = format_tool_item(_tool())
    assert not is_pre_exposed(fragment, "")
    assert not is_pre_exposed("", fragment)


def test_filter_pre_exposed_tools_drops_verbatim_fragment() -> None:
    kept_tool = _tool("mcp__a__keep")
    dropped_tool = _tool("mcp__a__drop")
    session_text = format_tool_item(dropped_tool)
    filtered = filter_pre_exposed_tools([dropped_tool, kept_tool], session_text)
    assert [tool["name"] for tool in filtered] == ["mcp__a__keep"]
    injected = format_agent_tools(filtered)
    assert "<agent-tools" in injected
    assert "mcp__a__keep" in injected
    assert "mcp__a__drop" not in injected


def test_filter_pre_exposed_skills_drops_verbatim_fragment() -> None:
    kept = _skill_match(name="keep-skill", doc_id="keep-skill")
    dropped = _skill_match(name="drop-skill", doc_id="drop-skill")
    session_text = format_skill_item(dropped)
    filtered = filter_pre_exposed_skills([dropped, kept], session_text)
    assert [match.name for match in filtered] == ["keep-skill"]
    injected = format_agent_skills(filtered)
    assert "<agent-skills>" in injected
    assert "keep-skill" in injected
    assert "drop-skill" not in injected


def test_filter_pre_exposed_all_dropped_returns_empty_format() -> None:
    tool = _tool()
    session_text = format_tool_item(tool)
    assert format_agent_tools(filter_pre_exposed_tools([tool], session_text)) == ""

    match = _skill_match()
    session_text = format_skill_item(match)
    assert format_agent_skills(filter_pre_exposed_skills([match], session_text)) == ""


def test_filter_pre_exposed_tools_openai_variant_without_description() -> None:
    tool = _tool("mcp__ctx7__query-docs")
    session_text = format_tool_item(tool, include_tool_description=False)
    filtered = filter_pre_exposed_tools(
        [tool],
        session_text,
        include_tool_description=False,
    )
    assert filtered == []


def test_session_text_from_proxy_body_anthropic_includes_prior_user_turn() -> None:
    prior_fragment = format_tool_item(_tool("mcp__a__seen"))
    body = {
        "system": [{"type": "text", "text": "system prompt"}],
        "messages": [
            {"role": "user", "content": f"earlier\n\n{prior_fragment}"},
            {"role": "user", "content": "new prompt"},
            {"role": "assistant", "content": [{"type": "text", "text": "reply"}]},
        ],
    }
    session_text = session_text_from_proxy_body(body, "anthropic")
    filtered = filter_pre_exposed_tools([_tool("mcp__a__seen"), _tool("mcp__a__new")], session_text)
    assert [tool["name"] for tool in filtered] == ["mcp__a__new"]


def test_session_text_from_proxy_body_openai_includes_instructions_and_input() -> None:
    body = {
        "instructions": "You are Codex.",
        "input": [
            {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "developer context"}],
            },
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "find tools"}],
            },
        ],
    }
    session_text = session_text_from_proxy_body(body, "openai")
    assert "You are Codex." in session_text
    assert "developer context" in session_text
    assert "find tools" in session_text


def test_session_text_from_hook_payload_uses_inline_transcript() -> None:
    dropped = _skill_match(name="seen-skill", doc_id="seen-skill")
    fragment = format_skill_item(dropped)
    payload = {
        "cyt_transcript": [
            {
                "role": "user",
                "message": {"content": [{"type": "text", "text": f"plan\n\n{fragment}"}]},
            },
        ],
        "cyt_agent": "cursor",
    }
    session_text = session_text_from_hook_payload(payload, allow_file_read=False)
    filtered = filter_pre_exposed_skills(
        [dropped, _skill_match(name="fresh-skill", doc_id="fresh-skill")],
        session_text,
    )
    assert [match.name for match in filtered] == ["fresh-skill"]


def test_session_text_from_hook_payload_includes_cyt_rules_injection() -> None:
    dropped = _tool("mcp__rules__seen")
    payload = {
        "cyt_rules_injection": format_tool_item(dropped),
    }
    session_text = session_text_from_hook_payload(payload, allow_file_read=False)
    filtered = filter_pre_exposed_tools([dropped, _tool("mcp__rules__fresh")], session_text)
    assert [tool["name"] for tool in filtered] == ["mcp__rules__fresh"]


def test_session_text_from_hook_payload_reads_transcript_file_when_allowed() -> None:
    dropped = _tool("mcp__file__seen")
    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / "session.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "output_text", "text": format_tool_item(dropped)}],
                    },
                },
            ),
            encoding="utf-8",
        )
        payload = {
            "transcript_path": str(transcript),
            "cyt_agent": "codex",
        }
        session_text = session_text_from_hook_payload(payload, allow_file_read=True)
        filtered = filter_pre_exposed_tools([dropped, _tool("mcp__file__fresh")], session_text)
        assert [tool["name"] for tool in filtered] == ["mcp__file__fresh"]
