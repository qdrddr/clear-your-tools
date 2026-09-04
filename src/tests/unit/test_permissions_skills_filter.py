"""Tests for skills permission matching and filtering."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt.permissions.editor import disable_skill, enable_skill
from cyt.permissions.inventory.skills import skill_policy_name_from_path
from cyt.permissions.match import (
    format_skill_path_permission_entry,
    is_skill_name_denied,
    is_skill_path_denied,
    is_skill_permission_denied,
    parse_skill_permission_entry,
)
from cyt.permissions.runtime import filter_matched_skills_by_permissions, filter_skill_entries
from cyt.skills.catalog import SkillEntryRef
from cyt.skills.search import MatchedSkill


def test_skill_policy_name_prefers_frontmatter(tmp_path: Path) -> None:
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\nname: frontmatter-name\ndescription: demo\n---\n# Body\n",
        encoding="utf-8",
    )
    name, from_frontmatter = skill_policy_name_from_path(skill_md)
    assert name == "frontmatter-name"
    assert from_frontmatter is True


def test_skill_policy_name_falls_back_to_directory(tmp_path: Path) -> None:
    skill_dir = tmp_path / "dir-name"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("# No frontmatter\n", encoding="utf-8")
    name, from_frontmatter = skill_policy_name_from_path(skill_md)
    assert name == "dir-name"
    assert from_frontmatter is False


def test_is_skill_name_denied_is_case_insensitive() -> None:
    assert is_skill_name_denied("Noisy-Skill", ("noisy-skill",))


def test_path_rules_do_not_match_skill_names() -> None:
    assert not is_skill_name_denied("upgrade-guide", ("path:.cursor/skills/upgrade-guide",))
    assert is_skill_path_denied(
        "/tmp/.cursor/skills/upgrade-guide/SKILL.md",
        ("path:.cursor/skills/upgrade-guide",),
        base=Path("/tmp"),
    )


def test_skill_name_does_not_match_path_rule_with_same_text(tmp_path: Path) -> None:
    skill_dir = tmp_path / "shared-label"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("---\nname: other-name\n---\n", encoding="utf-8")
    deny = (format_skill_path_permission_entry(skill_dir),)
    assert not is_skill_name_denied("shared-label", deny)
    assert is_skill_permission_denied(
        skill_name="other-name",
        skill_path=skill_md,
        deny_entries=deny,
        base=tmp_path,
    )


def test_parse_skill_permission_entry_distinguishes_name_and_path() -> None:
    name_rule = parse_skill_permission_entry("my-skill")
    path_rule = parse_skill_permission_entry("path:my-skill")
    assert name_rule is not None and name_rule.kind == "name"
    assert path_rule is not None and path_rule.kind == "path"
    assert path_rule.value == "my-skill"


def test_permission_lists_normalize_yaml_path_object_form() -> None:
    from cyt.permissions.schema import PermissionLists

    parsed = PermissionLists.from_raw(
        {"deny": [{"path": "~/.codex/skills/.system"}, "upgrade-guide"], "allow": []},
    )
    assert parsed.deny == ("path:~/.codex/skills/.system", "upgrade-guide")


def test_permission_lists_normalize_yaml_name_object_form() -> None:
    from cyt.permissions.schema import PermissionLists

    parsed = PermissionLists.from_raw({"deny": [{"name": "upgrade-guide"}], "allow": []})
    assert parsed.deny == ("upgrade-guide",)


def test_codex_system_skills_denied_with_yaml_path_object_form(tmp_path: Path) -> None:
    from cyt.permissions.match import is_skill_path_denied
    from cyt.permissions.merge import effective_permissions

    system_root = tmp_path / ".codex" / "skills" / ".system"
    system_skill = system_root / "imagegen" / "SKILL.md"
    system_skill.parent.mkdir(parents=True)
    system_skill.write_text("---\nname: imagegen\n---\n", encoding="utf-8")

    config = {
        "skills": {
            "permissions": {"deny": [{"path": str(system_root)}]},
        },
    }
    effective = effective_permissions(
        agent="all",
        global_config=config,
        workspace_config={},
    )
    assert effective.skills.deny == (f"path:{system_root}",)
    assert is_skill_path_denied(system_skill, effective.skills.deny, base=tmp_path)


def test_filter_matched_skills_by_permissions() -> None:
    matches = [
        MatchedSkill(
            doc_id="a",
            file_path="/tmp/a/SKILL.md",
            markdown="",
            name="allowed-skill",
            score=1.0,
            token_count=1,
        ),
        MatchedSkill(
            doc_id="b",
            file_path="/tmp/b/SKILL.md",
            markdown="",
            name="blocked-skill",
            score=1.0,
            token_count=1,
        ),
    ]
    filtered = filter_matched_skills_by_permissions(matches, ("blocked-skill",))
    assert [match.name for match in filtered] == ["allowed-skill"]


def test_filter_matched_skills_by_path_rule(tmp_path: Path) -> None:
    allowed_dir = tmp_path / "allowed"
    blocked_dir = tmp_path / "blocked"
    allowed_dir.mkdir()
    blocked_dir.mkdir()
    allowed_md = allowed_dir / "SKILL.md"
    blocked_md = blocked_dir / "SKILL.md"
    allowed_md.write_text("---\nname: allowed-skill\n---\n", encoding="utf-8")
    blocked_md.write_text("---\nname: allowed-skill\n---\n", encoding="utf-8")

    matches = [
        MatchedSkill(
            doc_id="a",
            file_path=str(allowed_md),
            markdown="",
            name="allowed-skill",
            score=1.0,
            token_count=1,
        ),
        MatchedSkill(
            doc_id="b",
            file_path=str(blocked_md),
            markdown="",
            name="allowed-skill",
            score=1.0,
            token_count=1,
        ),
    ]
    filtered = filter_matched_skills_by_permissions(
        matches,
        (format_skill_path_permission_entry(blocked_dir),),
        base=tmp_path,
    )
    assert [match.file_path for match in filtered] == [str(allowed_md)]


def test_filter_skill_entries_honors_path_rules(tmp_path: Path) -> None:
    skill_dir = tmp_path / "blocked"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("---\nname: demo\n---\n", encoding="utf-8")
    entry = SkillEntryRef(
        source_path=str(skill_md),
        doc_id="blocked",
        content_sha256="abc",
        cache_key="abc",
        entry_dir=str(skill_dir),
        nodes_dir=str(skill_dir / "nodes"),
        chunk_dir=str(skill_dir / "chunks"),
        bm25_chunk_dir=str(skill_dir / "chunks"),
        pipeline="bm25",
        index_params_hash="hash",
        disk_backed=True,
        document={"structure": []},
    )
    filtered = filter_skill_entries(
        [entry],
        (format_skill_path_permission_entry(skill_dir),),
        base=tmp_path,
    )
    assert filtered == []


def test_disable_and_enable_skill_by_path(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "upgrade-guide"
    skill_dir.mkdir(parents=True)

    disable_skill(
        "",
        scope="workspace",
        agent_target="all",
        agent="all",
        workspace_root=tmp_path,
        skill_path=skill_dir,
    )
    config_path = tmp_path / ".agents" / "cyt" / "config" / "config.yaml"
    raw = config_path.read_text(encoding="utf-8")
    assert "path:skills/upgrade-guide" in raw

    enable_skill(
        "",
        scope="workspace",
        agent_target="all",
        agent="all",
        workspace_root=tmp_path,
        skill_path=skill_dir,
    )
    raw_after = config_path.read_text(encoding="utf-8")
    assert "upgrade-guide" not in raw_after


def test_cli_rejects_skill_name_and_path_together() -> None:
    from cyt.permissions.cli import _skills_handler

    handler = _skills_handler("disable")
    with pytest.raises(SystemExit, match="not both"):
        handler(
            __import__("argparse").Namespace(
                skill_name="demo",
                path=Path("/tmp/demo"),
                scope="workspace",
                agent="all",
                json=False,
                config=None,
                workspace=None,
            ),
        )
