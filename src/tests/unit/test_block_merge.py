"""Tests for proxy injection block merge helpers."""

from __future__ import annotations

from cyt.injection.block_merge import merge_injection_into_text, strip_agent_skills_blocks
from cyt.proxy.user_message_inject import (
    anthropic_append_to_user_turn,
    prepare_agent_skills_inject_body,
)


def test_strip_agent_skills_blocks_removes_wrapper() -> None:
    text = (
        "prefix\n\n"
        "<agent-skills>\n"
        '<skill name="demo" path="/a/demo.md">\nbody\n</skill>\n'
        "</agent-skills>"
    )
    assert strip_agent_skills_blocks(text) == "prefix"


def test_prepare_agent_skills_inject_body_strips_on_next_turn() -> None:
    body = {
        "messages": [
            {
                "role": "user",
                "content": "first\n\n<agent-skills><skill name='a'>x</skill></agent-skills>",
            },
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "second"},
        ],
    }
    prepared, same_turn = prepare_agent_skills_inject_body(
        body,
        kind="anthropic",
        use_user_turn=True,
    )
    assert same_turn is False
    assert "<agent-skills>" not in prepared["messages"][0]["content"]
    assert "<agent-skills>" not in prepared["messages"][2]["content"]


def test_merge_agent_skills_blocks_adds_new_skill_item() -> None:
    from cyt.injection.block_merge import merge_agent_skills_blocks

    prior = (
        "Based on the user query added chunks of descriptions of skills (not entire skill). "
        "The entire skill could be retrieved with the file path, though in most cases it likely "
        "excessive.\n\n"
        "<agent-skills>\n"
        '<skill name="old-skill" path="/a/old.md">\nold body\n</skill>\n'
        "</agent-skills>"
    )
    delta = (
        "Based on the user query added chunks of descriptions of skills (not entire skill). "
        "The entire skill could be retrieved with the file path, though in most cases it likely "
        "excessive.\n\n"
        "<agent-skills>\n"
        '<skill name="new-skill" path="/a/new.md">\nnew body\n</skill>\n'
        "</agent-skills>"
    )
    merged = merge_agent_skills_blocks(prior, delta)
    assert merged.count("<agent-skills>") == 1
    assert 'name="old-skill"' in merged
    assert 'name="new-skill"' in merged


def test_merge_injection_into_text_merges_skills_on_same_turn() -> None:
    existing = (
        "user query\n\n"
        "<agent-skills>\n"
        '<skill name="old-skill" path="/a/old.md">\nold body\n</skill>\n'
        "</agent-skills>"
    )
    delta = (
        "Based on the user query added chunks of descriptions of skills (not entire skill). "
        "The entire skill could be retrieved with the file path, though in most cases it likely "
        "excessive.\n\n"
        "<agent-skills>\n"
        '<skill name="new-skill" path="/a/new.md">\nnew body\n</skill>\n'
        "</agent-skills>"
    )
    merged = merge_injection_into_text(existing, delta)
    assert merged.count("<agent-skills>") == 1
    assert 'name="old-skill"' in merged
    assert 'name="new-skill"' in merged


def test_merge_injection_into_text_appends_tools_after_skills() -> None:
    existing = (
        "user query\n\n"
        "<agent-skills>\n"
        '<skill name="demo" path="/a/demo.md">\nbody\n</skill>\n'
        "</agent-skills>"
    )
    delta = "\n<agent-tools description='demo'>\n<tool name='mcp__a__b'>{'input_schema':{}}</tool>\n</agent-tools>"
    merged = merge_injection_into_text(existing, delta)
    assert merged.count("<agent-skills>") == 1
    assert "<agent-tools" in merged


def test_merge_injection_into_text_replaces_skills_on_next_turn() -> None:
    existing = (
        "user query\n\n"
        "<agent-skills>\n"
        '<skill name="old-skill" path="/a/old.md">\nold body\n</skill>\n'
        "</agent-skills>"
    )
    delta = (
        "<agent-skills>\n"
        '<skill name="new-skill" path="/a/new.md">\nnew body\n</skill>\n'
        "</agent-skills>"
    )
    merged = merge_injection_into_text(existing, delta, same_turn=False)
    assert merged.count("<agent-skills>") == 1
    assert 'name="old-skill"' not in merged
    assert 'name="new-skill"' in merged


def test_anthropic_append_to_user_turn_merges_duplicate_skills_block() -> None:
    body = {
        "messages": [
            {
                "role": "user",
                "content": (
                    "latest\n\n"
                    "<agent-skills>\n"
                    '<skill name="old-skill" path="/a/old.md">\nold body\n</skill>\n'
                    "</agent-skills>"
                ),
            },
        ],
    }
    delta = (
        "<agent-skills>\n"
        '<skill name="new-skill" path="/a/new.md">\nnew body\n</skill>\n'
        "</agent-skills>"
    )
    out = anthropic_append_to_user_turn(body, delta)
    content = out["messages"][0]["content"]
    assert isinstance(content, str)
    assert content.count("<agent-skills>") == 1
    assert 'name="old-skill"' in content
    assert 'name="new-skill"' in content
