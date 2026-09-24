"""Shared fixture loader for tier slow-clock and fast/hot transition tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyt.tiers.adapters.tools import stamp_tool_catalog_source
from cyt.tiers.models import EffectiveStats, EntityKind, EntityTierState, EpochState, Tier, TierProject
from cyt.tiers.store import TierStore
from tests.support.tier_seed_helpers import parse_tier

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tier_transitions"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"


def _coerce_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        return int(value)
    return default


def _load_payload(path: Path = SCENARIOS_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def load_config_values(path: Path = SCENARIOS_PATH) -> dict[str, Any]:
    raw = _load_payload(path).get("config")
    return dict(raw) if isinstance(raw, dict) else {}


def load_fixed_now_ms(path: Path = SCENARIOS_PATH) -> int:
    return _coerce_int(_load_payload(path).get("fixed_now_ms"), 1_700_000_060_000)


def load_epoch_expired_now_ms(path: Path = SCENARIOS_PATH) -> int:
    return _coerce_int(_load_payload(path).get("epoch_expired_now_ms"), 1_700_000_305_000)


def _stats_from_raw(raw: object) -> EffectiveStats:
    stats = EffectiveStats()
    if not isinstance(raw, dict):
        return stats
    for key, value in raw.items():
        if hasattr(stats, key):
            setattr(stats, key, float(value))
    return stats


@dataclass(frozen=True)
class TransitionToolFixture:
    wire_name: str
    entity_id: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class TransitionSkillFixture:
    entity_id: str
    doc_id: str
    description: str = ""


@dataclass(frozen=True)
class McpServerToolFixture:
    wire_name: str
    entity_id: str
    mcp_server: str
    tool_name: str
    description: str


@dataclass(frozen=True)
class SlowClockScenario:
    id: str
    kind: str
    entity_id: str
    stable_tier: Tier
    effective_tier: Tier
    stats: dict[str, float]
    config_overrides: dict[str, Any]
    expected_stable_tier: Tier
    expected_reason: str


@dataclass(frozen=True)
class FastHotScenario:
    id: str
    kind: str
    entity_id: str
    stable_tier: Tier
    effective_tier: Tier
    stats: dict[str, float]
    wake_cycle_id: int
    sleep_cooldown_until_cycle: int
    expected_effective_tier: Tier
    expected_stable_tier: Tier
    expected_reason: str
    expect_temp_promotion: bool
    use_optional_promotion: bool


@dataclass(frozen=True)
class TempExpiryScenario:
    id: str
    kind: str
    entity_id: str
    stable_tier: Tier
    effective_tier: Tier
    overlap_tier: Tier | None
    temp_promotion_until_ms: int
    stats: dict[str, float]
    now_ms: int
    expected_stable_tier: Tier
    expected_reason: str


@dataclass(frozen=True)
class ManagerIntegrationScenario:
    id: str
    path: str
    kind: str
    entity_id: str
    seed_tier: Tier
    stats: dict[str, float]
    expected_stable_tier: Tier | None = None
    expected_reason: str | None = None
    expected_after_fast_effective_tier: Tier | None = None
    expected_after_fast_stable_tier: Tier | None = None
    expected_after_epoch_stable_tier: Tier | None = None
    expected_epoch_reason: str | None = None


@dataclass(frozen=True)
class GherkinTransitionScenario:
    id: str
    path: str
    kind: str
    entity_id: str
    seed_tier: Tier
    stats: dict[str, float]
    expected_stable_tier: Tier | None = None
    expected_effective_tier: Tier | None = None
    expected_reason: str | None = None
    expected_after_epoch_stable_tier: Tier | None = None
    user_prompt_key: str | None = None
    mcp_server_batch: bool = False
    mcp_server: str | None = None
    expected_mcp_server_stable_tier: Tier | None = None
    config_overrides: dict[str, Any] | None = None


@dataclass(frozen=True)
class TierTransitionsFixturePack:
    workspace: Path
    db_path: Path
    config: dict[str, Any]
    tool: TransitionToolFixture
    skill: TransitionSkillFixture
    fixed_now_ms: int
    epoch_expired_now_ms: int


def load_tool_fixture(path: Path = SCENARIOS_PATH) -> TransitionToolFixture:
    raw = _load_payload(path).get("tool")
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: missing tool fixture")
    return TransitionToolFixture(
        wire_name=str(raw.get("wire_name", "gitnexus_query")),
        entity_id=str(raw.get("entity_id", "cyt_mcp:gitnexus_query")),
        input_schema=dict(raw.get("input_schema") or {}),
    )


def load_skill_fixture(path: Path = SCENARIOS_PATH) -> TransitionSkillFixture:
    raw = _load_payload(path).get("skill")
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: missing skill fixture")
    return TransitionSkillFixture(
        entity_id=str(raw.get("entity_id", "skill:create-hook")),
        doc_id=str(raw.get("doc_id", "create-hook")),
        description=str(raw.get("description", "")),
    )


def load_fast_wake_prompts(path: Path = SCENARIOS_PATH) -> dict[str, str]:
    raw = _load_payload(path).get("fast_wake_prompts")
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: missing fast_wake_prompts")
    return {str(key): str(value) for key, value in raw.items()}


def load_mcp_server_tools(path: Path = SCENARIOS_PATH) -> tuple[McpServerToolFixture, ...]:
    rows = _load_payload(path).get("mcp_server_tools", [])
    fixtures: list[McpServerToolFixture] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        fixtures.append(
            McpServerToolFixture(
                wire_name=str(row.get("wire_name", "")),
                entity_id=str(row.get("entity_id", "")),
                mcp_server=str(row.get("mcp_server", "")),
                tool_name=str(row.get("tool_name", "")),
                description=str(row.get("description", "")),
            ),
        )
    return tuple(fixtures)


def _slow_clock_scenario(row: dict[str, Any]) -> SlowClockScenario:
    return SlowClockScenario(
        id=str(row["id"]),
        kind=str(row.get("kind", "tool")),
        entity_id=str(row["entity_id"]),
        stable_tier=parse_tier(row.get("stable_tier")),
        effective_tier=parse_tier(row.get("effective_tier", row.get("stable_tier"))),
        stats={str(k): float(v) for k, v in dict(row.get("stats") or {}).items()},
        config_overrides=dict(row.get("config_overrides") or {}),
        expected_stable_tier=parse_tier(row.get("expected_stable_tier")),
        expected_reason=str(row.get("expected_reason", "")),
    )


def _fast_hot_scenario(row: dict[str, Any]) -> FastHotScenario:
    return FastHotScenario(
        id=str(row["id"]),
        kind=str(row.get("kind", "tool")),
        entity_id=str(row["entity_id"]),
        stable_tier=parse_tier(row.get("stable_tier")),
        effective_tier=parse_tier(row.get("effective_tier", row.get("stable_tier"))),
        stats={str(k): float(v) for k, v in dict(row.get("stats") or {}).items()},
        wake_cycle_id=_coerce_int(row.get("wake_cycle_id"), 1),
        sleep_cooldown_until_cycle=_coerce_int(row.get("sleep_cooldown_until_cycle"), 0),
        expected_effective_tier=parse_tier(row.get("expected_effective_tier")),
        expected_stable_tier=parse_tier(row.get("expected_stable_tier")),
        expected_reason=str(row.get("expected_reason", "")),
        expect_temp_promotion=bool(row.get("expect_temp_promotion", False)),
        use_optional_promotion=bool(row.get("use_optional_promotion", False)),
    )


def _temp_expiry_scenario(row: dict[str, Any]) -> TempExpiryScenario:
    overlap = row.get("overlap_tier")
    return TempExpiryScenario(
        id=str(row["id"]),
        kind=str(row.get("kind", "tool")),
        entity_id=str(row["entity_id"]),
        stable_tier=parse_tier(row.get("stable_tier")),
        effective_tier=parse_tier(row.get("effective_tier", row.get("stable_tier"))),
        overlap_tier=parse_tier(overlap) if overlap is not None else None,
        temp_promotion_until_ms=int(row.get("temp_promotion_until_ms", 0)),
        stats={str(k): float(v) for k, v in dict(row.get("stats") or {}).items()},
        now_ms=int(row.get("now_ms", 0)),
        expected_stable_tier=parse_tier(row["expected_stable_tier"]),
        expected_reason=str(row.get("expected_reason", "")),
    )


def _manager_integration_scenario(row: dict[str, Any]) -> ManagerIntegrationScenario:
    return ManagerIntegrationScenario(
        id=str(row["id"]),
        path=str(row.get("path", "slow")),
        kind=str(row.get("kind", "tool")),
        entity_id=str(row["entity_id"]),
        seed_tier=parse_tier(row.get("seed_tier")),
        stats={str(k): float(v) for k, v in dict(row.get("stats") or {}).items()},
        expected_stable_tier=parse_tier(row["expected_stable_tier"])
        if row.get("expected_stable_tier") is not None
        else None,
        expected_reason=str(row["expected_reason"]) if row.get("expected_reason") else None,
        expected_after_fast_effective_tier=parse_tier(row["expected_after_fast_effective_tier"])
        if row.get("expected_after_fast_effective_tier") is not None
        else None,
        expected_after_fast_stable_tier=parse_tier(row["expected_after_fast_stable_tier"])
        if row.get("expected_after_fast_stable_tier") is not None
        else None,
        expected_after_epoch_stable_tier=parse_tier(row["expected_after_epoch_stable_tier"])
        if row.get("expected_after_epoch_stable_tier") is not None
        else None,
        expected_epoch_reason=str(row["expected_epoch_reason"])
        if row.get("expected_epoch_reason")
        else None,
    )


def _gherkin_scenario(row: dict[str, Any]) -> GherkinTransitionScenario:
    return GherkinTransitionScenario(
        id=str(row["id"]),
        path=str(row.get("path", "slow")),
        kind=str(row.get("kind", "tool")),
        entity_id=str(row["entity_id"]),
        seed_tier=parse_tier(row.get("seed_tier")),
        stats={str(k): float(v) for k, v in dict(row.get("stats") or {}).items()},
        expected_stable_tier=parse_tier(row["expected_stable_tier"])
        if row.get("expected_stable_tier") is not None
        else None,
        expected_effective_tier=parse_tier(row["expected_effective_tier"])
        if row.get("expected_effective_tier") is not None
        else None,
        expected_reason=str(row["expected_reason"]) if row.get("expected_reason") else None,
        expected_after_epoch_stable_tier=parse_tier(row["expected_after_epoch_stable_tier"])
        if row.get("expected_after_epoch_stable_tier") is not None
        else None,
        user_prompt_key=str(row["user_prompt_key"]) if row.get("user_prompt_key") else None,
        mcp_server_batch=bool(row.get("mcp_server_batch", False)),
        mcp_server=str(row["mcp_server"]) if row.get("mcp_server") else None,
        expected_mcp_server_stable_tier=parse_tier(row["expected_mcp_server_stable_tier"])
        if row.get("expected_mcp_server_stable_tier") is not None
        else None,
        config_overrides=dict(row.get("config_overrides") or {}) or None,
    )


def load_slow_clock_scenarios(path: Path = SCENARIOS_PATH) -> tuple[SlowClockScenario, ...]:
    rows = _load_payload(path).get("slow_clock", [])
    return tuple(_slow_clock_scenario(row) for row in rows if isinstance(row, dict))


def load_fast_hot_scenarios(path: Path = SCENARIOS_PATH) -> tuple[FastHotScenario, ...]:
    rows = _load_payload(path).get("fast_hot", [])
    return tuple(_fast_hot_scenario(row) for row in rows if isinstance(row, dict))


def load_temp_expiry_scenarios(path: Path = SCENARIOS_PATH) -> tuple[TempExpiryScenario, ...]:
    rows = _load_payload(path).get("temp_expiry", [])
    return tuple(_temp_expiry_scenario(row) for row in rows if isinstance(row, dict))


def load_manager_integration_scenarios(
    path: Path = SCENARIOS_PATH,
) -> tuple[ManagerIntegrationScenario, ...]:
    rows = _load_payload(path).get("manager_integration", [])
    return tuple(_manager_integration_scenario(row) for row in rows if isinstance(row, dict))


def load_gherkin_scenarios(path: Path = SCENARIOS_PATH) -> tuple[GherkinTransitionScenario, ...]:
    rows = _load_payload(path).get("gherkin", [])
    return tuple(_gherkin_scenario(row) for row in rows if isinstance(row, dict))


def gherkin_scenario_by_id(scenario_id: str, path: Path = SCENARIOS_PATH) -> GherkinTransitionScenario:
    for scenario in load_gherkin_scenarios(path):
        if scenario.id == scenario_id:
            return scenario
    raise KeyError(scenario_id)


def tier_transitions_config(
    pack: TierTransitionsFixturePack,
    *,
    kind: str = "tool",
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = dict(pack.config)
    section_key = "tools" if kind == "tool" else "skills"
    section = dict(config.get(section_key) or {})
    tiers = dict(section.get("tiers") or {})
    tiers["database"] = {
        "path": str(pack.db_path),
        "disk_flush_seconds": float(load_config_values().get("disk_flush_seconds", 0)),
    }
    if overrides:
        tiers.update(overrides)
    section["tiers"] = tiers
    config[section_key] = section
    if kind == "skill" and "tools" not in config:
        config["tools"] = {
            "tiers": {
                "mode": "live",
                "database": {"path": str(pack.db_path), "disk_flush_seconds": 0},
            },
        }
    return config


def materialize_transitions_pack(tmp_path: Path, *, path: Path = SCENARIOS_PATH) -> TierTransitionsFixturePack:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    db_path = workspace / "tier_state.db"
    config_values = load_config_values(path)
    config = {
        "tools": {
            "tiers": {
                "mode": "live",
                "database": {
                    "path": str(db_path),
                    "disk_flush_seconds": float(config_values.get("disk_flush_seconds", 0)),
                },
                "cache_epoch": {
                    "prompt_cache_ttl_minutes": config_values.get("prompt_cache_ttl_minutes", 5.0),
                    "ttl_multiplier": config_values.get("ttl_multiplier", 1.0),
                    "idle_gap_triggers_epoch": config_values.get("idle_gap_triggers_epoch", True),
                },
                "evaluation": {
                    "min_injections_before_reconsider": int(
                        config_values.get("min_injections_before_reconsider", 2),
                    ),
                },
                "temp_promotion_turns": int(config_values.get("temp_promotion_turns", 3)),
            },
        },
        "skills": {
            "enabled": True,
            "tiers": {
                "mode": "live",
                "database": {"path": str(db_path), "disk_flush_seconds": 0},
            },
        },
    }
    return TierTransitionsFixturePack(
        workspace=workspace,
        db_path=db_path,
        config=config,
        tool=load_tool_fixture(path),
        skill=load_skill_fixture(path),
        fixed_now_ms=load_fixed_now_ms(path),
        epoch_expired_now_ms=load_epoch_expired_now_ms(path),
    )


def entity_state_from_scenario(
    *,
    kind: str,
    entity_id: str,
    stable_tier: Tier,
    effective_tier: Tier | None = None,
    stats: dict[str, float] | None = None,
    sleep_cooldown_until_cycle: int = 0,
    wake_lease_until_cycle: int = 0,
) -> EntityTierState:
    state = EntityTierState(
        entity_id=entity_id,
        kind=kind,
        stable_tier=stable_tier,
        effective_tier=effective_tier or stable_tier,
        sleep_cooldown_until_cycle=sleep_cooldown_until_cycle,
        wake_lease_until_cycle=wake_lease_until_cycle,
        stats=_stats_from_raw(stats or {}),
    )
    return state


def seed_entity_state(
    pack: TierTransitionsFixturePack,
    state: EntityTierState,
    *,
    epoch: EpochState | None = None,
) -> int:
    store = TierStore.open(str(pack.db_path))
    try:
        project_id = store.get_or_create_project(str(pack.workspace))
        project = TierProject(project_id=project_id, root_path=pack.workspace)
        if epoch is not None:
            store.save_epoch_state(project, epoch)
        store.upsert_entity_state(project, state)
        return project_id
    finally:
        store.close()


def tool_dict_for_pack(pack: TierTransitionsFixturePack) -> dict[str, Any]:
    return stamp_tool_catalog_source(
        {
            "name": pack.tool.wire_name,
            "cyt_catalog_source": "cyt_mcp",
            "inputSchema": dict(pack.tool.input_schema),
        },
    )


def mcp_server_tool_dict(fixture: McpServerToolFixture) -> dict[str, Any]:
    return stamp_tool_catalog_source(
        {
            "name": fixture.wire_name,
            "cyt_catalog_source": "cyt_mcp",
            "mcp_server": fixture.mcp_server,
            "tool_name": fixture.tool_name,
            "description": fixture.description,
            "inputSchema": {"type": "object"},
        },
    )


def skill_metadata_for_pack(pack: TierTransitionsFixturePack) -> dict[str, Any]:
    return {
        "name": pack.skill.doc_id,
        "description": pack.skill.description or pack.skill.doc_id,
        "doc_id": pack.skill.doc_id,
    }


def expire_epoch_on_manager(manager: Any, *, now_ms: int) -> None:
    ttl_ms = 5 * 60 * 1000
    manager._epoch.epoch_start_ms = now_ms - ttl_ms - 1
    manager._epoch.last_request_ms = now_ms - ttl_ms - 1


def latest_epoch_log_reasons(pack: TierTransitionsFixturePack) -> list[str]:
    store = TierStore.open(str(pack.db_path))
    try:
        project_id = store.get_or_create_project(str(pack.workspace))
        row = store._conn.execute(
            "SELECT transitions_json FROM epoch_log WHERE project_id = ? ORDER BY ts_ms DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if row is None:
            return []
        payload = json.loads(str(row[0]))
        if not isinstance(payload, list):
            return []
        return [str(item.get("reason", "")) for item in payload if isinstance(item, dict)]
    finally:
        store.close()


def manager_state(
    manager: Any,
    *,
    kind: str,
    entity_id: str,
) -> EntityTierState | None:
    entity_kind = EntityKind.TOOL if kind == "tool" else EntityKind.SKILL
    return manager._states.get((entity_kind, entity_id))


__all__ = [
    "FastHotScenario",
    "GherkinTransitionScenario",
    "ManagerIntegrationScenario",
    "SlowClockScenario",
    "TierTransitionsFixturePack",
    "TransitionSkillFixture",
    "TransitionToolFixture",
    "entity_state_from_scenario",
    "expire_epoch_on_manager",
    "gherkin_scenario_by_id",
    "latest_epoch_log_reasons",
    "load_config_values",
    "load_epoch_expired_now_ms",
    "load_fast_hot_scenarios",
    "load_fixed_now_ms",
    "load_gherkin_scenarios",
    "load_manager_integration_scenarios",
    "load_slow_clock_scenarios",
    "manager_state",
    "materialize_transitions_pack",
    "seed_entity_state",
    "tier_transitions_config",
    "tool_dict_for_pack",
]
