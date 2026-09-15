"""Integration regression: tier_state.db must not retain stale skill:doc entities."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.tiers.store import TierStore
from tests.support.tier_skill_doc_fixtures import (
    SkillDocIntegrationScenario,
    count_skill_entity,
    count_stale_skill_doc_rows,
    load_integration_scenarios,
    load_polluted_seed,
    materialize_skill_doc_guard_pack,
    read_skill_used,
    seed_polluted_skill_doc_db,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "scenario",
    load_integration_scenarios(),
    ids=lambda item: item.id,
)
def test_tier_skill_doc_guard_integration_scenarios(
    scenario: SkillDocIntegrationScenario,
    tmp_path: Path,
) -> None:
    if scenario.id == "open_purges_stale_skill_doc_seed":
        _assert_open_purges_stale_skill_doc_seed(tmp_path)
    elif scenario.id == "open_merges_duplicate_skill_doc":
        _assert_open_merges_duplicate_skill_doc(tmp_path)
    elif scenario.id == "open_purges_executor_execute_artifact":
        _assert_open_purges_executor_execute_artifact(tmp_path)
    else:
        raise AssertionError(f"unknown integration scenario: {scenario.id}")


def _assert_open_purges_stale_skill_doc_seed(tmp_path: Path) -> None:
    pack = materialize_skill_doc_guard_pack(tmp_path / "guard")
    seed = load_polluted_seed()
    seed_polluted_skill_doc_db(
        db_path=pack.user_tier_db_path,
        real_project_root=pack.workspace,
        canonical_skill_path=pack.canonical_skill_path,
        seed=seed,
    )
    assert count_stale_skill_doc_rows(pack.user_tier_db_path) >= len(seed["stale_skill_doc_ids"])

    with patch("cyt.tiers.store.is_default_user_cyt_db", return_value=True):
        TierStore.open(str(pack.user_tier_db_path)).close()

    assert count_stale_skill_doc_rows(pack.user_tier_db_path) == 0


def _assert_open_merges_duplicate_skill_doc(tmp_path: Path) -> None:
    pack = materialize_skill_doc_guard_pack(tmp_path / "guard")
    seed = load_polluted_seed()
    seed_polluted_skill_doc_db(
        db_path=pack.user_tier_db_path,
        real_project_root=pack.workspace,
        canonical_skill_path=pack.canonical_skill_path,
        seed=seed,
    )
    canonical = str(pack.canonical_skill_path.resolve())

    with (
        patch("cyt.tiers.store.is_default_user_cyt_db", return_value=True),
        patch.object(
            TierStore,
            "purge_ephemeral_projects",
            return_value=0,
        ),
    ):
        TierStore.open(str(pack.user_tier_db_path)).close()

    assert count_skill_entity(pack.user_tier_db_path, "skill:doc:create-hook") == 0
    assert count_skill_entity(pack.user_tier_db_path, canonical) == 1
    assert read_skill_used(pack.user_tier_db_path, canonical) == 1.0


def _assert_open_purges_executor_execute_artifact(tmp_path: Path) -> None:
    pack = materialize_skill_doc_guard_pack(tmp_path / "guard")
    seed = load_polluted_seed()
    seed_polluted_skill_doc_db(
        db_path=pack.user_tier_db_path,
        real_project_root=pack.workspace,
        canonical_skill_path=pack.canonical_skill_path,
        seed=seed,
    )
    assert count_skill_entity(pack.user_tier_db_path, "executor/execute") == 1

    with patch("cyt.tiers.store.is_default_user_cyt_db", return_value=True):
        TierStore.open(str(pack.user_tier_db_path)).close()

    assert count_skill_entity(pack.user_tier_db_path, "executor/execute") == 0
