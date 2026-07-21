"""Round-trip tests for session log builders and formatters."""

from __future__ import annotations

from cyt.injection.session_log_build import (
    build_skill_log_entry,
    build_tool_log_entry,
    format_entry_fragment,
    format_tool_fragment,
)
from cyt.skills.search import MatchedSkill


def test_executor_tool_log_round_trip() -> None:
    tool = {
        "name": "Shell",
        "description": "Run shell",
        "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}},
    }
    original = format_tool_fragment(tool, catalog="executor")
    entry = build_tool_log_entry(tool, catalog="executor", full=False)
    rebuilt = format_entry_fragment(entry)
    assert rebuilt == original


def test_skill_log_round_trip() -> None:
    markdown = "---\nname: demo\n---\n\n# Demo skill\n\nBody text."
    match = MatchedSkill(
        doc_id="demo",
        file_path="/tmp/demo/SKILL.md",
        markdown=markdown,
        name="demo",
        score=1.0,
        token_count=10,
        command=None,
    )
    entry = build_skill_log_entry(match, full=False)
    fragment = format_entry_fragment(entry)
    assert "Body text." in fragment
    assert "<skill" in fragment
