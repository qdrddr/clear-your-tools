"""Regression tests: pytest/temp paths must not pollute tier_state.db."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.common.paths import is_ephemeral_workspace_path
from cyt.tiers.adapters.skills import is_ephemeral_skill_path
from cyt.tiers.config import resolve_tier_project
from cyt.tiers.manager import NoOpTierManager, TierManager, _managers, get_tier_manager
from cyt.tiers.models import EntityKind
from cyt.tiers.store import TierStore
from tests.support.tier_ephemeral_guard_fixtures import (
    EphemeralGuardFixturePack,
    clear_tier_manager_cache,
    count_ephemeral_tier_rows,
    load_polluted_seed,
    load_scenarios,
    open_real_project_with_polluted_seed,
    real_project_from_store,
    seed_polluted_user_db,
    stale_ephemeral_skill_state,
)


@pytest.mark.parametrize(
    "path",
    load_scenarios()["ephemeral_skill_paths"],
    ids=lambda p: Path(p).name,
)
def test_fixture_ephemeral_skill_paths_are_ephemeral(path: str) -> None:
    assert is_ephemeral_skill_path(path)


@pytest.mark.parametrize(
    "path",
    load_scenarios()["persistent_skill_paths"],
    ids=lambda p: Path(p).name,
)
def test_fixture_persistent_skill_paths_are_not_ephemeral(path: str) -> None:
    assert not is_ephemeral_skill_path(path)


@pytest.mark.parametrize(
    "path",
    load_scenarios()["ephemeral_workspace_paths"],
)
def test_fixture_ephemeral_workspace_paths_are_ephemeral(path: str) -> None:
    assert is_ephemeral_workspace_path(path)


@pytest.mark.parametrize(
    "path",
    load_scenarios()["persistent_workspace_paths"],
)
def test_fixture_persistent_workspace_paths_are_not_ephemeral(path: str) -> None:
    assert not is_ephemeral_workspace_path(path)


@pytest.mark.parametrize(
    "path",
    load_scenarios()["ephemeral_workspace_paths"],
)
def test_resolve_tier_project_rejects_ephemeral_workspaces(path: str) -> None:
    assert resolve_tier_project(workspace=Path(path)) is None


def test_open_purges_polluted_seed_from_fixture(
    ephemeral_guard_pack: EphemeralGuardFixturePack,
) -> None:
    seed = load_polluted_seed()
    seed_polluted_user_db(
        db_path=ephemeral_guard_pack.user_tier_db_path,
        real_project_root=ephemeral_guard_pack.real_repo_root,
        seed=seed,
    )
    before = count_ephemeral_tier_rows(ephemeral_guard_pack.user_tier_db_path)
    assert before["tier_project"] >= len(seed["ephemeral_projects"])
    assert before["entity_tier"] >= len(seed["ephemeral_skill_entity_ids"])

    with patch("cyt.tiers.store.is_default_user_cyt_db", return_value=True):
        store = TierStore.open(str(ephemeral_guard_pack.user_tier_db_path))
        try:
            project = real_project_from_store(store, ephemeral_guard_pack.real_repo_root)
            assert store.load_entity_states(project) == {}
        finally:
            store.close()

    after = count_ephemeral_tier_rows(ephemeral_guard_pack.user_tier_db_path)
    assert after == {"tier_project": 0, "entity_tier": 0, "entity_stats": 0}


def test_get_tier_manager_returns_noop_for_ephemeral_workspace(
    ephemeral_guard_pack: EphemeralGuardFixturePack,
    clear_tier_manager_cache: None,
) -> None:
    manager = get_tier_manager(
        ephemeral_guard_pack.config,
        workspace=ephemeral_guard_pack.ephemeral_workspace,
    )
    assert isinstance(manager, NoOpTierManager)
    assert count_ephemeral_tier_rows(ephemeral_guard_pack.user_tier_db_path) == {
        "tier_project": 0,
        "entity_tier": 0,
        "entity_stats": 0,
    }


def test_cached_manager_flush_does_not_rewrite_ephemeral_entity_ids(
    ephemeral_guard_pack: EphemeralGuardFixturePack,
    clear_tier_manager_cache: None,
) -> None:
    seed = load_polluted_seed()
    ephemeral_id = str(seed["ephemeral_skill_entity_ids"][0])
    with patch("cyt.tiers.store.is_default_user_cyt_db", return_value=True):
        open_real_project_with_polluted_seed(ephemeral_guard_pack).close()

        _managers.clear()
        manager = TierManager(
            ephemeral_guard_pack.real_repo_root,
            str(ephemeral_guard_pack.user_tier_db_path),
        )
        try:
            manager._states[(EntityKind.SKILL, ephemeral_id)] = stale_ephemeral_skill_state(
                ephemeral_id,
            )
            manager.flush_pending(force=True)
        finally:
            manager.close()

    assert count_ephemeral_tier_rows(ephemeral_guard_pack.user_tier_db_path) == {
        "tier_project": 0,
        "entity_tier": 0,
        "entity_stats": 0,
    }
