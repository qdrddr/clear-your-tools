"""Integration tests for DB maintenance across tool_examples and tier_state."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cyt.db.maintenance import run_cyt_db_maintenance
from cyt.tiers.maintenance import maybe_run_tier_maintenance_on_stats_query
from cyt.tiers.manager import _managers
from cyt.tool_examples.enrich import enrich_tools_with_examples
from cyt.tool_examples.record import record_tool_examples_capture
from tests.support.db_maintenance_fixtures import (
    DbMaintenanceFixturePack,
    build_examples_retention_config,
    build_unified_maintenance_config,
    count_tool_input_schemas,
    count_unique_schema_hashes,
    load_scenarios,
    load_tool_examples_seed,
    seed_schema_hash_diversity,
)


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


def test_maintenance_preserves_schema_hash_diversity_for_enrichment(
    db_maintenance_pack: DbMaintenanceFixturePack,
) -> None:
    """After maintenance, enrichment still serves examples from distinct schema variants."""
    scenario = load_scenarios()["tool_examples"]["schema_hash_diversity"]
    seed = load_tool_examples_seed()
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
    run_cyt_db_maintenance(config, vacuum=False)

    assert count_unique_schema_hashes(store_path) >= 2
    assert count_tool_input_schemas(store_path) <= scenario["expected"]["max_total_captures"]

    for capture in seed["captures"]:
        record_tool_examples_capture(
            workspace=db_maintenance_pack.workspace,
            mcp_server=str(scenario["mcp_server"]),
            tool_name=str(scenario["tool_name"]),
            input_schema=capture["schema"],
            args=capture["args"],
            config=config,
        )

    tool = dict(seed["tool"])
    tool["input_schema"] = seed["captures"][-1]["schema"]
    enriched = enrich_tools_with_examples(
        [tool],
        str(seed["enrichment_query"]),
        config,
    )
    examples = enriched[0]["cyt_injection_examples"]
    assert examples
    keys = {capture["inject_example_key"] for capture in seed["captures"]}
    assert any(key in example for example in examples for key in keys)


def test_unified_maintenance_end_to_end(db_maintenance_pack: DbMaintenanceFixturePack) -> None:
    """Seed both DBs, run unified maintenance, verify both sides are trimmed."""
    te_scenario = load_scenarios()["tool_examples"]["schema_hash_diversity"]
    tier_scenario = load_scenarios()["tier_state"]["epoch_log_cap"]

    from cyt.tiers.store import TierStore
    from cyt.tool_examples.store import ToolExamplesStore

    te_store = ToolExamplesStore(str(db_maintenance_pack.tool_examples_db))
    try:
        seed_schema_hash_diversity(
            te_store,
            project_root=db_maintenance_pack.workspace,
            scenario=te_scenario,
        )
    finally:
        te_store.close()

    tier_store = TierStore(str(db_maintenance_pack.tier_state_db))
    try:
        from tests.support.db_maintenance_fixtures import seed_epoch_log_rows

        seed_epoch_log_rows(
            tier_store,
            project_root=db_maintenance_pack.workspace,
            count=int(tier_scenario["epoch_log_count"]),
        )
    finally:
        tier_store.close()

    config = build_unified_maintenance_config(
        db_maintenance_pack.workspace,
        tool_examples_db=db_maintenance_pack.tool_examples_db,
        tier_state_db=db_maintenance_pack.tier_state_db,
        examples_retention=te_scenario["retention"],
        tier_retention=tier_scenario["retention"],
    )
    result = run_cyt_db_maintenance(config, vacuum=False)

    assert result.tool_examples is not None
    assert result.tier_state is not None
    assert (
        count_tool_input_schemas(db_maintenance_pack.tool_examples_db)
        <= te_scenario["expected"]["max_total_captures"]
    )

    from tests.support.db_maintenance_fixtures import count_epoch_log

    assert (
        count_epoch_log(db_maintenance_pack.tier_state_db)
        <= tier_scenario["expected"]["max_remaining"]
    )


def test_tier_stats_daily_maintenance_marker(
    db_maintenance_pack: DbMaintenanceFixturePack,
    db_maintenance_marker: Path,
) -> None:
    """Simulate tiers stats daily hook: maintenance runs once per day via marker file."""
    config = build_unified_maintenance_config(
        db_maintenance_pack.workspace,
        tool_examples_db=db_maintenance_pack.tool_examples_db,
        tier_state_db=db_maintenance_pack.tier_state_db,
        tier_retention={"enabled": True, "vacuum_after_maintenance": False},
    )
    first = maybe_run_tier_maintenance_on_stats_query(config)
    second = maybe_run_tier_maintenance_on_stats_query(config)
    assert first is not None
    assert second is None
    assert db_maintenance_marker.is_file()
