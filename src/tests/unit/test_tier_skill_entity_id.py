"""Tests for canonical skill tier entity IDs."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cyt.common.paths import shorten_home_path
from cyt.tiers.adapters.skills import canonical_skill_entity_id
from cyt.tiers.manager import TierManager, _managers


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


def test_canonical_skill_entity_id_resolves_home_and_absolute(tmp_path: Path) -> None:
    skill_path = tmp_path / "skills" / "RTK" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# RTK\n", encoding="utf-8")
    shortened = shorten_home_path(str(skill_path))
    assert canonical_skill_entity_id(shortened) == str(skill_path.resolve())
    assert canonical_skill_entity_id(str(skill_path)) == str(skill_path.resolve())


def test_record_skill_used_respects_last_injected(project_root: Path, base_config: dict) -> None:
    db_path = project_root / "tier_state.db"
    skill_path = project_root / "skill.md"
    skill_path.write_text("# Skill\n", encoding="utf-8")
    entity_id = str(skill_path.resolve())
    shortened = shorten_home_path(entity_id)

    config = dict(base_config)
    tools = dict(config.get("tools") or {})
    tools["tiers"] = {"enabled": False, "shadow": True, "database": {"path": str(db_path)}}
    config["tools"] = tools

    manager = TierManager(project_root, str(db_path))
    try:
        manager.record_skills_injected(
            [type("Match", (), {"file_path": shortened})()],
            config,
        )
        manager.record_skill_used(entity_id, config=config)
        state = manager._states.get(("skill", entity_id))
        assert state is not None
        assert state.stats.used >= 1.0
        assert state.stats.used_without_injection == 0.0
    finally:
        manager.close()


@pytest.fixture
def base_config() -> dict:
    from cyt.config import load_config

    return load_config()
