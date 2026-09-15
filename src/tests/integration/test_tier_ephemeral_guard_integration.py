"""Integration regression: tier_state.db must never retain pytest/temp paths."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cyt.hook.permissions_react import react_to_permissions_changed
from cyt.skills.agent_interceptor import run_skill_read_intercept
from cyt.skills.catalog import SkillEntryRef
from cyt.skills.search import MatchedSkill
from cyt.tiers.manager import TierManager, _managers, get_tier_manager
from tests.support.tier_ephemeral_guard_fixtures import (
    EphemeralGuardFixturePack,
    IntegrationScenario,
    count_ephemeral_tier_rows,
    load_integration_scenarios,
    load_polluted_seed,
    seed_polluted_user_db,
    stale_ephemeral_skill_state,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "scenario",
    load_integration_scenarios(),
    ids=lambda s: s.id,
)
def test_tier_ephemeral_guard_integration_scenarios(
    scenario: IntegrationScenario,
    ephemeral_guard_pack: EphemeralGuardFixturePack,
    monkeypatch: pytest.MonkeyPatch,
    clear_tier_manager_cache: None,
) -> None:
    monkeypatch.setattr(
        "cyt.permissions.merge.DEFAULT_USER_CONFIG_PATH",
        ephemeral_guard_pack.global_config_path,
    )
    monkeypatch.setattr(
        "cyt.config.DEFAULT_USER_CONFIG_PATH",
        ephemeral_guard_pack.global_config_path,
    )

    if scenario.id == "permissions_react_ephemeral_workspace":
        _assert_permissions_react_does_not_pollute_user_db(ephemeral_guard_pack)
    elif scenario.id == "skill_read_intercept_ephemeral_skill":
        _assert_skill_read_intercept_does_not_pollute_user_db(ephemeral_guard_pack)
    elif scenario.id == "cached_manager_flush_stale_ephemeral":
        _assert_cached_manager_reconcile_cleans_user_db(ephemeral_guard_pack)
    else:
        raise AssertionError(f"unknown integration scenario: {scenario.id}")


def _assert_permissions_react_does_not_pollute_user_db(
    pack: EphemeralGuardFixturePack,
) -> None:
    react_to_permissions_changed(
        agent="cursor",
        workspace_root=pack.ephemeral_workspace,
    )
    manager = get_tier_manager(pack.config, workspace=pack.ephemeral_workspace)
    assert manager.is_noop
    assert count_ephemeral_tier_rows(pack.user_tier_db_path) == {
        "tier_project": 0,
        "entity_tier": 0,
        "entity_stats": 0,
    }


def _assert_skill_read_intercept_does_not_pollute_user_db(
    pack: EphemeralGuardFixturePack,
) -> None:
    skill_path = pack.ephemeral_workspace / ".cursor" / "skills" / "demo" / "SKILL.md"
    payload = {
        "cyt_intercept_read_path": str(skill_path),
        "cyt_intercept_query": "User_Asks: demo section",
        "workspace_roots": [str(pack.ephemeral_workspace)],
        "conversation_id": "sess-ephemeral-guard",
    }
    entry = SkillEntryRef(
        source_path=str(skill_path),
        doc_id="demo",
        content_sha256="abc",
        cache_key="cache",
        entry_dir=str(pack.ephemeral_workspace / "entry"),
        nodes_dir=str(pack.ephemeral_workspace / "entry" / "nodes"),
        chunk_dir=str(pack.ephemeral_workspace / "entry" / "chunks"),
        bm25_chunk_dir=str(pack.ephemeral_workspace / "entry" / "bm25"),
        pipeline="bm25",
        index_params_hash="hash",
        disk_backed=False,
        document={"name": "demo", "description": "demo skill"},
    )
    with patch("cyt.skills.agent_interceptor._ensure_skill_entry", return_value=entry):
        with patch("cyt.skills.agent_interceptor._prune_single_skill") as prune:
            prune.return_value = MatchedSkill(
                doc_id="demo",
                file_path=str(skill_path),
                markdown="# Skinny",
                name="demo",
                score=1.0,
                token_count=10,
            )
            result = run_skill_read_intercept(payload, pack.config)
    assert result["permission"] == "allow"
    assert count_ephemeral_tier_rows(pack.user_tier_db_path) == {
        "tier_project": 0,
        "entity_tier": 0,
        "entity_stats": 0,
    }


def _assert_cached_manager_reconcile_cleans_user_db(
    pack: EphemeralGuardFixturePack,
) -> None:
    seed = load_polluted_seed()
    ephemeral_id = str(seed["ephemeral_skill_entity_ids"][0])
    seed_polluted_user_db(
        db_path=pack.user_tier_db_path,
        real_project_root=pack.real_repo_root,
        seed=seed,
    )

    _managers.clear()
    with patch("cyt.tiers.store.is_default_user_cyt_db", return_value=True):
        manager = get_tier_manager(pack.config, workspace=pack.real_repo_root)
        assert isinstance(manager, TierManager)
        manager._states[("skill", ephemeral_id)] = stale_ephemeral_skill_state(ephemeral_id)
        manager.flush_pending(force=True)

        assert count_ephemeral_tier_rows(pack.user_tier_db_path) == {
            "tier_project": 0,
            "entity_tier": 0,
            "entity_stats": 0,
        }
        manager.close()
