"""Shared fixture loader for wake-cycle and epoch timing tier tests."""

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

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tier_wake_cycle"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"

_TIER_BY_NAME = {
    "DORMANT": Tier.DORMANT,
    "COLD": Tier.COLD,
    "ACTIVE": Tier.ACTIVE,
    "HOT": Tier.HOT,
    "EXTRA_HOT": Tier.EXTRA_HOT,
}


@dataclass(frozen=True)
class WakeCycleToolFixture:
    wire_name: str
    entity_id: str
    mcp_server: str
    bare_tool_name: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class EpochTimingScenario:
    id: str
    now_ms: int
    epoch_start_ms: int
    last_request_ms: int
    expected_remaining_ms: int
    expected_timeout_ms: int


@dataclass(frozen=True)
class DurationFormatScenario:
    id: str
    seconds: int
    expected: str


@dataclass(frozen=True)
class FastWakeScenario:
    id: str
    wake_cycle_id: int
    sleep_cooldown_until_cycle: int
    stats: dict[str, float]
    expected_tier: Tier
    expect_transition: bool


@dataclass(frozen=True)
class FastSleepScenario:
    id: str
    wake_cycle_id: int
    wake_lease_until_cycle: int
    had_selection: bool
    had_use: bool
    had_shadow: bool
    stats: dict[str, float]
    expected_tier: Tier
    expect_transition: bool
    expected_wake_lease_until_cycle: int | None = None


@dataclass(frozen=True)
class RequestCycleScenario:
    id: str
    initial_wake_cycle_id: int | None = None
    expected_after_begin: int | None = None
    persisted_to_disk: bool = False
    entity_id: str | None = None
    expected_tier_after_begin: Tier | None = None


@dataclass(frozen=True)
class HttpPayloadScenario:
    id: str
    payload: dict[str, Any]
    expected: dict[str, float]


@dataclass(frozen=True)
class WakeCycleIntegrationScenario:
    id: str
    initial_wake_cycle_id: int | None = None
    expected_after_prune: int | None = None
    payload_id: str | None = None
    expected_wake_cycle_id: int | None = None
    expected_epoch_id: int | None = None
    expected_timeout_seconds: int | None = None
    expected_remaining_seconds: int | None = None
    expected_after_begin: int | None = None


@dataclass(frozen=True)
class TierWakeCycleFixturePack:
    workspace: Path
    tier_db_path: Path
    config: dict[str, Any]
    tool: WakeCycleToolFixture
    fixed_now_ms: int


