"""Shared fixture loader for tier shadow evaluation tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyt.tiers.adapters.tools import stamp_tool_catalog_source
from tests.support.tier_transitions_fixtures import McpServerToolFixture, load_mcp_server_tools

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tier_shadow"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"


def _load_payload(path: Path = SCENARIOS_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


@dataclass(frozen=True)
class LexicalHitScenario:
    id: str
    query: str
    entity_id: str
    expected_score: float
    tool: dict[str, Any] | None = None
    skill: dict[str, Any] | None = None


@dataclass(frozen=True)
class RecordShadowScenario:
    id: str
    kind: str
    entity_id: str | None
    hits: list[tuple[str, float]]
    expect_wake: bool | None = None
    mcp_server_batch: bool = False
    primary_entity_id: str | None = None
    hit_score: float = 0.0
    mcp_server: str | None = None
    expect_tools_dormant: bool = False
    expect_server_cold: bool = False


def load_wake_cycle_id(path: Path = SCENARIOS_PATH) -> int:
    return int(_load_payload(path).get("wake_cycle_id", 1))


def load_lexical_hit_scenarios(path: Path = SCENARIOS_PATH) -> tuple[LexicalHitScenario, ...]:
    rows = _load_payload(path).get("lexical_hits", [])
    scenarios: list[LexicalHitScenario] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        entity_id = str(row.get("entity_id", ""))
        tool = None
        skill = None
        if entity_id.startswith("skill:"):
            skill = {
                "name": str(row.get("name", entity_id.split(":", 1)[1])),
                "description": str(row.get("description", "")),
                "doc_id": entity_id.split(":", 1)[1],
            }
        else:
            tool = stamp_tool_catalog_source(
                {
                    "name": str(row.get("wire_name", "")),
                    "cyt_catalog_source": "cyt_mcp",
                    "description": str(row.get("description", "")),
                    "inputSchema": {"type": "object"},
                },
            )
        scenarios.append(
            LexicalHitScenario(
                id=str(row["id"]),
                query=str(row["query"]),
                entity_id=entity_id,
                expected_score=float(row.get("expected_score", 0.0)),
                tool=tool,
                skill=skill,
            ),
        )
    return tuple(scenarios)


def _mcp_tool_dict(fixture: McpServerToolFixture) -> dict[str, Any]:
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


def mcp_server_tools_by_id() -> dict[str, dict[str, Any]]:
    return {fixture.entity_id: _mcp_tool_dict(fixture) for fixture in load_mcp_server_tools()}


def load_record_shadow_scenarios(path: Path = SCENARIOS_PATH) -> tuple[RecordShadowScenario, ...]:
    rows = _load_payload(path).get("record_shadow", [])
    scenarios: list[RecordShadowScenario] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_hits = row.get("hits")
        hits: list[tuple[str, float]] = []
        if isinstance(raw_hits, list):
            for item in raw_hits:
                if isinstance(item, list) and len(item) == 2:
                    hits.append((str(item[0]), float(item[1])))
        scenarios.append(
            RecordShadowScenario(
                id=str(row["id"]),
                kind=str(row.get("kind", "tool")),
                entity_id=str(row["entity_id"]) if row.get("entity_id") else None,
                hits=hits,
                expect_wake=row.get("expect_wake") if "expect_wake" in row else None,
                mcp_server_batch=bool(row.get("mcp_server_batch", False)),
                primary_entity_id=str(row["primary_entity_id"])
                if row.get("primary_entity_id")
                else None,
                hit_score=float(row.get("hit_score", 0.0)),
                mcp_server=str(row["mcp_server"]) if row.get("mcp_server") else None,
                expect_tools_dormant=bool(row.get("expect_tools_dormant", False)),
                expect_server_cold=bool(row.get("expect_server_cold", False)),
            ),
        )
    return tuple(scenarios)
