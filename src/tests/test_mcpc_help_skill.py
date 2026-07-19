"""Tests for MCPC help skill registry append."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from cyt.mcpc.help_skill import append_mcpc_help_skill_entries
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
                    "skills": {"own": {"enabled": True}},
                },
            },
        },
    },
    "skills": {"enabled": True, "directories": []},
}


def setup_function() -> None:
    clear_mcpc_skills_cache()


def test_append_mcpc_help_skill_entries_skips_when_disabled() -> None:
    config: dict[str, Any] = {
        "pruning": {
            "inject_via": "hook",
            "tools": {
                "enabled": True,
                "hook": {
                    "tools_from": "mcpc",
                    "mcpc": {
                        "executable": "mcpc",
                        "skills": {"own": {"enabled": False}},
                    },
                },
            },
        },
        "skills": {"enabled": True, "directories": []},
    }
    entries = append_mcpc_help_skill_entries([], config)
    assert entries == []


def test_append_mcpc_help_skill_entries_from_snapshot() -> None:
    fake_entry = SkillEntryRef(
        entry_dir="mcpc/help",
        doc_id="help",
        document={},
        source_path="mcpc/help/SKILL.md",
        content_sha256="abc",
        cache_key="mcpc/help/help",
        nodes_dir="nodes",
        chunk_dir="chunks",
        bm25_chunk_dir="bm25",
        pipeline="bm25",
        index_params_hash="hash",
        disk_backed=False,
    )
    with patch(
        "cyt.mcpc.help_skill.build_help_skill_registry",
        return_value=[fake_entry],
    ):
        entries = append_mcpc_help_skill_entries([], _CONFIG)
    assert len(entries) == 1
