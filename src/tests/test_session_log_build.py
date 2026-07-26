"""Round-trip tests for session log builders and formatters."""

from __future__ import annotations

from cyt.injection.session_log_build import (
    build_skill_log_entry,
    build_tool_log_entry,
    format_entry_fragment,
    format_tool_fragment,
    skill_content_hash,
    tool_content_hash,
    tool_definition_content_hash,
    tool_item_key,
    tool_item_legacy_keys,
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


def test_skill_hash_stable_across_pruned_markdown() -> None:
    stable_hash = "1e543789a9a5647edb1bab1b82c0be0cb6b01ec099d5c25b9751c0b6bb83bb2c"  # pragma: allowlist secret
    skinny = MatchedSkill(
        doc_id="colgrep",
        file_path="mcpc/coolgrep-skill/skills/colgrep.md",
        markdown="# skinny chunk only",
        name="colgrep",
        score=1.0,
        token_count=10,
        content_hash=stable_hash,
    )
    full = MatchedSkill(
        doc_id="colgrep",
        file_path="mcpc/coolgrep-skill/skills/colgrep.md",
        markdown="---\nname: colgrep\n---\n\n# full body with extra sections",
        name="colgrep",
        score=1.0,
        token_count=100,
        content_hash=stable_hash,
    )
    assert skill_content_hash(skinny) == stable_hash
    assert skill_content_hash(full) == stable_hash
    assert build_skill_log_entry(skinny, full=False)["hash"] == stable_hash
    assert build_skill_log_entry(full, full=True)["hash"] == stable_hash


def test_tool_hash_stable_for_pruned_schema() -> None:
    full_tool = {
        "name": "@jcodemunch/search_text",
        "tool_name": "search_text",
        "mcpc_session": "@jcodemunch",
        "description": "Full description",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "query": {"type": "string"},
                "is_regex": {"type": "boolean", "default": False},
                "max_results": {"type": "integer", "default": 20},
            },
            "required": ["repo", "query"],
        },
    }
    pruned_tool = {
        **full_tool,
        "input_schema": {
            "properties": {
                "repo": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["repo", "query"],
            "type": "object",
        },
    }
    catalog = [full_tool]
    full_hash = tool_content_hash(full_tool, catalog="mcpc", catalog_tools=catalog)
    pruned_hash = tool_content_hash(pruned_tool, catalog="mcpc", catalog_tools=catalog)
    assert full_hash == pruned_hash
    expected = tool_definition_content_hash(
        {
            "name": "@jcodemunch/search_text",
            "id": "@jcodemunch/search_text",
            "description": "Full description",
            "inputSchema": full_tool["input_schema"],
        },
    )
    assert full_hash == expected
    skinny_entry = build_tool_log_entry(
        pruned_tool,
        catalog="mcpc",
        full=False,
        catalog_tools=catalog,
    )
    full_entry = build_tool_log_entry(
        full_tool,
        catalog="mcpc",
        full=True,
        catalog_tools=catalog,
    )
    assert skinny_entry["hash"] == full_entry["hash"] == full_hash


def test_tool_item_legacy_keys_for_mcpc() -> None:
    tool = {
        "name": "@jcodemunch/search_text",
        "tool_name": "search_text",
        "mcpc_session": "@jcodemunch",
        "cyt_catalog_source": "mcpc",
    }
    assert tool_item_key(tool, catalog="mcpc") == "tool:mcpc:@jcodemunch:search_text"
    assert tool_item_legacy_keys(tool, catalog="mcpc") == (
        "tool:@jcodemunch:search_text",
        "tool:search_text",
    )


def test_tool_item_legacy_keys_for_executor() -> None:
    tool = {"name": "Shell", "cyt_catalog_source": "executor"}
    assert tool_item_key(tool, catalog="executor") == "tool:executor:Shell"
    assert tool_item_legacy_keys(tool, catalog="executor") == ("tool:Shell",)
