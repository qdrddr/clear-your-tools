"""Tests for workspace-aware skill directory resolution."""

from __future__ import annotations

from pathlib import Path

import yaml

from cyt.config import save_user_config
from cyt.hook.install_scope import CytInstallScope
from cyt.skills.directories import (
    WORKSPACE_SKILLS_DIR,
    ensure_workspace_skills_config,
    merge_skills_directory_lists,
    resolve_skill_directories,
    resolve_skill_directory_path,
)
from tests.support.skills_helpers import isolated_skills_agents_block


def test_resolve_skill_directory_path_joins_relative_to_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    resolved = resolve_skill_directory_path(".agents/skills", workspace)
    assert resolved == (workspace / ".agents/skills").resolve()


def test_resolve_skill_directories_includes_workspace_agents_skills_from_config(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    agents_dir = workspace / ".agents" / "skills"
    agents_dir.mkdir(parents=True)

    config = {
        "skills": {"directories": ["~/.agents/skills", ".agents/skills"]},
        "agents": isolated_skills_agents_block(),
    }
    directories = resolve_skill_directories(config, agent="cursor", workspace_root=workspace)
    joined = {str(path) for path in directories}
    assert str(agents_dir.resolve()) in joined


def test_merge_skills_directory_lists_appends_without_duplicates() -> None:
    merged, changed = merge_skills_directory_lists(
        ["~/.agents/skills"],
        [".agents/skills", "~/.agents/skills"],
    )
    assert changed is True
    assert merged == ["~/.agents/skills", ".agents/skills"]


def test_ensure_workspace_skills_config_writes_workspace_overlay(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    scope = CytInstallScope(workspace_root=workspace)
    config_path = scope.workspace_all_agents_cyt_config_path()
    assert config_path is not None
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("{}\n", encoding="utf-8")

    assert ensure_workspace_skills_config(workspace) is True
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw["skills"]["directories"] == [WORKSPACE_SKILLS_DIR]
    assert (workspace / WORKSPACE_SKILLS_DIR).is_dir()

    assert ensure_workspace_skills_config(workspace) is False


def test_ensure_workspace_skills_config_merges_existing_directories(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    scope = CytInstallScope(workspace_root=workspace)
    config_path = scope.workspace_all_agents_cyt_config_path()
    assert config_path is not None
    save_user_config(
        config_path,
        {"skills": {"directories": [".cursor/skills"]}},
        apply_bundled_sections=False,
    )

    assert ensure_workspace_skills_config(workspace) is True
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert WORKSPACE_SKILLS_DIR in raw["skills"]["directories"]
    assert ".cursor/skills" in raw["skills"]["directories"]
