"""Loaders for tier injection XML / pre-exposure fixture scenarios."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.support.paths import FIXTURES_DIR
from tests.support.tier_behavior_fixtures import IntegrationScenario

TIER_INJECTION_DIR = FIXTURES_DIR / "tier_injection"
SCENARIOS_PATH = TIER_INJECTION_DIR / "scenarios.json"


@dataclass(frozen=True)
class TierAttrCase:
    tier: str
    expected_attr: str


@dataclass(frozen=True)
class PreExposureCase:
    id: str
    tool_ref: str
    expects_skipped: bool


@dataclass(frozen=True)
class InjectionIntegrationScenario:
    id: str
    behavior_scenario_id: str
    query: str | None
    expected_stamped_tools: dict[str, str]
    expected_skill_tiers: dict[str, str]
    expects_tool_legend: bool
    expects_tier_attr_on_tools: tuple[str, ...]


def _load_payload(path: Path = SCENARIOS_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def load_tier_attr_cases(path: Path = SCENARIOS_PATH) -> tuple[TierAttrCase, ...]:
    cases: list[TierAttrCase] = []
    for row in _load_payload(path).get("tier_attr_cases", []):
        if not isinstance(row, dict):
            continue
        cases.append(
            TierAttrCase(
                tier=str(row["tier"]),
                expected_attr=str(row["expected_attr"]),
            ),
        )
    return tuple(cases)


def load_legend_snippets(path: Path = SCENARIOS_PATH) -> dict[str, tuple[str, ...]]:
    raw = _load_payload(path).get("legend_snippets", {})
    if not isinstance(raw, dict):
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for key, values in raw.items():
        if isinstance(values, list):
            out[str(key)] = tuple(str(item) for item in values)
    return out


def load_sample_tools(path: Path = SCENARIOS_PATH) -> list[dict[str, Any]]:
    raw = _load_payload(path).get("sample_tools", [])
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def sample_tool_by_name(name: str, path: Path = SCENARIOS_PATH) -> dict[str, Any]:
    for tool in load_sample_tools(path):
        if str(tool.get("name") or "") == name:
            return tool
    raise KeyError(f"unknown sample tool {name!r}")


def load_pre_exposure_cases(path: Path = SCENARIOS_PATH) -> tuple[PreExposureCase, ...]:
    cases: list[PreExposureCase] = []
    for row in _load_payload(path).get("pre_exposure", []):
        if not isinstance(row, dict):
            continue
        cases.append(
            PreExposureCase(
                id=str(row["id"]),
                tool_ref=str(row["tool_ref"]),
                expects_skipped=bool(row.get("expects_skipped", True)),
            ),
        )
    return tuple(cases)


def load_injection_integration_scenarios(
    path: Path = SCENARIOS_PATH,
) -> tuple[InjectionIntegrationScenario, ...]:
    scenarios: list[InjectionIntegrationScenario] = []
    for row in _load_payload(path).get("integration", []):
        if not isinstance(row, dict):
            continue
        stamped_raw = row.get("expected_stamped_tools") or {}
        skill_raw = row.get("expected_skill_tiers") or {}
        tier_attrs_raw = row.get("expects_tier_attr_on_tools") or []
        scenarios.append(
            InjectionIntegrationScenario(
                id=str(row["id"]),
                behavior_scenario_id=str(row["behavior_scenario_id"]),
                query=str(row["query"]) if row.get("query") else None,
                expected_stamped_tools={
                    str(name): str(tier) for name, tier in dict(stamped_raw).items()
                },
                expected_skill_tiers={
                    str(doc_id): str(tier) for doc_id, tier in dict(skill_raw).items()
                },
                expects_tool_legend=bool(row.get("expects_tool_legend", False)),
                expects_tier_attr_on_tools=tuple(
                    str(name) for name in tier_attrs_raw if isinstance(name, str)
                ),
            ),
        )
    return tuple(scenarios)


def behavior_scenario_by_id(scenario_id: str) -> IntegrationScenario:
    from tests.support.tier_behavior_fixtures import load_integration_scenarios

    for scenario in load_integration_scenarios():
        if scenario.id == scenario_id:
            return scenario
    raise KeyError(f"unknown tier_behavior integration scenario {scenario_id!r}")
