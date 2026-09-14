"""Shared fixture loader for cyt-mcp tool-use tier capture tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.tiers.adapters.tools import stamp_tool_catalog_source
from cyt.tiers.manager import TierManager
from cyt.tiers.models import EffectiveStats, EntityKind, EntityTierState, Tier

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tier_capture"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"


@dataclass(frozen=True)
class CaptureToolFixture:
    wire_name: str
    entity_id: str
    mcp_server: str
    bare_tool_name: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class AttemptStep:
    success: bool
    args: dict[str, Any] | None


@dataclass(frozen=True)
class AttemptSequenceScenario:
    id: str
    steps: tuple[AttemptStep, ...]
    expected: dict[str, Any]
    seed_injected: float | None


@dataclass(frozen=True)
class HttpPayloadScenario:
    id: str
    payload: dict[str, Any]
    expected: dict[str, Any]


@dataclass(frozen=True)
class IntegrationCaptureScenario:
    id: str
    payload_ids: tuple[str, ...]
    expected: dict[str, Any]


@dataclass(frozen=True)
class TierCaptureFixturePack:
    workspace: Path
    tier_db_path: Path
    examples_db_path: Path
    config: dict[str, Any]
    tool: CaptureToolFixture


def _load_payload(path: Path = SCENARIOS_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def load_capture_tool(path: Path = SCENARIOS_PATH) -> CaptureToolFixture:
    raw = _load_payload(path).get("tool")
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected tool object")
    schema = raw.get("input_schema")
    if not isinstance(schema, dict):
        raise ValueError(f"{path}: expected tool.input_schema object")
    return CaptureToolFixture(
        wire_name=str(raw["wire_name"]),
        entity_id=str(raw["entity_id"]),
        mcp_server=str(raw["mcp_server"]),
        bare_tool_name=str(raw["bare_tool_name"]),
        input_schema=dict(schema),
    )


def load_attempt_sequences(path: Path = SCENARIOS_PATH) -> tuple[AttemptSequenceScenario, ...]:
    scenarios: list[AttemptSequenceScenario] = []
    for row in _load_payload(path).get("attempt_sequences", []):
        if not isinstance(row, dict):
            continue
        steps: list[AttemptStep] = []
        for step in row.get("steps", []):
            if not isinstance(step, dict):
                continue
            args_raw = step.get("args")
            steps.append(
                AttemptStep(
                    success=step.get("success") is not False,
                    args=dict(args_raw) if isinstance(args_raw, dict) else None,
                ),
            )
        expected_raw = row.get("expected")
        if not isinstance(expected_raw, dict):
            expected_raw = {}
        seed_raw = row.get("seed_injected")
        scenarios.append(
            AttemptSequenceScenario(
                id=str(row["id"]),
                steps=tuple(steps),
                expected=dict(expected_raw),
                seed_injected=float(seed_raw) if seed_raw is not None else None,
            ),
        )
    return tuple(scenarios)


def load_http_payloads(path: Path = SCENARIOS_PATH) -> tuple[HttpPayloadScenario, ...]:
    tool = load_capture_tool(path)
    scenarios: list[HttpPayloadScenario] = []
    for row in _load_payload(path).get("http_payloads", []):
        if not isinstance(row, dict):
            continue
        payload_raw = row.get("payload")
        if not isinstance(payload_raw, dict):
            continue
        payload = resolve_http_payload(dict(payload_raw), tool)
        expected_raw = row.get("expected")
        if not isinstance(expected_raw, dict):
            expected_raw = {}
        scenarios.append(
            HttpPayloadScenario(
                id=str(row["id"]),
                payload=payload,
                expected=dict(expected_raw),
            ),
        )
    return tuple(scenarios)


def load_integration_capture_scenarios(
    path: Path = SCENARIOS_PATH,
) -> tuple[IntegrationCaptureScenario, ...]:
    scenarios: list[IntegrationCaptureScenario] = []
    for row in _load_payload(path).get("integration", []):
        if not isinstance(row, dict):
            continue
        payload_ids_raw = row.get("payload_ids")
        if not isinstance(payload_ids_raw, list):
            payload_ids_raw = []
        expected_raw = row.get("expected")
        if not isinstance(expected_raw, dict):
            expected_raw = {}
        scenarios.append(
            IntegrationCaptureScenario(
                id=str(row["id"]),
                payload_ids=tuple(str(item) for item in payload_ids_raw),
                expected=dict(expected_raw),
            ),
        )
    return tuple(scenarios)


def load_meta_tools_not_reported(path: Path = SCENARIOS_PATH) -> tuple[str, ...]:
    raw = _load_payload(path).get("meta_tools_not_reported")
    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw)


def load_capture_config_values(path: Path = SCENARIOS_PATH) -> dict[str, str]:
    raw = _load_payload(path).get("config")
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def http_payload_by_id(payload_id: str, path: Path = SCENARIOS_PATH) -> HttpPayloadScenario:
    for scenario in load_http_payloads(path):
        if scenario.id == payload_id:
            return scenario
    raise KeyError(f"unknown http payload id: {payload_id}")


def resolve_http_payload(
    payload: dict[str, Any],
    tool: CaptureToolFixture | None = None,
) -> dict[str, Any]:
    tool = tool or load_capture_tool()
    resolved = dict(payload)
    schema = resolved.get("input_schema")
    if isinstance(schema, dict) and schema.get("$use_tool_input_schema") is True:
        resolved["input_schema"] = dict(tool.input_schema)
    return resolved


def capture_tool_dict(tool: CaptureToolFixture | None = None) -> dict[str, Any]:
    tool = tool or load_capture_tool()
    return stamp_tool_catalog_source(
        {
            "name": tool.wire_name,
            "cyt_catalog_source": "cyt_mcp",
            "inputSchema": dict(tool.input_schema),
        },
    )


def capture_tier_config(
    pack: TierCaptureFixturePack,
    *,
    examples_enabled: bool = False,
) -> dict[str, Any]:
    config = dict(pack.config)
    tools = dict(config.get("tools") or {})
    tiers = dict(tools.get("tiers") or {})
    tiers["database"] = {"path": str(pack.tier_db_path)}
    tools["tiers"] = tiers
    if examples_enabled:
        examples = dict(tools.get("examples") or {})
        examples["enabled"] = True
        examples["database"] = {"path": str(pack.examples_db_path)}
        tools["examples"] = examples
    config["tools"] = tools
    return config


def materialize_capture_pack(tmp_path: Path) -> TierCaptureFixturePack:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    tier_db_path = workspace / "tier_state.db"
    examples_db_path = workspace / "tool_examples.db"
    tool = load_capture_tool()
    config = set_hook_workspace_in_config(
        {
            "tools": {
                "tiers": {
                    "mode": "shadow",
                    "capture": {
                        "source": load_capture_config_values().get("capture_default", "cyt_mcp"),
                    },
                    "database": {"path": str(tier_db_path)},
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
    return TierCaptureFixturePack(
        workspace=workspace,
        tier_db_path=tier_db_path,
        examples_db_path=examples_db_path,
        config=config,
        tool=tool,
    )


def manager_for_pack(pack: TierCaptureFixturePack) -> TierManager:
    return TierManager(pack.workspace, str(pack.tier_db_path))


def apply_attempt_sequence(
    manager: TierManager,
    pack: TierCaptureFixturePack,
    scenario: AttemptSequenceScenario,
) -> None:
    tool = capture_tool_dict(pack.tool)
    state_key = (EntityKind.TOOL, pack.tool.entity_id)
    if scenario.seed_injected is not None:
        state = manager._states.get(state_key)
        if state is None:
            state = EntityTierState(
                entity_id=pack.tool.entity_id,
                kind=EntityKind.TOOL,
                stable_tier=Tier.ACTIVE,
                effective_tier=Tier.ACTIVE,
                stats=EffectiveStats(injected=scenario.seed_injected),
            )
            manager._states[state_key] = state
        else:
            state.stats.injected = scenario.seed_injected
    for step in scenario.steps:
        manager.record_tool_attempt(
            tool,
            success=step.success,
            config=pack.config,
        )


async def post_tier_feedback_http(
    pack: TierCaptureFixturePack,
    payload: dict[str, Any],
    *,
    examples_enabled: bool = False,
) -> int:
    from cyt.hook.http_server import hook_tier_feedback

    config = capture_tier_config(pack, examples_enabled=examples_enabled)
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
    "AttemptSequenceScenario",
    "AttemptStep",
    "CaptureToolFixture",
    "HttpPayloadScenario",
    "IntegrationCaptureScenario",
    "TierCaptureFixturePack",
    "apply_attempt_sequence",
    "capture_tier_config",
    "capture_tool_dict",
    "http_payload_by_id",
    "load_attempt_sequences",
    "load_capture_config_values",
    "load_capture_tool",
    "load_http_payloads",
    "load_integration_capture_scenarios",
    "load_meta_tools_not_reported",
    "manager_for_pack",
    "materialize_capture_pack",
    "post_tier_feedback_http",
    "resolve_http_payload",
]
