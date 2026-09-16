"""Unit tests for unified DB maintenance orchestration."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt.db.maintenance import run_cyt_db_maintenance
from cyt.tiers.maintenance import run_tier_state_maintenance
from cyt.tiers.models import EffectiveStats
from cyt.tiers.retention_config import tier_retention_config
from cyt.tool_examples.maintenance import run_tool_examples_maintenance
from tests.support.db_maintenance_fixtures import (
    DbMaintenanceFixturePack,
    build_examples_retention_config,
    build_tier_retention_config,
    build_unified_maintenance_config,
    count_epoch_log,
    count_tool_input_schemas,
    count_unique_schema_hashes,
    entity_candidates,
    list_entity_ids,
    load_scenarios,
    seed_epoch_log_rows,
    seed_historical_examples,
    seed_schema_hash_diversity,
    seed_tier_entities,
)


def test_tier_retention_config_inherits_request_half_life() -> None:
    cfg = {
        "tools": {
            "tiers": {
                "statistics": {"request_half_life": 42},
                "retention": {"enabled": True},
            },
        },
    }
    retention = tier_retention_config(cfg)
    assert retention.request_half_life == 42.0
    assert retention.enabled is True
    assert retention.epoch_log_max_entries == 200


def test_tier_retention_config_explicit_half_life_override() -> None:
    cfg = {
        "tools": {
            "tiers": {
                "statistics": {"request_half_life": 42},
                "retention": {"request_half_life": 75},
            },
        },
    }
    assert tier_retention_config(cfg).request_half_life == 75.0


def test_run_cyt_db_maintenance_dry_run(db_maintenance_pack: DbMaintenanceFixturePack) -> None:
    result = run_cyt_db_maintenance(db_maintenance_pack.config, dry_run=True)
    assert result.tool_examples is not None
    assert result.tier_state is not None
    assert result.tool_examples.dry_run is True
    assert result.tier_state.dry_run is True


def test_schema_hash_diversity_retention_from_fixture(
    db_maintenance_pack: DbMaintenanceFixturePack,
) -> None:
    scenario = load_scenarios()["tool_examples"]["schema_hash_diversity"]
    store_path = db_maintenance_pack.tool_examples_db
    from cyt.tool_examples.store import ToolExamplesStore

    store = ToolExamplesStore(str(store_path))
    try:
        seed_schema_hash_diversity(
            store,
            project_root=db_maintenance_pack.workspace,
            scenario=scenario,
        )
    finally:
        store.close()

    config = build_examples_retention_config(
        db_maintenance_pack.workspace,
        store_path,
        scenario["retention"],
    )
    result = run_tool_examples_maintenance(config, vacuum=False)
    assert result.deleted.get("tool_input_schema", 0) >= 5
    assert (
        count_unique_schema_hashes(store_path) >= scenario["expected"]["min_unique_schema_hashes"]
    )
    assert count_tool_input_schemas(store_path) <= scenario["expected"]["max_total_captures"]


def test_historical_floor_retention_from_fixture(
    db_maintenance_pack: DbMaintenanceFixturePack,
) -> None:
    scenario = load_scenarios()["tool_examples"]["historical_floor"]
    store_path = db_maintenance_pack.tool_examples_db
    from cyt.tool_examples.store import ToolExamplesStore

    store = ToolExamplesStore(str(store_path))
    try:
        schema_id = seed_historical_examples(
            store,
            project_root=db_maintenance_pack.workspace,
            scenario=scenario,
        )
    finally:
        store.close()

    config = build_examples_retention_config(
        db_maintenance_pack.workspace,
        store_path,
        scenario["retention"],
    )
    result = run_tool_examples_maintenance(config, vacuum=False)
    assert result.deleted.get("tool_example_stale", 0) >= scenario["expected"]["min_removed"]

    store = ToolExamplesStore(str(store_path))
    try:
        rows = store.list_examples_for_path(
            [schema_id],
            "inputSchema.properties.queries.items[]",
            limit=20,
        )
        assert len(rows) == scenario["expected"]["remaining_examples"]
    finally:
        store.close()


def test_tier_decay_and_dormant_prune_from_fixture(
    db_maintenance_pack: DbMaintenanceFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cyt.tiers.adapters.tools.resolve_tracked_catalog_entity_ids",
        lambda _config, blocking=False: None,
    )
    scenario = load_scenarios()["tier_state"]["decay_and_prune"]
    store_path = db_maintenance_pack.tier_state_db
    from cyt.tiers.store import TierStore

    store = TierStore(str(store_path))
    try:
        seed_tier_entities(
            store,
            project_root=db_maintenance_pack.workspace,
            entities=scenario["entities"],
        )
    finally:
        store.close()

    config = build_tier_retention_config(
        db_maintenance_pack.workspace,
        store_path,
        retention=scenario["retention"],
    )
    run_tier_state_maintenance(config, vacuum=False)

    decay_id = scenario["expected"]["decayed_entity_id"]
    runtime = EffectiveStats(candidates=100.0, requests_since_decay=150)
    runtime.decay(half_life=100.0)
    assert abs(entity_candidates(store_path, decay_id) - runtime.candidates) < 1e-5

    remaining = list_entity_ids(store_path)
    for entity_id in scenario["expected"]["removed_entity_ids"]:
        assert entity_id not in remaining
    for entity_id in scenario["expected"]["kept_entity_ids"]:
        assert entity_id in remaining


def test_epoch_log_cap_from_fixture(db_maintenance_pack: DbMaintenanceFixturePack) -> None:
    scenario = load_scenarios()["tier_state"]["epoch_log_cap"]
    store_path = db_maintenance_pack.tier_state_db
    from cyt.tiers.store import TierStore

    store = TierStore(str(store_path))
    try:
        seed_epoch_log_rows(
            store,
            project_root=db_maintenance_pack.workspace,
            count=int(scenario["epoch_log_count"]),
        )
    finally:
        store.close()

    config = build_tier_retention_config(
        db_maintenance_pack.workspace,
        store_path,
        retention=scenario["retention"],
    )
    result = run_tier_state_maintenance(config, vacuum=False)
    assert result.deleted.get("epoch_log", 0) >= 1
    assert count_epoch_log(store_path) <= scenario["expected"]["max_remaining"]


def test_purge_stale_catalog_tool_entities_from_fixture(
    db_maintenance_pack: DbMaintenanceFixturePack,
) -> None:
    scenario = load_scenarios()["tier_state"]["stale_catalog_source"]
    store_path = db_maintenance_pack.tier_state_db
    from cyt.tiers.store import TierStore

    store = TierStore(str(store_path))
    try:
        seed_tier_entities(
            store,
            project_root=db_maintenance_pack.workspace,
            entities=scenario["entities"],
        )
        removed = store.purge_stale_catalog_tool_entities(
            allowed_sources=frozenset({"cyt_mcp"}),
            catalog_entity_ids=None,
        )
    finally:
        store.close()

    assert removed >= 1
    remaining = list_entity_ids(store_path)
    for entity_id in scenario["expected"]["removed_entity_ids"]:
        assert entity_id not in remaining
    for entity_id in scenario["expected"]["kept_entity_ids"]:
        assert entity_id in remaining


def test_tool_examples_maintenance_vacuums_after_deletes(
    db_maintenance_pack: DbMaintenanceFixturePack,
) -> None:
    scenario = load_scenarios()["tool_examples"]["schema_hash_diversity"]
    store_path = db_maintenance_pack.tool_examples_db
    from cyt.tool_examples.store import ToolExamplesStore

    store = ToolExamplesStore(str(store_path))
    try:
        seed_schema_hash_diversity(
            store,
            project_root=db_maintenance_pack.workspace,
            scenario=scenario,
        )
    finally:
        store.close()

    retention = dict(scenario["retention"])
    retention["vacuum_after_maintenance"] = True
    config = build_examples_retention_config(
        db_maintenance_pack.workspace,
        store_path,
        retention,
    )
    result = run_tool_examples_maintenance(config, vacuum=True)
    assert result.deleted.get("tool_input_schema", 0) > 0
    assert result.vacuumed is True


def test_tier_state_maintenance_vacuums_after_deletes(
    db_maintenance_pack: DbMaintenanceFixturePack,
) -> None:
    scenario = load_scenarios()["tier_state"]["epoch_log_cap"]
    store_path = db_maintenance_pack.tier_state_db
    from cyt.tiers.store import TierStore

    store = TierStore(str(store_path))
    try:
        seed_epoch_log_rows(
            store,
            project_root=db_maintenance_pack.workspace,
            count=int(scenario["epoch_log_count"]),
        )
    finally:
        store.close()

    retention = dict(scenario["retention"])
    retention["vacuum_after_maintenance"] = True
    config = build_tier_retention_config(
        db_maintenance_pack.workspace,
        store_path,
        retention=retention,
    )
    result = run_tier_state_maintenance(config, vacuum=True)
    assert result.deleted.get("epoch_log", 0) > 0
    assert result.vacuumed is True


def test_purge_stale_catalog_entity_not_in_tracked_set(
    db_maintenance_pack: DbMaintenanceFixturePack,
) -> None:
    store_path = db_maintenance_pack.tier_state_db
    from cyt.tiers.store import TierStore

    store = TierStore(str(store_path))
    try:
        seed_tier_entities(
            store,
            project_root=db_maintenance_pack.workspace,
            entities=[
                {
                    "entity_id": "cyt_mcp:removed_from_catalog",
                    "kind": "tool",
                    "effective_tier": "ACTIVE",
                    "candidates": 3.0,
                },
                {
                    "entity_id": "cyt_mcp:still_tracked",
                    "kind": "tool",
                    "effective_tier": "ACTIVE",
                    "candidates": 3.0,
                },
            ],
        )
        removed = store.purge_stale_catalog_tool_entities(
            allowed_sources=frozenset({"cyt_mcp"}),
            catalog_entity_ids=frozenset({"cyt_mcp:still_tracked"}),
        )
    finally:
        store.close()

    assert removed == 1
    remaining = list_entity_ids(store_path)
    assert "cyt_mcp:still_tracked" in remaining
    assert "cyt_mcp:removed_from_catalog" not in remaining


def test_unified_maintenance_runs_both_databases(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    examples_db = tmp_path / "tool_examples.db"
    tier_db = tmp_path / "tier_state.db"
    config = build_unified_maintenance_config(
        workspace,
        tool_examples_db=examples_db,
        tier_state_db=tier_db,
        examples_retention={"vacuum_after_maintenance": False},
        tier_retention={"enabled": True, "vacuum_after_maintenance": False},
    )
    scenarios = load_scenarios()["unified"]["both_databases"]
    result = run_cyt_db_maintenance(config, dry_run=True)
    for section in scenarios["expected_sections"]:
        assert section in result.to_dict()
