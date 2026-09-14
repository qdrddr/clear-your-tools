"""Fixture loader for cyt-mcp wire name identity and gate regression scenarios."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from cyt.injection.session_log_build import build_tool_catalog_log_entry
from cyt_mcp.tool_identity import enrich_tool_identity

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "cyt_mcp_tool_identity"
SERVER_KEYS_PATH = FIXTURES_ROOT / "server_keys.json"
BACKEND_TOOLS_PATH = FIXTURES_ROOT / "backend_tools.json"
BARE_CATALOG_PATH = FIXTURES_ROOT / "type2_catalog_bare_regression.json"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"

CatalogVariant = Literal["wire", "bare_regression", "wire_subset"]


@dataclass(frozen=True)
class GateScenario:
    id: str
    catalog: CatalogVariant
    tool_name: str
    tool_input: dict[str, Any]
    allowed: bool
    reason_contains: tuple[str, ...]
    reason_must_not_contain: tuple[str, ...]
    issue: str | None
    catalog_tools: tuple[str, ...] | None


@dataclass(frozen=True)
class EnrichmentExpectation:
    wire_name: str
    server_key: str
    tool_name: str


@dataclass(frozen=True)
class SessionLogRequirement:
    tool: dict[str, Any]
    expected_record_name: str


@dataclass(frozen=True)
class SessionLogRejection:
    id: str
    tool: dict[str, Any]
    error_contains: str


@dataclass(frozen=True)
class ToolIdentityFixturePack:
    server_keys: tuple[str, ...]
    backend_tools: list[dict[str, Any]]
    enriched_tools: list[dict[str, Any]]
    bare_regression_tools: list[dict[str, Any]]
    gate_scenarios: tuple[GateScenario, ...]
    enrichment_expectations: tuple[EnrichmentExpectation, ...]
    session_log_requirements: tuple[SessionLogRequirement, ...]
    session_log_rejections: tuple[SessionLogRejection, ...]


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected object")
    return payload


def load_server_keys(path: Path = SERVER_KEYS_PATH) -> tuple[str, ...]:
    payload = _load_json(path)
    raw = payload.get("server_keys")
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected server_keys array")
    return tuple(str(item).strip() for item in raw if str(item).strip())


def load_backend_tools(path: Path = BACKEND_TOOLS_PATH) -> list[dict[str, Any]]:
    payload = _load_json(path)
    raw = payload.get("tools")
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected tools array")
    return [dict(item) for item in raw if isinstance(item, dict)]


def load_bare_regression_tools(path: Path = BARE_CATALOG_PATH) -> list[dict[str, Any]]:
    payload = _load_json(path)
    raw = payload.get("tools")
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected tools array")
    return [dict(item) for item in raw if isinstance(item, dict)]


def enrich_backend_tools(
    tools: list[dict[str, Any]],
    server_keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    return [enrich_tool_identity(dict(tool), list(server_keys)) for tool in tools]


def backend_tool_to_type2_record(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("input_schema") or tool.get("inputSchema") or {}
    record: dict[str, Any] = {
        "name": str(tool.get("name") or ""),
        "input_schema": schema if isinstance(schema, dict) else {},
    }
    server_key = str(tool.get("server_key") or "").strip()
    bare = str(tool.get("tool_name") or "").strip()
    if server_key:
        record["server_key"] = server_key
    if bare:
        record["tool_name"] = bare
    description = tool.get("description")
    if description is not None and str(description).strip():
        record["description"] = str(description).strip()
    return record


def enriched_type2_catalog(
    tools: list[dict[str, Any]] | None = None,
    *,
    server_keys: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    keys = server_keys or load_server_keys()
    backend = tools if tools is not None else load_backend_tools()
    enriched = enrich_backend_tools(backend, keys)
    return [backend_tool_to_type2_record(tool) for tool in enriched]


def bare_regression_type2_catalog() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for tool in load_bare_regression_tools():
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        records.append(
            {
                "name": str(tool.get("name") or ""),
                "input_schema": schema if isinstance(schema, dict) else {},
            },
        )
    return records


def _parse_gate_scenario(raw: dict[str, Any]) -> GateScenario:
    expected = raw.get("expected")
    if not isinstance(expected, dict):
        raise ValueError(f"scenario {raw.get('id')}: expected object required")
    reason_contains_raw = expected.get("reason_contains")
    reason_contains: tuple[str, ...] = ()
    if isinstance(reason_contains_raw, list):
        reason_contains = tuple(str(item) for item in reason_contains_raw)
    reason_must_not_raw = expected.get("reason_must_not_contain")
    reason_must_not_contain: tuple[str, ...] = ()
    if isinstance(reason_must_not_raw, list):
        reason_must_not_contain = tuple(str(item) for item in reason_must_not_raw)
    catalog_tools_raw = raw.get("catalog_tools")
    catalog_tools: tuple[str, ...] | None = None
    if isinstance(catalog_tools_raw, list):
        catalog_tools = tuple(str(item) for item in catalog_tools_raw)
    return GateScenario(
        id=str(raw.get("id") or ""),
        catalog=str(raw.get("catalog") or "wire"),  # type: ignore[arg-type]
        tool_name=str(raw.get("tool_name") or ""),
        tool_input=dict(raw.get("tool_input") or {}),
        allowed=bool(expected.get("allowed")),
        reason_contains=reason_contains,
        reason_must_not_contain=reason_must_not_contain,
        issue=str(raw.get("issue")).strip() if raw.get("issue") else None,
        catalog_tools=catalog_tools,
    )


def load_gate_scenarios(path: Path = SCENARIOS_PATH) -> tuple[GateScenario, ...]:
    payload = _load_json(path)
    raw_scenarios = payload.get("scenarios")
    if not isinstance(raw_scenarios, list):
        raise ValueError(f"{path}: expected scenarios array")
    return tuple(_parse_gate_scenario(item) for item in raw_scenarios if isinstance(item, dict))


def load_enrichment_expectations(path: Path = SCENARIOS_PATH) -> tuple[EnrichmentExpectation, ...]:
    payload = _load_json(path)
    raw = payload.get("enrichment_expectations")
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected enrichment_expectations array")
    out: list[EnrichmentExpectation] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            EnrichmentExpectation(
                wire_name=str(item.get("wire_name") or ""),
                server_key=str(item.get("server_key") or ""),
                tool_name=str(item.get("tool_name") or ""),
            ),
        )
    return tuple(out)


def load_session_log_requirements(path: Path = SCENARIOS_PATH) -> tuple[SessionLogRequirement, ...]:
    payload = _load_json(path)
    raw = payload.get("session_log_requirements")
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected session_log_requirements array")
    out: list[SessionLogRequirement] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        tool = item.get("tool")
        if not isinstance(tool, dict):
            continue
        out.append(
            SessionLogRequirement(
                tool=dict(tool),
                expected_record_name=str(item.get("expected_record_name") or ""),
            ),
        )
    return tuple(out)


def load_session_log_rejections(path: Path = SCENARIOS_PATH) -> tuple[SessionLogRejection, ...]:
    payload = _load_json(path)
    raw = payload.get("session_log_rejections")
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected session_log_rejections array")
    out: list[SessionLogRejection] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        tool = item.get("tool")
        if not isinstance(tool, dict):
            continue
        out.append(
            SessionLogRejection(
                id=str(item.get("id") or ""),
                tool=dict(tool),
                error_contains=str(item.get("error_contains") or ""),
            ),
        )
    return tuple(out)


def load_fixture_pack() -> ToolIdentityFixturePack:
    server_keys = load_server_keys()
    backend_tools = load_backend_tools()
    enriched_tools = enrich_backend_tools(backend_tools, server_keys)
    return ToolIdentityFixturePack(
        server_keys=server_keys,
        backend_tools=backend_tools,
        enriched_tools=enriched_tools,
        bare_regression_tools=load_bare_regression_tools(),
        gate_scenarios=load_gate_scenarios(),
        enrichment_expectations=load_enrichment_expectations(),
        session_log_requirements=load_session_log_requirements(),
        session_log_rejections=load_session_log_rejections(),
    )


def catalog_for_variant(
    variant: CatalogVariant,
    *,
    subset_wire_names: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    if variant == "bare_regression":
        return bare_regression_type2_catalog()
    catalog = enriched_type2_catalog()
    if variant == "wire_subset" and subset_wire_names:
        allowed = set(subset_wire_names)
        return [tool for tool in catalog if str(tool.get("name") or "") in allowed]
    return catalog


def write_type2_session_log(
    path: Path,
    catalog_tools: list[dict[str, Any]],
    *,
    inject_enabled: bool = True,
    legacy_bare_catalog: bool = False,
) -> None:
    """Write a Type-2 session log entry.

    When ``legacy_bare_catalog`` is true, persist tools verbatim (simulates old
    on-disk session logs that stored bare backend names without identity fields).
    """
    if legacy_bare_catalog:
        catalog_entry = {
            "kind": "tool_catalog",
            "key": "tool_catalog:cyt_mcp",
            "catalog": "cyt_mcp",
            "hash": "legacy-bare-catalog",
            "tools": catalog_tools,
        }
    else:
        catalog_entry = build_tool_catalog_log_entry("cyt_mcp", catalog_tools)
    lines = [
        json.dumps(
            {
                "kind": "session_state",
                "key": "session_state:inject",
                "tools_inject_enabled": inject_enabled,
            },
        ),
        json.dumps(catalog_entry),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def patch_session_log_resolver(
    monkeypatch: Any,
    log_path: Path | None,
) -> None:
    def _resolver(_payload: dict) -> Path | None:
        return log_path

    monkeypatch.setattr("cyt_client.tool_gate.session_log_path", _resolver)
    monkeypatch.setattr("cyt_client.sessions.session_log_path", _resolver)
    monkeypatch.setattr("cyt_client.session_pre_tool_exposure.session_log_path", _resolver)
