"""Tests for MCPC session resources match splitting."""

from __future__ import annotations

from cyt.mcpc.session_resources import split_mcpc_resource_matches
from cyt.skills.search import MatchedSkill


def _resource_match() -> MatchedSkill:
    return MatchedSkill(
        doc_id="architecture",
        file_path="mcpc/everything/resources/architecture.md",
        markdown=(
            "---\n"
            "name: architecture.md\n"
            "description: Static document\n"
            "mcpc_kind: resource\n"
            "mcpc_command: mcpc --json @everything resources-read demo://architecture.md\n"
            "---\n\n# Architecture\n"
        ),
        name="architecture.md",
        score=1.0,
        token_count=10,
    )


def _skill_match() -> MatchedSkill:
    return MatchedSkill(
        doc_id="ask-database",
        file_path="mcpc/sk/skills/ask-database.md",
        markdown="---\nname: ask-database\n---\n\nBody\n",
        name="ask-database",
        score=1.0,
        token_count=10,
    )


def test_split_mcpc_resource_matches_separates_kinds() -> None:
    skills, resources = split_mcpc_resource_matches([_skill_match(), _resource_match()])
    assert len(skills) == 1
    assert skills[0].name == "ask-database"
    assert len(resources) == 1
    assert resources[0].command.startswith("mcpc --json")
