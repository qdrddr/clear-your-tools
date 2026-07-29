"""Tests for MCPC session skills snapshot sources."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from cyt.mcpc.session_skills import append_mcpc_session_skill_entries
from cyt.mcpc.skills_cache import clear_mcpc_skills_cache
from cyt.skills.catalog import SkillEntryRef

_CONFIG: dict[str, Any] = {
    "pruning": {
        "inject_via": "hook",
        "tools": {
            "enabled": True,
            "hook": {
                "tools_from": "mcpc",
                "mcpc": {
                    "executable": "mcpc",
                    "skills": {"in_session": {"enabled": True}},
                },
            },
        },
    },
    "skills": {"enabled": True, "directories": []},
}


def setup_function() -> None:
    clear_mcpc_skills_cache()


def test_append_mcpc_session_skill_entries_skips_when_disabled() -> None:
    config: dict[str, Any] = {
        "pruning": {
            "inject_via": "hook",
            "tools": {
                "enabled": True,
                "hook": {
                    "tools_from": "mcpc",
                    "mcpc": {
                        "executable": "mcpc",
                        "skills": {"in_session": {"enabled": False}},
                    },
                },
            },
        },
        "skills": {"enabled": True, "directories": []},
    }
    assert append_mcpc_session_skill_entries([], config) == []


def test_append_mcpc_session_skill_entries_from_snapshot() -> None:
    fake_entry = SkillEntryRef(
        entry_dir="mcpc/sk",
        doc_id="ask-database",
        document={},
        source_path="mcpc/sk/skills/ask-database.md",
        content_sha256="def",
        cache_key="mcpc/sk/ask-database",
        nodes_dir="nodes",
        chunk_dir="chunks",
        bm25_chunk_dir="bm25",
        pipeline="bm25",
        index_params_hash="hash",
        disk_backed=False,
    )
    with patch(
        "cyt.mcpc.session_skills.build_session_skill_registry",
        return_value=[fake_entry],
    ):
        entries = append_mcpc_session_skill_entries([], _CONFIG)
    assert len(entries) == 1
