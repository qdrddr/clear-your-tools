"""Tests for canonical skill tier entity IDs."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cyt.common.paths import shorten_home_path
from cyt.tiers.adapters.skills import (
    _path_matches_doc_id,
    canonical_skill_entity_id,
    is_ephemeral_skill_path,
    normalize_skill_entity_states,
    resolve_skill_doc_id,
    resolve_skill_entity_id,
    resolve_skill_frontmatter_name,
    skill_display_name,
    stable_skill_doc_entity_id,
    tier_entity_id_for_skill,
)
from cyt.tiers.manager import TierManager, _managers
from cyt.tiers.models import EffectiveStats, EntityKind, EntityTierState


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


def test_ephemeral_skill_paths_use_doc_id_entity() -> None:
    temp_path = "/var/folders/xx/T/pytest-of-user/test0/skills/create-hook.md"
    assert is_ephemeral_skill_path(temp_path)
    real_layout = "/var/folders/xx/T/pytest-of-user/test0/.cursor/skills/create-hook/SKILL.md"
    assert not is_ephemeral_skill_path(real_layout)
    assert canonical_skill_entity_id(temp_path) == ""
    assert resolve_skill_entity_id(temp_path) == ""
    assert resolve_skill_entity_id(temp_path, doc_id="create-hook") == stable_skill_doc_entity_id(
        "create-hook",
    )


def test_mcpc_skill_paths_stay_virtual() -> None:
    virtual = "mcpc/cursor/skills/create-hook.md"
    assert resolve_skill_entity_id(virtual) == virtual
    assert canonical_skill_entity_id(virtual) == virtual


def test_resolve_skill_frontmatter_name_from_source_path(tmp_path: Path) -> None:
    skill_path = tmp_path / "create-hook" / "SKILL.md"
    assert (
        resolve_skill_frontmatter_name(str(skill_path), None, source_path=str(skill_path)) is None
    )

    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: create-hook\ndescription: hooks\n---\n",
        encoding="utf-8",
    )
    assert (
        resolve_skill_frontmatter_name(str(skill_path), None, source_path=str(skill_path))
        == "create-hook"
    )


def test_canonical_skill_entity_id_resolves_home_and_absolute() -> None:
    skill_path = Path("/Users/example/skills/RTK/SKILL.md")
    shortened = shorten_home_path(str(skill_path))
    assert canonical_skill_entity_id(shortened) == str(skill_path.resolve())
    assert canonical_skill_entity_id(str(skill_path)) == str(skill_path.resolve())


def test_path_matches_doc_id_uses_parent_for_skill_md(tmp_path: Path) -> None:
    skill_path = tmp_path / "create-hook" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# hook\n", encoding="utf-8")
    assert _path_matches_doc_id(skill_path, "create-hook")


def test_resolve_skill_doc_id_uses_parent_for_skill_md(tmp_path: Path) -> None:
    skill_path = tmp_path / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# demo\n", encoding="utf-8")
    assert resolve_skill_doc_id(str(skill_path)) == "demo"


def test_normalize_skill_entity_states_merges_ephemeral_rows() -> None:
    temp_path = "/var/folders/xx/T/pytest-of-user/test0/skills/create-hook/SKILL.md"
    canonical = resolve_skill_entity_id(temp_path, doc_id="create-hook")
    states = {
        (EntityKind.SKILL, temp_path): EntityTierState(
            entity_id=temp_path,
            kind=EntityKind.SKILL,
            stats=EffectiveStats(candidates=3.0, injected=1.0),
        ),
    }
    removed, updated = normalize_skill_entity_states(states)
    assert removed == [temp_path]
    assert len(updated) == 1
    assert updated[0].entity_id == canonical
    assert states[(EntityKind.SKILL, canonical)].stats.candidates == 3.0


def test_skill_display_name_never_returns_ephemeral_path() -> None:
    temp_path = "/var/folders/xx/T/pytest-of-user/test0/skills/create-hook.md"
    assert skill_display_name(temp_path, doc_id="create-hook") == "create-hook"


def test_normalize_skill_entity_states_deletes_unmergeable_ephemeral_rows() -> None:
    from cyt.tiers.models import EffectiveStats, EntityKind, EntityTierState

    temp_path = "/var/folders/xx/T/pytest-of-user/test0/skills/unknown-skill.md"
    states = {
        (EntityKind.SKILL, temp_path): EntityTierState(
            entity_id=temp_path,
            kind=EntityKind.SKILL,
            stats=EffectiveStats(candidates=1.0),
        ),
    }
    removed, updated = normalize_skill_entity_states(states)
    assert removed == [temp_path]
    assert updated == []
    assert states == {}


def test_tier_entity_id_for_skill_rejects_unresolved_ephemeral_path() -> None:
    temp_path = "/var/folders/xx/T/pytest-of-user/test0/skills/unknown-skill.md"
    assert tier_entity_id_for_skill(temp_path) is None


def test_tier_entity_id_for_skill_maps_ephemeral_to_doc_id_with_entry_doc_id() -> None:
    temp_path = "/var/folders/xx/T/pytest-of-user/test0/skills/create-hook.md"
    assert tier_entity_id_for_skill(temp_path, doc_id="create-hook") == stable_skill_doc_entity_id(
        "create-hook",
    )


def test_record_skill_candidates_skips_ephemeral_source(
    project_root: Path,
    base_config: dict,
) -> None:
    db_path = project_root / "tier_state.db"
    config = dict(base_config)
    tools = dict(config.get("tools") or {})
    tools["tiers"] = {"enabled": False, "shadow": True, "database": {"path": str(db_path)}}
    config["tools"] = tools
    temp_path = "/var/folders/xx/T/pytest-of-user/test0/skills/create-hook.md"
    entry = type(
        "Entry",
        (),
        {"source_path": temp_path, "doc_id": None},
    )()

    manager = TierManager(project_root, str(db_path))
    try:
        manager.record_skill_candidates([entry], config)
        assert not any(
            is_ephemeral_skill_path(state.entity_id)
            for (kind, _), state in manager._states.items()
            if kind == "skill"
        )
    finally:
        manager.close()


def test_record_skill_used_respects_last_injected(project_root: Path, base_config: dict) -> None:
    db_path = project_root / "tier_state.db"
    skill_path = project_root / "skill.md"
    skill_path.write_text("# Skill\n", encoding="utf-8")
    entity_id = resolve_skill_entity_id(str(skill_path.resolve()), doc_id="skill")
    shortened = shorten_home_path(str(skill_path.resolve()))

    config = dict(base_config)
    tools = dict(config.get("tools") or {})
    tools["tiers"] = {"enabled": False, "shadow": True, "database": {"path": str(db_path)}}
    config["tools"] = tools

    manager = TierManager(project_root, str(db_path))
    try:
        manager.record_skills_injected(
            [type("Match", (), {"file_path": shortened, "doc_id": "skill"})()],
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