def _load_payload(path: Path = SCENARIOS_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


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


def load_wake_cycle_tool(path: Path = SCENARIOS_PATH) -> WakeCycleToolFixture:
    raw = _load_payload(path).get("tool")
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected tool object")
    schema = raw.get("input_schema")
    if not isinstance(schema, dict):
        raise ValueError(f"{path}: expected tool.input_schema object")
    return WakeCycleToolFixture(
        wire_name=str(raw["wire_name"]),
        entity_id=str(raw["entity_id"]),
        mcp_server=str(raw["mcp_server"]),
        bare_tool_name=str(raw["bare_tool_name"]),
        input_schema=dict(schema),
    )


def load_config_values(path: Path = SCENARIOS_PATH) -> dict[str, Any]:
    raw = _load_payload(path).get("config")
    if not isinstance(raw, dict):
        return {}
    return dict(raw)


def load_fixed_now_ms(path: Path = SCENARIOS_PATH) -> int:
    value = _load_payload(path).get("fixed_now_ms")
    if isinstance(value, int):
        return value
    return 1_700_000_060_000


def load_epoch_timing_scenarios(path: Path = SCENARIOS_PATH) -> tuple[EpochTimingScenario, ...]:
    scenarios: list[EpochTimingScenario] = []
    for row in _load_payload(path).get("epoch_timing", []):
        if not isinstance(row, dict):
            continue
        scenarios.append(
            EpochTimingScenario(
                id=str(row["id"]),
                now_ms=int(row["now_ms"]),
                epoch_start_ms=int(row["epoch_start_ms"]),
                last_request_ms=int(row["last_request_ms"]),
                expected_remaining_ms=int(row["expected_remaining_ms"]),
                expected_timeout_ms=int(row["expected_timeout_ms"]),
            ),
        )
    return tuple(scenarios)


def load_duration_format_scenarios(path: Path = SCENARIOS_PATH) -> tuple[DurationFormatScenario, ...]:
    scenarios: list[DurationFormatScenario] = []
    for row in _load_payload(path).get("duration_format", []):
        if not isinstance(row, dict):
            continue
        scenarios.append(
            DurationFormatScenario(
                id=str(row["id"]),
                seconds=int(row["seconds"]),
                expected=str(row["expected"]),
            ),
        )
    return tuple(scenarios)


def load_fast_wake_scenarios(path: Path = SCENARIOS_PATH) -> tuple[FastWakeScenario, ...]:
    scenarios: list[FastWakeScenario] = []
    for row in _load_payload(path).get("fast_wake", []):
        if not isinstance(row, dict):
            continue
        stats_raw = row.get("stats")
        stats = (
            {str(k): float(v) for k, v in stats_raw.items()}
            if isinstance(stats_raw, dict)
            else {}
        )
        scenarios.append(
            FastWakeScenario(
                id=str(row["id"]),
                wake_cycle_id=int(row["wake_cycle_id"]),
                sleep_cooldown_until_cycle=int(row.get("sleep_cooldown_until_cycle", 0)),
                stats=stats,
                expected_tier=_tier_value(row.get("expected_tier")),
                expect_transition=row.get("expect_transition") is not False,
            ),
        )
    return tuple(scenarios)


def load_fast_sleep_scenarios(path: Path = SCENARIOS_PATH) -> tuple[FastSleepScenario, ...]:
    scenarios: list[FastSleepScenario] = []
    for row in _load_payload(path).get("fast_sleep", []):
        if not isinstance(row, dict):
            continue
        stats_raw = row.get("stats")
        stats = (
            {str(k): float(v) for k, v in stats_raw.items()}
            if isinstance(stats_raw, dict)
            else {}
        )
        expected_lease = row.get("expected_wake_lease_until_cycle")
        scenarios.append(
            FastSleepScenario(
                id=str(row["id"]),
                wake_cycle_id=int(row["wake_cycle_id"]),
                wake_lease_until_cycle=int(row.get("wake_lease_until_cycle", 0)),
                had_selection=row.get("had_selection") is True,
                had_use=row.get("had_use") is True,
                had_shadow=row.get("had_shadow") is True,
                stats=stats,
                expected_tier=_tier_value(row.get("expected_tier")),
                expect_transition=row.get("expect_transition") is not False,
                expected_wake_lease_until_cycle=(
                    int(expected_lease) if isinstance(expected_lease, int) else None
                ),
            ),
        )
    return tuple(scenarios)


def load_request_cycle_scenarios(path: Path = SCENARIOS_PATH) -> tuple[RequestCycleScenario, ...]:
    scenarios: list[RequestCycleScenario] = []
    for row in _load_payload(path).get("request_cycle", []):
        if not isinstance(row, dict):
            continue
        initial = row.get("initial_wake_cycle_id")
        expected_after = row.get("expected_after_begin")
        tier_raw = row.get("expected_tier_after_begin")
        scenarios.append(
            RequestCycleScenario(
                id=str(row["id"]),
                initial_wake_cycle_id=int(initial) if isinstance(initial, int) else None,
                expected_after_begin=int(expected_after) if isinstance(expected_after, int) else None,
                persisted_to_disk=row.get("persisted_to_disk") is True,
                entity_id=str(row["entity_id"]) if row.get("entity_id") else None,
                expected_tier_after_begin=(
                    _tier_value(tier_raw) if isinstance(tier_raw, str) else None
                ),
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


def http_payload_by_id(payload_id: str, path: Path = SCENARIOS_PATH) -> HttpPayloadScenario:
    for scenario in load_http_payloads(path):
        if scenario.id == payload_id:
            return scenario
    raise KeyError(f"unknown wake-cycle http payload id: {payload_id}")


def load_integration_scenarios(path: Path = SCENARIOS_PATH) -> tuple[WakeCycleIntegrationScenario, ...]:
    scenarios: list[WakeCycleIntegrationScenario] = []
    for row in _load_payload(path).get("integration", []):
        if not isinstance(row, dict):
            continue
        scenarios.append(
            WakeCycleIntegrationScenario(
                id=str(row["id"]),
                initial_wake_cycle_id=(
                    int(row["initial_wake_cycle_id"])
                    if isinstance(row.get("initial_wake_cycle_id"), int)
                    else None
                ),
                expected_after_prune=(
                    int(row["expected_after_prune"])
                    if isinstance(row.get("expected_after_prune"), int)
                    else None
                ),
                payload_id=str(row["payload_id"]) if row.get("payload_id") else None,
                expected_wake_cycle_id=(
                    int(row["expected_wake_cycle_id"])
                    if isinstance(row.get("expected_wake_cycle_id"), int)
                    else None
                ),
                expected_epoch_id=(
                    int(row["expected_epoch_id"])
                    if isinstance(row.get("expected_epoch_id"), int)
                    else None
                ),
                expected_timeout_seconds=(
                    int(row["expected_timeout_seconds"])
                    if isinstance(row.get("expected_timeout_seconds"), int)
                    else None
                ),
                expected_remaining_seconds=(
                    int(row["expected_remaining_seconds"])
                    if isinstance(row.get("expected_remaining_seconds"), int)
                    else None
                ),
                expected_after_begin=(
                    int(row["expected_after_begin"])
                    if isinstance(row.get("expected_after_begin"), int)
                    else None
                ),
            ),
        )
    return tuple(scenarios)


def load_legacy_config_expectations(path: Path = SCENARIOS_PATH) -> dict[str, Any]:
    raw = _load_payload(path).get("config_legacy_keys")
    if not isinstance(raw, dict):
        return {}
    return dict(raw)


def seed_wake_cycle_db(pack: TierWakeCycleFixturePack, path: Path = SCENARIOS_PATH) -> int:
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
                    wake_cycle_id=int(
                        epoch_raw.get("wake_cycle_id", epoch_raw.get("session_id", 0)),
                    ),
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
                        wake_lease_until_cycle=int(row.get("wake_lease_until_cycle", 0)),
                        sleep_cooldown_until_cycle=int(row.get("sleep_cooldown_until_cycle", 0)),
                        stats=_stats_from_raw(row.get("stats")),
                    ),
                )
        return project_id
    finally:
        store.close()


def tier_wake_config(
    pack: TierWakeCycleFixturePack,
    *,
    disk_flush_seconds: float | None = None,
    wake_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = dict(pack.config)
    config_values = load_config_values()
    flush_seconds = (
        float(disk_flush_seconds)
        if disk_flush_seconds is not None
        else float(config_values.get("disk_flush_seconds", 900.0))
    )
    tools = dict(config.get("tools") or {})
    tiers = dict(tools.get("tiers") or {})
    tiers["database"] = {
        "path": str(pack.tier_db_path),
        "disk_flush_seconds": flush_seconds,
    }
    cache_epoch = {
        "prompt_cache_ttl_minutes": config_values.get("prompt_cache_ttl_minutes", 5.0),
        "ttl_multiplier": config_values.get("ttl_multiplier", 1.0),
        "idle_gap_triggers_epoch": config_values.get("idle_gap_triggers_epoch", True),
    }
    wake = dict(wake_overrides or {})
    if not wake:
        wake = {
            "lease_cycles": int(config_values.get("wake_lease_cycles", 2)),
            "cooldown_cycles": int(config_values.get("sleep_cooldown_cycles", 3)),
        }
    tiers["cache_epoch"] = cache_epoch
    tiers["wake"] = wake
    tools["tiers"] = tiers
    config["tools"] = tools
    return config


def materialize_wake_cycle_pack(
    tmp_path: Path,
    *,
    seed: bool = True,
    path: Path = SCENARIOS_PATH,
) -> TierWakeCycleFixturePack:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    tier_db_path = workspace / "tier_state.db"
    tool = load_wake_cycle_tool(path)
    config_values = load_config_values(path)
    config = set_hook_workspace_in_config(
        {
            "cache": {"enabled": True},
            "tools": {
                "tiers": {
                    "mode": "shadow",
                    "database": {
                        "path": str(tier_db_path),
                        "disk_flush_seconds": float(config_values.get("disk_flush_seconds", 900.0)),
                    },
                    "cache_epoch": {
                        "prompt_cache_ttl_minutes": config_values.get(
                            "prompt_cache_ttl_minutes",
                            5.0,
                        ),
                        "ttl_multiplier": config_values.get("ttl_multiplier", 1.0),
                        "idle_gap_triggers_epoch": config_values.get(
                            "idle_gap_triggers_epoch",
                            True,
                        ),
                    },
                    "wake": {
                        "lease_cycles": int(config_values.get("wake_lease_cycles", 2)),
                        "cooldown_cycles": int(config_values.get("sleep_cooldown_cycles", 3)),
                    },
                },
            },
            "pruning": {
                "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
                "tools": {
                    "hook": {"tools_from": ["cyt_mcp"]},
                    "sequence": ["bm25"],
                },
            },
        },
        workspace,
    )
    (workspace / "config.yaml").write_text(
        f"""
tools:
  tiers:
    mode: shadow
    database:
      path: {tier_db_path}
    cache_epoch:
      prompt_cache_ttl_minutes: {config_values.get("prompt_cache_ttl_minutes", 5.0)}
      ttl_multiplier: {config_values.get("ttl_multiplier", 1.0)}
      idle_gap_triggers_epoch: {str(config_values.get("idle_gap_triggers_epoch", True)).lower()}
    wake:
      lease_cycles: {int(config_values.get("wake_lease_cycles", 2))}
      cooldown_cycles: {int(config_values.get("sleep_cooldown_cycles", 3))}
skills:
  tiers:
    mode: shadow
""",
        encoding="utf-8",
    )

    pack = TierWakeCycleFixturePack(
        workspace=workspace,
        tier_db_path=tier_db_path,
        config=config,
        tool=tool,
        fixed_now_ms=load_fixed_now_ms(path),
    )
    if seed:
        seed_wake_cycle_db(pack, path)
    return pack


def wake_cycle_tool_dict(tool: WakeCycleToolFixture | None = None) -> dict[str, Any]:
    tool = tool or load_wake_cycle_tool()
    return stamp_tool_catalog_source(
        {
            "name": tool.wire_name,
            "cyt_catalog_source": "cyt_mcp",
            "inputSchema": dict(tool.input_schema),
        },
    )


def manager_for_pack(pack: TierWakeCycleFixturePack) -> TierManager:
    return TierManager(pack.workspace, str(pack.tier_db_path))


def epoch_on_disk(pack: TierWakeCycleFixturePack) -> EpochState:
    store = TierStore.open(str(pack.tier_db_path))
    try:
        project_id = store.get_or_create_project(str(pack.workspace))
        project = TierProject(project_id=project_id, root_path=pack.workspace)
        return store.load_epoch_state(project)
    finally:
        store.close()


async def post_wake_cycle_feedback_http(
    pack: TierWakeCycleFixturePack,
    payload: dict[str, Any],
    *,
    disk_flush_seconds: float | None = None,
) -> int:
    from cyt.hook.http_server import hook_tier_feedback

    config = tier_wake_config(pack, disk_flush_seconds=disk_flush_seconds)
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


__all__ = [
    "DurationFormatScenario",
    "EpochTimingScenario",
    "FastSleepScenario",
    "FastWakeScenario",
    "HttpPayloadScenario",
    "RequestCycleScenario",
    "TierWakeCycleFixturePack",
    "WakeCycleIntegrationScenario",
    "WakeCycleToolFixture",
    "epoch_on_disk",
    "http_payload_by_id",
    "load_config_values",
    "load_duration_format_scenarios",
    "load_epoch_timing_scenarios",
    "load_fast_sleep_scenarios",
    "load_fast_wake_scenarios",
    "load_fixed_now_ms",
    "load_http_payloads",
    "load_integration_scenarios",
    "load_legacy_config_expectations",
    "load_request_cycle_scenarios",
    "load_wake_cycle_tool",
    "manager_for_pack",
    "materialize_wake_cycle_pack",
    "post_wake_cycle_feedback_http",
    "seed_wake_cycle_db",
    "tier_wake_config",
    "wake_cycle_tool_dict",
]
