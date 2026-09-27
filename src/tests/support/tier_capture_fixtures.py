"""Shared fixture loader for cyt-mcp tool-use tier capture tests."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.tiers.adapters.tools import stamp_tool_catalog_source, tool_entity_id
from cyt.tiers.manager import TierManager
from cyt.tiers.models import EffectiveStats, EntityKind, EntityTierState, Tier
from tests.support.cyt_mcp_catalog_resilience_fixtures import (
    load_usr_tools_catalog,
    load_ws_tools_catalog,
    register_layer_catalog,
)

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
class MetaToolTierFeedbackScenario:
    id: str
    tool_name: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class CatalogLayerRegistrationScenario:
    id: str
    catalog_layer: str
    backend_tool_name: str
    meta_tool_names: tuple[str, ...]


@dataclass(frozen=True)
class MetaToolIntegrationScenario:
    id: str
    payload_ids: tuple[str, ...]
    forbidden_entity_ids: tuple[str, ...]
    forbidden_names: tuple[str, ...]
    minimum_backend_tools: int | None


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


def load_meta_tool_tier_feedback_payloads(
    path: Path = SCENARIOS_PATH,
) -> tuple[MetaToolTierFeedbackScenario, ...]:
    scenarios: list[MetaToolTierFeedbackScenario] = []
    for row in _load_payload(path).get("meta_tool_tier_feedback", []):
        if not isinstance(row, dict):
            continue
        payload_raw = row.get("payload")
        if not isinstance(payload_raw, dict):
            continue
        scenarios.append(
            MetaToolTierFeedbackScenario(
                id=str(row["id"]),
                tool_name=str(row["tool_name"]),
                payload=dict(payload_raw),
            ),
        )
    return tuple(scenarios)


def meta_tool_tier_feedback_by_id(
    payload_id: str,
    path: Path = SCENARIOS_PATH,
) -> MetaToolTierFeedbackScenario:
    for scenario in load_meta_tool_tier_feedback_payloads(path):
        if scenario.id == payload_id:
            return scenario
    raise KeyError(f"unknown meta tool tier feedback id: {payload_id}")


def load_catalog_layer_registration_scenarios(
    path: Path = SCENARIOS_PATH,
) -> tuple[CatalogLayerRegistrationScenario, ...]:
    scenarios: list[CatalogLayerRegistrationScenario] = []
    for row in _load_payload(path).get("catalog_layer_registration", []):
        if not isinstance(row, dict):
            continue
        meta_names_raw = row.get("meta_tool_names")
        if not isinstance(meta_names_raw, list):
            meta_names_raw = []
        scenarios.append(
            CatalogLayerRegistrationScenario(
                id=str(row["id"]),
                catalog_layer=str(row["catalog_layer"]),
                backend_tool_name=str(row["backend_tool_name"]),
                meta_tool_names=tuple(str(item) for item in meta_names_raw),
            ),
        )
    return tuple(scenarios)


def load_meta_tool_integration_scenarios(
    path: Path = SCENARIOS_PATH,
) -> tuple[MetaToolIntegrationScenario, ...]:
    scenarios: list[MetaToolIntegrationScenario] = []
    for row in _load_payload(path).get("meta_tool_integration", []):
        if not isinstance(row, dict):
            continue
        payload_ids_raw = row.get("payload_ids")
        if not isinstance(payload_ids_raw, list):
            payload_ids_raw = []
        forbidden_entity_ids_raw = row.get("forbidden_entity_ids")
        if not isinstance(forbidden_entity_ids_raw, list):
            forbidden_entity_ids_raw = []
        forbidden_names_raw = row.get("forbidden_names")
        if not isinstance(forbidden_names_raw, list):
            forbidden_names_raw = []
        minimum_raw = row.get("minimum_backend_tools")
        scenarios.append(
            MetaToolIntegrationScenario(
                id=str(row["id"]),
                payload_ids=tuple(str(item) for item in payload_ids_raw),
                forbidden_entity_ids=tuple(str(item) for item in forbidden_entity_ids_raw),
                forbidden_names=tuple(str(item) for item in forbidden_names_raw),
                minimum_backend_tools=int(minimum_raw) if minimum_raw is not None else None,
            ),
        )
    return tuple(scenarios)


def meta_tool_entity_id(tool_name: str) -> str:
    return tool_entity_id({"name": tool_name, "cyt_catalog_source": "cyt_mcp"})


def _meta_tool_catalog_entries(meta_tool_names: Sequence[str]) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "input_schema": {"type": "object"},
            "cyt_catalog_source": "cyt_mcp",
        }
        for name in meta_tool_names
    ]


def register_catalog_with_meta_tools(
    workspace: Path,
    *,
    catalog_layer: str,
    backend_tool_name: str,
    meta_tool_names: Sequence[str],
    instance_id: str = "pid:test",
) -> None:
    if catalog_layer == "ws":
        base_tools = load_ws_tools_catalog()
    elif catalog_layer == "usr":
        base_tools = load_usr_tools_catalog()
    else:
        raise ValueError(f"unsupported catalog layer: {catalog_layer}")
    backend_present = any(str(tool.get("name") or "") == backend_tool_name for tool in base_tools)
    if not backend_present:
        raise ValueError(
            f"backend tool {backend_tool_name!r} missing from {catalog_layer} fixture catalog",
        )
    tools = list(base_tools)
    tools.extend(_meta_tool_catalog_entries(meta_tool_names))
    register_layer_catalog(
        workspace,
        tools,
        catalog_layer=catalog_layer,
        instance_id=instance_id,
    )


def register_dual_layer_catalog_with_meta_tools(
    workspace: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    meta_tool_names = load_meta_tools_not_reported()
    ws_tools = load_ws_tools_catalog()
    usr_tools = load_usr_tools_catalog()
    ws_with_meta = list(ws_tools)
    ws_with_meta.extend(_meta_tool_catalog_entries(meta_tool_names))
    usr_with_meta = list(usr_tools)
    usr_with_meta.extend(_meta_tool_catalog_entries(meta_tool_names))
    register_layer_catalog(workspace, ws_with_meta, catalog_layer="ws")
    register_layer_catalog(workspace, usr_with_meta, catalog_layer="usr")
    return ws_with_meta, usr_with_meta


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
    "CatalogLayerRegistrationScenario",
    "HttpPayloadScenario",
    "IntegrationCaptureScenario",
    "MetaToolIntegrationScenario",
    "MetaToolTierFeedbackScenario",
    "TierCaptureFixturePack",
    "apply_attempt_sequence",
    "capture_tier_config",
    "capture_tool_dict",
    "http_payload_by_id",
    "load_attempt_sequences",
    "load_capture_config_values",
    "load_capture_tool",
    "load_catalog_layer_registration_scenarios",
    "load_http_payloads",
    "load_integration_capture_scenarios",
    "load_meta_tool_integration_scenarios",
    "load_meta_tool_tier_feedback_payloads",
    "load_meta_tools_not_reported",
    "manager_for_pack",
    "materialize_capture_pack",
    "meta_tool_entity_id",
    "meta_tool_tier_feedback_by_id",
    "post_tier_feedback_http",
    "register_catalog_with_meta_tools",
    "register_dual_layer_catalog_with_meta_tools",
    "resolve_http_payload",
]
