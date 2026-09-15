"""Regression tests: tier_state.db must not retain stale skill:doc entities."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cyt.tiers.adapters.skills import is_stale_skill_doc_entity
from cyt.tiers.store import TierStore
from tests.support.tier_skill_doc_fixtures import (
    TierSkillDocGuardPack,
    count_skill_entity,
    count_stale_skill_doc_rows,
    load_polluted_seed,
    load_scenarios,
    read_skill_used,
    seed_polluted_skill_doc_db,
)


@pytest.mark.parametrize(
    "doc_id",
    load_scenarios()["generic_doc_ids"],
)
def test_fixture_generic_doc_ids_are_stale(doc_id: str) -> None:
    entity_id = f"skill:doc:{doc_id}"
    assert is_stale_skill_doc_entity(entity_id, {entity_id})


def test_duplicate_skill_doc_is_stale_when_canonical_path_exists(
    tier_skill_doc_guard_pack: TierSkillDocGuardPack,
) -> None:
    canonical = str(tier_skill_doc_guard_pack.canonical_skill_path.resolve())
    doc_entity = "skill:doc:create-hook"
    assert is_stale_skill_doc_entity(doc_entity, {doc_entity, canonical})


def test_purge_stale_skill_doc_entities_removes_polluted_seed(
    tier_skill_doc_guard_pack: TierSkillDocGuardPack,
) -> None:
    seed = load_polluted_seed()
    seed_polluted_skill_doc_db(
        db_path=tier_skill_doc_guard_pack.user_tier_db_path,
        real_project_root=tier_skill_doc_guard_pack.workspace,
        canonical_skill_path=tier_skill_doc_guard_pack.canonical_skill_path,
        seed=seed,
    )
    before = count_stale_skill_doc_rows(tier_skill_doc_guard_pack.user_tier_db_path)
    assert before >= len(seed["stale_skill_doc_ids"]) + 1

    store = TierStore(str(tier_skill_doc_guard_pack.user_tier_db_path))
    try:
        removed = store.purge_stale_skill_doc_entities()
        assert removed >= len(seed["stale_skill_doc_ids"]) + 1
        assert count_stale_skill_doc_rows(tier_skill_doc_guard_pack.user_tier_db_path) == 0
    finally:
        store.close()


def test_purge_stale_skill_doc_entities_merges_duplicate_stats(
    tier_skill_doc_guard_pack: TierSkillDocGuardPack,
) -> None:
    seed = load_polluted_seed()
    seed_polluted_skill_doc_db(
        db_path=tier_skill_doc_guard_pack.user_tier_db_path,
        real_project_root=tier_skill_doc_guard_pack.workspace,
        canonical_skill_path=tier_skill_doc_guard_pack.canonical_skill_path,
        seed=seed,
    )
    canonical = str(tier_skill_doc_guard_pack.canonical_skill_path.resolve())

    store = TierStore(str(tier_skill_doc_guard_pack.user_tier_db_path))
    try:
        store.purge_stale_skill_doc_entities()
    finally:
        store.close()

    assert (
        count_skill_entity(tier_skill_doc_guard_pack.user_tier_db_path, "skill:doc:create-hook")
        == 0
    )
    assert count_skill_entity(tier_skill_doc_guard_pack.user_tier_db_path, canonical) == 1
    assert read_skill_used(tier_skill_doc_guard_pack.user_tier_db_path, canonical) == 1.0


def test_open_purges_stale_skill_doc_on_user_db(
    tier_skill_doc_guard_pack: TierSkillDocGuardPack,
) -> None:
    seed = load_polluted_seed()
    seed_polluted_skill_doc_db(
        db_path=tier_skill_doc_guard_pack.user_tier_db_path,
        real_project_root=tier_skill_doc_guard_pack.workspace,
        canonical_skill_path=tier_skill_doc_guard_pack.canonical_skill_path,
        seed=seed,
    )
    canonical = str(tier_skill_doc_guard_pack.canonical_skill_path.resolve())
    with (
        patch("cyt.tiers.store.is_default_user_cyt_db", return_value=True),
        patch.object(
            TierStore,
            "purge_ephemeral_projects",
            return_value=0,
        ),
    ):
        TierStore.open(str(tier_skill_doc_guard_pack.user_tier_db_path)).close()

    assert count_stale_skill_doc_rows(tier_skill_doc_guard_pack.user_tier_db_path) == 0
    assert count_skill_entity(tier_skill_doc_guard_pack.user_tier_db_path, canonical) == 1
