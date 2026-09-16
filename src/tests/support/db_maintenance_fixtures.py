"""Shared loaders and seed helpers for DB maintenance regression tests."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.tiers.models import EntityKind, Tier
from cyt.tiers.store import TierStore
from cyt.tool_examples.store import ToolExamplesStore

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "db_maintenance"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"
TOOL_EXAMPLES_SEED_PATH = FIXTURES_ROOT / "tool_examples_seed.json"

_TIER_BY_NAME = {
    "DORMANT": Tier.DORMANT,
    "COLD": Tier.COLD,
    "ACTIVE": Tier.ACTIVE,
    "HOT": Tier.HOT,
    "EXTRA_HOT": Tier.EXTRA_HOT,
}


@dataclass(frozen=True)
class DbMaintenanceFixturePack:
    workspace: Path
    tool_examples_db: Path
    tier_state_db: Path
    config: dict[str, Any]
    scenarios: dict[str, Any]


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"expected JSON object in {path}")
    return payload


def load_scenarios(path: Path = SCENARIOS_PATH) -> dict[str, Any]:
    return load_json(path)


def load_tool_examples_seed(path: Path = TOOL_EXAMPLES_SEED_PATH) -> dict[str, Any]:
    return load_json(path)


def _tier_from_name(raw: str) -> Tier:
    return _TIER_BY_NAME.get(raw.upper(), Tier.ACTIVE)


def build_examples_retention_config(
    workspace: Path,
    db_path: Path,
    retention: dict[str, Any],
) -> dict[str, Any]:
    return set_hook_workspace_in_config(
        {
            "tools": {
                "examples": {
                    "enabled": True,
                    "database": {"path": str(db_path)},
                    "retention": retention,
                },
            },
        },
        workspace,
    )


def build_tier_retention_config(
    workspace: Path,
    db_path: Path,
    *,
    retention: dict[str, Any] | None = None,
    mode: str = "shadow",
) -> dict[str, Any]:
    tiers_block: dict[str, Any] = {
        "mode": mode,
        "database": {"path": str(db_path)},
    }
    if retention is not None:
        tiers_block["retention"] = retention
    return set_hook_workspace_in_config({"tools": {"tiers": tiers_block}}, workspace)


def build_unified_maintenance_config(
    workspace: Path,
    *,
    tool_examples_db: Path,
    tier_state_db: Path,
    examples_retention: dict[str, Any] | None = None,
    tier_retention: dict[str, Any] | None = None,
) -> dict[str, Any]:
    examples_block: dict[str, Any] = {
        "enabled": True,
        "database": {"path": str(tool_examples_db)},
    }
    if examples_retention is not None:
        examples_block["retention"] = examples_retention
    tiers_block: dict[str, Any] = {
        "mode": "shadow",
        "database": {"path": str(tier_state_db)},
    }
    if tier_retention is not None:
        tiers_block["retention"] = tier_retention
    return set_hook_workspace_in_config(
        {"tools": {"examples": examples_block, "tiers": tiers_block}},
        workspace,
    )


def seed_schema_hash_diversity(
    store: ToolExamplesStore,
    *,
    project_root: Path,
    scenario: dict[str, Any],
) -> None:
    project_id = store.get_or_create_project(str(project_root))
    mcp_server = str(scenario["mcp_server"])
    tool_name = str(scenario["tool_name"])
    for variant in scenario["schema_variants"]:
        if not isinstance(variant, dict):
            continue
        schema = variant["schema"]
        count = int(variant.get("capture_count", 1))
        for idx in range(count):
            args: dict[str, Any] = {"query": f"{variant['id']}-{idx}"}
            if "repo" in schema.get("properties", {}):
                args["repo"] = f"owner/repo-{idx}"
            store.upsert_capture(project_id, mcp_server, tool_name, schema, args)


def seed_historical_examples(
    store: ToolExamplesStore,
    *,
    project_root: Path,
    scenario: dict[str, Any],
) -> int:
    project_id = store.get_or_create_project(str(project_root))
    schema = scenario["schema"]
    mcp_server = str(scenario["mcp_server"])
    tool_name = str(scenario["tool_name"])
    schema_id = store.upsert_capture(
        project_id,
        mcp_server,
        tool_name,
        schema,
        {"queries": ["seed"]},
    )
    now_ms = int(time.time() * 1000)
    stale_ms = now_ms - 10 * 86400 * 1000
    stale_count = int(scenario.get("stale_example_count", 5))
    path = "inputSchema.properties.queries.items[]"
    with store._lock:
        for idx in range(stale_count):
            store._conn.execute(
                "INSERT INTO tool_example(schema_id, json_path, value, value_type, timestamp_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (schema_id, path, f'"stale-{idx}"', "string", stale_ms + idx),
            )
        store._conn.commit()
    return schema_id


def seed_tier_entities(
    store: TierStore,
    *,
    project_root: Path,
    entities: list[dict[str, Any]],
) -> None:
    project_id = store.get_or_create_project(str(project_root))
    now_ms = int(time.time() * 1000)
    with store._lock:
        for entity in entities:
            entity_id = str(entity["entity_id"])
            kind = str(entity.get("kind", EntityKind.TOOL))
            tier_name = str(entity.get("effective_tier", "ACTIVE"))
            tier = _tier_from_name(tier_name)
            last_seen_days_ago = entity.get("last_seen_days_ago")
            last_seen_ms = (
                now_ms - int(last_seen_days_ago) * 86400 * 1000
                if last_seen_days_ago is not None
                else now_ms
            )
            store._conn.execute(
                "INSERT INTO entity_stats("
                "project_id, kind, entity_id, pipeline, candidates, injected, used, "
                "attempts, used_without_injection, optional_used, shadow_hits, "
                "shadow_evaluations, last_seen_ms, requests_since_decay"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    project_id,
                    kind,
                    entity_id,
                    "default",
                    float(entity.get("candidates", 0.0)),
                    float(entity.get("injected", 0.0)),
                    float(entity.get("used", 0.0)),
                    float(entity.get("attempts", 0.0)),
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    last_seen_ms,
                    int(entity.get("requests_since_decay", 0)),
                ),
            )
            store._conn.execute(
                "INSERT INTO entity_tier("
                "project_id, kind, entity_id, stable_tier, effective_tier, tier_since_epoch"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, kind, entity_id, int(tier), int(tier), 0),
            )
        store._conn.commit()


def seed_epoch_log_rows(
    store: TierStore,
    *,
    project_root: Path,
    count: int,
) -> None:
    project_id = store.get_or_create_project(str(project_root))
    now_ms = int(time.time() * 1000)
    with store._lock:
        for idx in range(count):
            store._conn.execute(
                "INSERT INTO epoch_log(project_id, epoch_id, ts_ms, transitions_json) "
                "VALUES (?, ?, ?, ?)",
                (project_id, idx, now_ms - idx * 86400 * 1000, "[]"),
            )
        store._conn.commit()


def count_tool_input_schemas(db_path: Path) -> int:
    store = ToolExamplesStore(str(db_path))
    try:
        row = store._conn.execute("SELECT COUNT(*) FROM tool_input_schema").fetchone()
        return int(row[0]) if row is not None else 0
    finally:
        store.close()


def count_unique_schema_hashes(db_path: Path) -> int:
    store = ToolExamplesStore(str(db_path))
    try:
        row = store._conn.execute(
            "SELECT COUNT(DISTINCT schema_hash) FROM tool_input_schema",
        ).fetchone()
        return int(row[0]) if row is not None else 0
    finally:
        store.close()


def count_tool_examples(db_path: Path) -> int:
    store = ToolExamplesStore(str(db_path))
    try:
        row = store._conn.execute("SELECT COUNT(*) FROM tool_example").fetchone()
        return int(row[0]) if row is not None else 0
    finally:
        store.close()


def list_entity_ids(db_path: Path) -> set[str]:
    store = TierStore(str(db_path))
    try:
        rows = store._conn.execute("SELECT entity_id FROM entity_stats").fetchall()
        return {str(row[0]) for row in rows}
    finally:
        store.close()


def count_epoch_log(db_path: Path) -> int:
    store = TierStore(str(db_path))
    try:
        row = store._conn.execute("SELECT COUNT(*) FROM epoch_log").fetchone()
        return int(row[0]) if row is not None else 0
    finally:
        store.close()


def entity_candidates(db_path: Path, entity_id: str) -> float:
    store = TierStore(str(db_path))
    try:
        row = store._conn.execute(
            "SELECT candidates FROM entity_stats WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()
        return float(row[0]) if row is not None else 0.0
    finally:
        store.close()


@pytest.fixture
def db_maintenance_pack(tmp_path: Path) -> DbMaintenanceFixturePack:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    tool_examples_db = tmp_path / "tool_examples.db"
    tier_state_db = tmp_path / "tier_state.db"
    scenarios = load_scenarios()
    config = build_unified_maintenance_config(
        workspace,
        tool_examples_db=tool_examples_db,
        tier_state_db=tier_state_db,
    )
    return DbMaintenanceFixturePack(
        workspace=workspace,
        tool_examples_db=tool_examples_db,
        tier_state_db=tier_state_db,
        config=config,
        scenarios=scenarios,
    )


@pytest.fixture
def db_maintenance_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    marker = tmp_path / ".tier_db_maintenance_last_run"
    from cyt.tiers import maintenance as maintenance_mod

    def _patched_marker_path(config: dict[str, Any]) -> Path:
        _ = config
        return marker

    monkeypatch.setattr(maintenance_mod, "_maintenance_marker_path", _patched_marker_path)
    yield marker
