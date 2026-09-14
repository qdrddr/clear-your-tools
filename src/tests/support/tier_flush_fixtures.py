"""Shared fixture loader for tier statistics disk persistence tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.tiers.adapters.tools import stamp_tool_catalog_source
from cyt.tiers.manager import TierManager
from cyt.tiers.models import (
    EffectiveStats,
    EntityKind,
    EntityTierState,
    EpochState,
    Tier,
    TierProject,
)
from cyt.tiers.store import TierStore

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tier_flush"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"

_TIER_BY_NAME = {
    "DORMANT": Tier.DORMANT,
    "COLD": Tier.COLD,
    "ACTIVE": Tier.ACTIVE,
    "HOT": Tier.HOT,
    "EXTRA_HOT": Tier.EXTRA_HOT,
}


@dataclass(frozen=True)
class FlushToolFixture:
    wire_name: str
    entity_id: str
    mcp_server: str
    bare_tool_name: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class RecordStep:
    success: bool
    args: dict[str, Any] | None


@dataclass(frozen=True)
class RecordScenario:
    id: str
    disk_flush_seconds: float
    steps: tuple[RecordStep, ...]
    expected: dict[str, float]
    persisted_before_flush: bool
    persisted_after_flush: bool


@dataclass(frozen=True)
class HttpPayloadScenario:
    id: str
    payload: dict[str, Any]
    expected: dict[str, float]


@dataclass(frozen=True)
class IntegrationFlushScenario:
    id: str
    disk_flush_seconds: float
    expected: dict[str, float]
    uses_seed: bool = False
    payload_id: str | None = None
    persisted_before_flush: bool | None = None
    persisted_after_flush: bool | None = None
    disable_cache: bool = False


@dataclass(frozen=True)
class TierFlushFixturePack:
    workspace: Path
    tier_db_path: Path
    config: dict[str, Any]
    tool: FlushToolFixture


def _load_payload(path: Path = SCENARIOS_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def load_flush_config_values(path: Path = SCENARIOS_PATH) -> dict[str, float]:
    raw = _load_payload(path).get("config")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        if isinstance(value, (int, float)):
            out[str(key)] = float(value)
    return out


def load_flush_tool(path: Path = SCENARIOS_PATH) -> FlushToolFixture:
    raw = _load_payload(path).get("tool")
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected tool object")
    schema = raw.get("input_schema")
    if not isinstance(schema, dict):
        raise ValueError(f"{path}: expected tool.input_schema object")
    return FlushToolFixture(
        wire_name=str(raw["wire_name"]),
        entity_id=str(raw["entity_id"]),
        mcp_server=str(raw["mcp_server"]),
        bare_tool_name=str(raw["bare_tool_name"]),
        input_schema=dict(schema),
    )


def load_record_scenarios(path: Path = SCENARIOS_PATH) -> tuple[RecordScenario, ...]:
    scenarios: list[RecordScenario] = []
    for row in _load_payload(path).get("record_scenarios", []):
        if not isinstance(row, dict):
            continue
        steps: list[RecordStep] = []
        for step in row.get("steps", []):
            if not isinstance(step, dict):
                continue
            args_raw = step.get("args")
            steps.append(
                RecordStep(
                    success=step.get("success") is not False,
                    args=dict(args_raw) if isinstance(args_raw, dict) else None,
                ),
            )
        expected_raw = row.get("expected")
        if not isinstance(expected_raw, dict):
            expected_raw = {}
        scenarios.append(
            RecordScenario(
                id=str(row["id"]),
                disk_flush_seconds=float(row.get("disk_flush_seconds", 900)),
                steps=tuple(steps),
                expected={str(k): float(v) for k, v in expected_raw.items()},
                persisted_before_flush=row.get("persisted_before_flush") is True,
                persisted_after_flush=row.get("persisted_after_flush") is not False,
            ),
        )
    return tuple(scenarios)


def load_http_payloads(path: Path = SCENARIOS_PATH) -> tuple[HttpPayloadScenario, ...]:
    scenarios: list[HttpPayloadScenario] = []
    for row in _load_payload(path).get("http_payloads", []):
        if not isinstance(row, dict):
            continue
        payload_raw = row.get("payload")
        if not isinstance(payload_raw, dict):
            continue
        expected_raw = row.get("expected")
        if not isinstance(expected_raw, dict):
            expected_raw = {}
        scenarios.append(
            HttpPayloadScenario(
                id=str(row["id"]),
                payload=dict(payload_raw),
                expected={str(k): float(v) for k, v in expected_raw.items()},
            ),
        )
    return tuple(scenarios)


def load_integration_flush_scenarios(
    path: Path = SCENARIOS_PATH,
) -> tuple[IntegrationFlushScenario, ...]:
    config_values = load_flush_config_values(path)
    scenarios: list[IntegrationFlushScenario] = []
    for row in _load_payload(path).get("integration", []):
        if not isinstance(row, dict):
            continue
        expected_raw = row.get("expected")
        if not isinstance(expected_raw, dict):
            expected_raw = {}
        disk_flush = row.get("disk_flush_seconds")
        if disk_flush is None:
            disk_flush = config_values.get("deferred_flush_seconds", 900.0)
        scenarios.append(
            IntegrationFlushScenario(
                id=str(row["id"]),
                disk_flush_seconds=float(disk_flush),
                expected={str(k): float(v) for k, v in expected_raw.items()},
                uses_seed=row.get("uses_seed") is True,
                payload_id=str(row["payload_id"]) if row.get("payload_id") else None,
                persisted_before_flush=(
                    bool(row["persisted_before_flush"]) if "persisted_before_flush" in row else None
                ),
                persisted_after_flush=(
                    bool(row["persisted_after_flush"]) if "persisted_after_flush" in row else None
                ),
                disable_cache=row.get("disable_cache") is True,
            ),
        )
    return tuple(scenarios)


def http_payload_by_id(payload_id: str, path: Path = SCENARIOS_PATH) -> HttpPayloadScenario:
    for scenario in load_http_payloads(path):
        if scenario.id == payload_id:
            return scenario
    raise KeyError(f"unknown tier flush http payload id: {payload_id}")


def record_scenario_by_id(scenario_id: str, path: Path = SCENARIOS_PATH) -> RecordScenario:
    for scenario in load_record_scenarios(path):
        if scenario.id == scenario_id:
            return scenario
    raise KeyError(f"unknown tier flush record scenario id: {scenario_id}")


def _tier_value(raw: object, *, default: Tier = Tier.ACTIVE) -> Tier:
    if isinstance(raw, str):
        return _TIER_BY_NAME.get(raw.strip().upper(), default)
    return default


def _stats_from_raw(raw: object) -> EffectiveStats:
    if not isinstance(raw, dict):
        return EffectiveStats()
    stats = EffectiveStats()
    for key in (
        "candidates",
        "injected",
        "used",
        "attempts",
        "used_without_injection",
        "optional_used",
        "shadow_hits",
        "shadow_evaluations",
    ):
        value = raw.get(key)
        if isinstance(value, (int, float)):
            setattr(stats, key, float(value))
    return stats


def seed_tier_flush_db(pack: TierFlushFixturePack, path: Path = SCENARIOS_PATH) -> int:
    """Insert fixture seed tier states and epoch metadata."""
    seed_raw = _load_payload(path).get("seed_states")
    if not isinstance(seed_raw, dict):
        return TierStore.open(str(pack.tier_db_path)).get_or_create_project(str(pack.workspace))

    store = TierStore.open(str(pack.tier_db_path))
    try:
        project_id = store.get_or_create_project(str(pack.workspace))
        project = TierProject(project_id=project_id, root_path=pack.workspace)
        epoch_raw = seed_raw.get("epoch")
        if isinstance(epoch_raw, dict):
            store.save_epoch_state(
                project,
                EpochState(
                    epoch_id=int(epoch_raw.get("epoch_id", 0)),
                    epoch_start_ms=int(epoch_raw.get("epoch_start_ms", 0)),
                    last_request_ms=int(epoch_raw.get("last_request_ms", 0)),
                    wake_cycle_id=int(epoch_raw.get("wake_cycle_id", epoch_raw.get("session_id", 0))),
                ),
            )
        tools_raw = seed_raw.get("tools")
        if isinstance(tools_raw, dict):
            for entity_id, row in tools_raw.items():
                if not isinstance(row, dict):
                    continue
                store.upsert_entity_state(
                    project,
                    EntityTierState(
                        entity_id=str(entity_id),
                        kind=EntityKind.TOOL,
                        stable_tier=_tier_value(row.get("stable_tier")),
                        effective_tier=_tier_value(
                            row.get("effective_tier"),
                            default=_tier_value(row.get("stable_tier")),
                        ),
                        stats=_stats_from_raw(row.get("stats")),
                    ),
                )
        return project_id
    finally:
        store.close()


def tier_flush_config(
    pack: TierFlushFixturePack,
    *,
    disk_flush_seconds: float,
) -> dict[str, Any]:
    config = dict(pack.config)
    tools = dict(config.get("tools") or {})
    tiers = dict(tools.get("tiers") or {})
    tiers["database"] = {
        "path": str(pack.tier_db_path),
        "disk_flush_seconds": disk_flush_seconds,
    }
    tools["tiers"] = tiers
    config["tools"] = tools
    return config


def materialize_flush_pack(
    tmp_path: Path,
    *,
    seed: bool = False,
    disk_flush_seconds: float | None = None,
    path: Path = SCENARIOS_PATH,
) -> TierFlushFixturePack:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    tier_db_path = workspace / "tier_state.db"
    tool = load_flush_tool(path)
    config_values = load_flush_config_values(path)
    flush_seconds = (
        float(disk_flush_seconds)
        if disk_flush_seconds is not None
        else config_values.get("deferred_flush_seconds", 900.0)
    )
    config = set_hook_workspace_in_config(
        {
            "cache": {"enabled": True},
            "tools": {
                "tiers": {
                    "mode": "shadow",
                    "database": {
                        "path": str(tier_db_path),
                        "disk_flush_seconds": flush_seconds,
                    },
                },
            },
            "pruning": {
                "tools": {
                    "hook": {
                        "tools_from": ["cyt_mcp"],
                    },
                },
            },
        },
        workspace,
    )
    pack = TierFlushFixturePack(
        workspace=workspace,
        tier_db_path=tier_db_path,
        config=config,
        tool=tool,
    )
    if seed:
        seed_tier_flush_db(pack, path)
    return pack


def flush_tool_dict(tool: FlushToolFixture | None = None) -> dict[str, Any]:
    tool = tool or load_flush_tool()
    return stamp_tool_catalog_source(
        {
            "name": tool.wire_name,
            "cyt_catalog_source": "cyt_mcp",
            "inputSchema": dict(tool.input_schema),
        },
    )


def manager_for_pack(pack: TierFlushFixturePack) -> TierManager:
    return TierManager(pack.workspace, str(pack.tier_db_path))


def reload_manager_from_disk(pack: TierFlushFixturePack) -> TierManager:
    return TierManager(pack.workspace, str(pack.tier_db_path))


def entity_state_on_disk(
    pack: TierFlushFixturePack,
    *,
    entity_id: str | None = None,
) -> EntityTierState | None:
    entity_id = entity_id or pack.tool.entity_id
    manager = reload_manager_from_disk(pack)
    try:
        return manager._states.get((EntityKind.TOOL, entity_id))
    finally:
        manager.close()


def apply_record_scenario(
    manager: TierManager,
    pack: TierFlushFixturePack,
    scenario: RecordScenario,
) -> None:
    config = tier_flush_config(pack, disk_flush_seconds=scenario.disk_flush_seconds)
    tool = flush_tool_dict(pack.tool)
    for step in scenario.steps:
        manager.record_tool_attempt(
            tool,
            config=config,
            success=step.success,
        )


async def post_tier_feedback_http(
    pack: TierFlushFixturePack,
    payload: dict[str, Any],
    *,
    disk_flush_seconds: float,
) -> int:
    from cyt.hook.http_server import hook_tier_feedback

    config = tier_flush_config(pack, disk_flush_seconds=disk_flush_seconds)
    body = dict(payload)
    body.setdefault("workspace_root", str(pack.workspace))
    request = MagicMock()
    request.client = MagicMock(host="127.0.0.1")
    request.body = AsyncMock(return_value=json.dumps(body).encode())
    request.app = MagicMock()
    request.app.state.cyt_config = config
    with patch("cyt.hook.http_server._is_localhost_request", return_value=True):
        response = await hook_tier_feedback(request)
    return int(response.status_code)


def warm_tier_statistics(config: dict[str, Any]) -> None:
    from cyt.cache.warm import _warm_tier_statistics

    _warm_tier_statistics(config)


__all__ = [
    "FlushToolFixture",
    "HttpPayloadScenario",
    "IntegrationFlushScenario",
    "RecordScenario",
    "RecordStep",
    "TierFlushFixturePack",
    "apply_record_scenario",
    "entity_state_on_disk",
    "flush_tool_dict",
    "http_payload_by_id",
    "load_flush_config_values",
    "load_flush_tool",
    "load_http_payloads",
    "load_integration_flush_scenarios",
    "load_record_scenarios",
    "manager_for_pack",
    "materialize_flush_pack",
    "post_tier_feedback_http",
    "record_scenario_by_id",
    "reload_manager_from_disk",
    "seed_tier_flush_db",
    "tier_flush_config",
    "warm_tier_statistics",
]
