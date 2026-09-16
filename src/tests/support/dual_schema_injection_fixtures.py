"""Loaders and workspace materialization for dual-schema tier injection fixtures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.tiers.models import Tier
from cyt.tiers.tool_token_materialization import CYT_BACKEND_INPUT_SCHEMA
from tests.support.tier_seed_helpers import parse_tier
from tests.support.tiers_stats_fixtures import patch_cyt_mcp_paths

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "dual_schema_injection"
TOOLS_PATH = FIXTURES_ROOT / "tools.json"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"
INJECTION_TABLE_PATH = FIXTURES_ROOT / "injection_table.json"

_TIER_LABEL_TO_ENUM = {
    "t0": Tier.DORMANT,
    "t1": Tier.COLD,
    "t2": Tier.ACTIVE,
    "t3": Tier.HOT,
    "t4": Tier.EXTRA_HOT,
}

_TIER_BY_NAME = {
    "DORMANT": Tier.DORMANT,
    "COLD": Tier.COLD,
    "ACTIVE": Tier.ACTIVE,
    "HOT": Tier.HOT,
    "EXTRA_HOT": Tier.EXTRA_HOT,
}


@dataclass(frozen=True)
class StampCase:
    id: str
    tool_ref: str
    tier: Tier
    backend_property_keys: tuple[str, ...]
    tier_scoped_property_keys: tuple[str, ...]


@dataclass(frozen=True)
class HintCase:
    id: str
    tool_ref: str
    tier: str
    injected_property_keys: tuple[str, ...]
    expects_hint: bool
    expects_explicit_empty_schema: bool
    expects_required_keys: tuple[str, ...]


@dataclass(frozen=True)
class EnsureMergeCase:
    id: str
    tool_ref: str
    tier: str
    injected_property_keys: tuple[str, ...]
    expected_property_keys: tuple[str, ...]


@dataclass(frozen=True)
class IntegrationInjectionExpectation:
    hint: bool = False
    explicit_empty_schema: bool = False
    required_keys: tuple[str, ...] = ()
    optional_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntegrationScenario:
    id: str
    query: str
    tool_tiers: dict[str, Tier]
    must_include_tools: tuple[str, ...]
    expected_stamped_tiers: dict[str, str]
    expected_has_backend_schema: tuple[str, ...]
    expected_injection: dict[str, IntegrationInjectionExpectation]


@dataclass(frozen=True)
class InjectionTableCase:
    id: str
    tier: str
    tool_ref: str
    backend_profile: str
    prune_outcome: str
    pre_existed: bool | None
    injected_property_keys: tuple[str, ...]
    expected: str
    expected_keys: tuple[str, ...]
    apply_ensure_merge: bool
    prune_query: str | None = None
    prune_decoy_tool_ref: str | None = None


@dataclass(frozen=True)
class DualSchemaFixturePack:
    workspace: Path
    db_path: Path
    catalog_cache_dir: Path
    catalog_dir: Path
    tools: list[dict[str, Any]]
    global_mcp_agg: Path
    global_mcp_defs: Path


def _load_payload(path: Path = SCENARIOS_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def load_tools_catalog(path: Path = TOOLS_PATH) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tools = payload.get("tools")
    if not isinstance(tools, list):
        raise ValueError(f"{path}: expected tools array")
    return [dict(tool) for tool in tools if isinstance(tool, dict)]


def tool_by_name(name: str, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    catalog = tools if tools is not None else load_tools_catalog()
    for tool in catalog:
        if str(tool.get("name") or "") == name:
            return dict(tool)
    raise KeyError(f"unknown dual_schema tool {name!r}")


def load_injection_table_cases(
    path: Path = INJECTION_TABLE_PATH,
) -> tuple[InjectionTableCase, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected rows array")
    cases: list[InjectionTableCase] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pre_raw = row.get("pre_existed")
        pre_existed = None if pre_raw is None else bool(pre_raw)
        cases.append(
            InjectionTableCase(
                id=str(row["id"]),
                tier=str(row["tier"]),
                tool_ref=str(row["tool_ref"]),
                backend_profile=str(row.get("backend_profile", "any")),
                prune_outcome=str(row.get("prune_outcome", "survived")),
                pre_existed=pre_existed,
                injected_property_keys=tuple(
                    str(key) for key in row.get("injected_property_keys", [])
                ),
                expected=str(row["expected"]),
                expected_keys=tuple(str(key) for key in row.get("expected_keys", [])),
                apply_ensure_merge=bool(row.get("apply_ensure_merge", False)),
                prune_query=(
                    str(row["prune_query"]) if row.get("prune_query") is not None else None
                ),
                prune_decoy_tool_ref=(
                    str(row["prune_decoy_tool_ref"])
                    if row.get("prune_decoy_tool_ref") is not None
                    else None
                ),
            ),
        )
    return tuple(cases)


def injection_table_cases_by_expected(expected: str) -> tuple[InjectionTableCase, ...]:
    return tuple(case for case in load_injection_table_cases() if case.expected == expected)


_FORMAT_EXPECTATIONS = frozenset(
    {
        "description_only",
        "hint",
        "explicit_empty_schema",
        "required_schema",
        "optional_only_schema",
        "required_and_optional_schema",
        "full_backend_schema",
    },
)


def tier_map_for_injection_table_prune(case: InjectionTableCase) -> dict[str, Tier]:
    from cyt.tiers.adapters.tools import tool_entity_id

    if case.prune_decoy_tool_ref is None:
        raise ValueError(f"{case.id}: prune_decoy_tool_ref is required for absent_prune rows")
    target_tier = _TIER_LABEL_TO_ENUM[case.tier.lower()]
    tier_map: dict[str, Tier] = {}
    for tool in load_tools_catalog():
        entity_id = tool_entity_id(tool)
        name = str(tool.get("name") or "")
        if name in {case.tool_ref, case.prune_decoy_tool_ref}:
            tier_map[entity_id] = target_tier
        else:
            tier_map[entity_id] = Tier.DORMANT
    return tier_map


def injection_table_format_cases() -> tuple[InjectionTableCase, ...]:
    return tuple(
        case
        for case in load_injection_table_cases()
        if case.expected in _FORMAT_EXPECTATIONS and case.pre_existed is False
    )


def build_injection_table_tool(
    case: InjectionTableCase,
    catalog: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from cyt.tiers.adapters.tools import prepare_tool_for_tier_pipeline
    from cyt.tools.injection_schema import ensure_tool_injection_schema

    source = tool_by_name(case.tool_ref, catalog)
    tier = _TIER_LABEL_TO_ENUM[case.tier.lower()]
    prepared = prepare_tool_for_tier_pipeline(source, tier)
    backend = prepared.get(CYT_BACKEND_INPUT_SCHEMA) or {}
    backend_props = backend.get("properties") if isinstance(backend, dict) else {}
    if not isinstance(backend_props, dict):
        backend_props = {}
    if case.injected_property_keys:
        props = {
            key: backend_props.get(key, {"type": "string"})
            for key in case.injected_property_keys
        }
        required = [
            name
            for name in backend.get("required", [])
            if isinstance(name, str) and name in props
        ]
        prepared["input_schema"] = {
            "type": "object",
            "properties": props,
            **({"required": required} if required else {}),
        }
    elif case.tier.lower() not in {"t1"}:
        prepared["input_schema"] = {
            "type": "object",
            "properties": {},
        }
    prepared["cyt_injection_tier"] = case.tier.lower()
    if case.apply_ensure_merge:
        prepared = ensure_tool_injection_schema(prepared)
    return prepared


def assert_injection_table_format(item: str, case: InjectionTableCase) -> None:
    expected = case.expected
    if expected == "description_only":
        assert "input_schema" not in item, item
        assert "get-tool-definitions" not in item, item
        assert "<tool " in item, item
        return
    if expected == "hint":
        assert "get-tool-definitions" in item, item
        assert "'input_schema':" not in item, item
        return
    if expected == "explicit_empty_schema":
        assert "'input_schema':" in item, item
        assert "'properties':{}" in item, item
        assert "'type':'object'" in item, item
        assert "get-tool-definitions" not in item, item
        return
    if expected in {"required_schema", "optional_only_schema", "required_and_optional_schema", "full_backend_schema"}:
        assert "get-tool-definitions" not in item, item
        assert "'input_schema':" in item, item
        for key in case.expected_keys:
            assert f"'{key}'" in item, item
        if expected == "required_schema":
            for prop in _unexpected_property_keys(case):
                assert f"'{prop}'" not in item, item
        return
    raise AssertionError(f"unexpected format expectation {expected!r} for case {case.id}")


def _unexpected_property_keys(case: InjectionTableCase) -> tuple[str, ...]:
    tool = tool_by_name(case.tool_ref)
    backend = tool.get("input_schema") or {}
    props = backend.get("properties") if isinstance(backend, dict) else {}
    if not isinstance(props, dict):
        return ()
    allowed = set(case.expected_keys)
    return tuple(key for key in props if key not in allowed)


def load_stamp_cases(path: Path = SCENARIOS_PATH) -> tuple[StampCase, ...]:
    cases: list[StampCase] = []
    for row in _load_payload(path).get("stamp_cases", []):
        if not isinstance(row, dict):
            continue
        tier_name = str(row["tier"])
        cases.append(
            StampCase(
                id=str(row["id"]),
                tool_ref=str(row["tool_ref"]),
                tier=_TIER_BY_NAME[tier_name],
                backend_property_keys=tuple(str(key) for key in row.get("backend_property_keys", [])),
                tier_scoped_property_keys=tuple(
                    str(key) for key in row.get("tier_scoped_property_keys", [])
                ),
            ),
        )
    return tuple(cases)


def load_hint_cases(path: Path = SCENARIOS_PATH) -> tuple[HintCase, ...]:
    cases: list[HintCase] = []
    for row in _load_payload(path).get("hint_cases", []):
        if not isinstance(row, dict):
            continue
        cases.append(
            HintCase(
                id=str(row["id"]),
                tool_ref=str(row["tool_ref"]),
                tier=str(row["tier"]),
                injected_property_keys=tuple(
                    str(key) for key in row.get("injected_property_keys", [])
                ),
                expects_hint=bool(row.get("expects_hint", False)),
                expects_explicit_empty_schema=bool(row.get("expects_explicit_empty_schema", False)),
                expects_required_keys=tuple(
                    str(key) for key in row.get("expects_required_keys", [])
                ),
            ),
        )
    return tuple(cases)


def load_ensure_merge_cases(path: Path = SCENARIOS_PATH) -> tuple[EnsureMergeCase, ...]:
    cases: list[EnsureMergeCase] = []
    for row in _load_payload(path).get("ensure_merge_cases", []):
        if not isinstance(row, dict):
            continue
        cases.append(
            EnsureMergeCase(
                id=str(row["id"]),
                tool_ref=str(row["tool_ref"]),
                tier=str(row["tier"]),
                injected_property_keys=tuple(
                    str(key) for key in row.get("injected_property_keys", [])
                ),
                expected_property_keys=tuple(
                    str(key) for key in row.get("expected_property_keys", [])
                ),
            ),
        )
    return tuple(cases)


def load_integration_scenarios(path: Path = SCENARIOS_PATH) -> tuple[IntegrationScenario, ...]:
    scenarios: list[IntegrationScenario] = []
    for row in _load_payload(path).get("integration", []):
        if not isinstance(row, dict):
            continue
        tier_map = {
            str(entity_id): parse_tier(str(tier_name))
            for entity_id, tier_name in dict(row.get("tool_tiers") or {}).items()
        }
        injection_raw = row.get("expected_injection") or {}
        expected_injection: dict[str, IntegrationInjectionExpectation] = {}
        for tool_name, spec in dict(injection_raw).items():
            if not isinstance(spec, dict):
                continue
            expected_injection[str(tool_name)] = IntegrationInjectionExpectation(
                hint=bool(spec.get("hint", False)),
                explicit_empty_schema=bool(spec.get("explicit_empty_schema", False)),
                required_keys=tuple(str(key) for key in spec.get("required_keys", [])),
                optional_keys=tuple(str(key) for key in spec.get("optional_keys", [])),
            )
        scenarios.append(
            IntegrationScenario(
                id=str(row["id"]),
                query=str(row["query"]),
                tool_tiers=tier_map,
                must_include_tools=tuple(str(name) for name in row.get("must_include_tools", [])),
                expected_stamped_tiers={
                    str(name): str(tier) for name, tier in dict(row.get("expected_stamped_tiers") or {}).items()
                },
                expected_has_backend_schema=tuple(
                    str(name) for name in row.get("expected_has_backend_schema", [])
                ),
                expected_injection=expected_injection,
            ),
        )
    return tuple(scenarios)


def build_hint_tool(case: HintCase, catalog: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    from cyt.tiers.adapters.tools import prepare_tool_for_tier_pipeline

    source = tool_by_name(case.tool_ref, catalog)
    tier = _TIER_LABEL_TO_ENUM[case.tier.lower()]
    prepared = prepare_tool_for_tier_pipeline(source, tier)
    backend = prepared.get(CYT_BACKEND_INPUT_SCHEMA) or {}
    props = {
        key: backend.get("properties", {}).get(key, {"type": "string"})
        for key in case.injected_property_keys
    }
    required = [
        name
        for name in backend.get("required", [])
        if isinstance(name, str) and name in props
    ]
    prepared["input_schema"] = {
        "type": "object",
        "properties": props,
        **({"required": required} if required else {}),
    }
    prepared["cyt_injection_tier"] = case.tier.lower()
    return prepared


def build_ensure_merge_tool(
    case: EnsureMergeCase,
    catalog: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    tool = build_hint_tool(
        HintCase(
            id=case.id,
            tool_ref=case.tool_ref,
            tier=case.tier,
            injected_property_keys=case.injected_property_keys,
            expects_hint=False,
            expects_explicit_empty_schema=False,
            expects_required_keys=case.expected_property_keys,
        ),
        catalog,
    )
    return tool


def materialize_fixture_pack(tmp_path: Path) -> DualSchemaFixturePack:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()

    catalog_dir = tmp_path / "catalog"
    catalog_dir.mkdir()
    cyt_config_dir = workspace / ".agents" / "cyt" / "config"
    mcp_dir = cyt_config_dir / "mcp"
    mcp_dir.mkdir(parents=True)
    (cyt_config_dir / "config.yaml").write_text("skills:\n  enabled: false\n", encoding="utf-8")
    (cyt_config_dir / "mcp-aggregator.yaml").write_text("default_agent: cursor\n", encoding="utf-8")
    (mcp_dir / "cursor.json").write_text('{"mcpServers": {"dual-schema": {}}}', encoding="utf-8")

    db_path = workspace / "tier_state.db"
    catalog_cache_dir = tmp_path / "cyt-mcp-catalog"
    catalog_cache_dir.mkdir()
    global_mcp_agg = tmp_path / "global-mcp-aggregator.yaml"
    global_mcp_defs = tmp_path / "global-mcp" / "cursor.json"
    global_mcp_defs.parent.mkdir(parents=True)
    global_mcp_agg.write_text("default_agent: cursor\n", encoding="utf-8")
    global_mcp_defs.write_text('{"mcpServers": {}}', encoding="utf-8")

    tools = load_tools_catalog()
    return DualSchemaFixturePack(
        workspace=workspace,
        db_path=db_path,
        catalog_cache_dir=catalog_cache_dir,
        catalog_dir=catalog_dir,
        tools=tools,
        global_mcp_agg=global_mcp_agg,
        global_mcp_defs=global_mcp_defs,
    )


def live_tier_config(pack: DualSchemaFixturePack) -> dict[str, Any]:
    return set_hook_workspace_in_config(
        {
            "tools": {
                "tiers": {
                    "mode": "live",
                    "database": {"path": str(pack.db_path)},
                },
            },
            "skills": {"enabled": False},
            "hook": {
                "workspace_roots": [str(pack.workspace)],
                "cyt_mcp_catalog_cache_dir": str(pack.catalog_cache_dir),
            },
        },
        str(pack.workspace),
    )


def write_disk_catalog(pack: DualSchemaFixturePack, config: dict[str, Any] | None = None) -> None:
    from cyt.cyt_mcp.catalog import apply_fetched_catalog

    apply_fetched_catalog(config or live_tier_config(pack), pack.tools)


def seed_tool_tiers(pack: DualSchemaFixturePack, tier_map: dict[str, Tier]) -> None:
    from tests.support.tier_seed_helpers import seed_tool_tiers as _seed_tool_tiers_on_db

    _seed_tool_tiers_on_db(
        workspace=pack.workspace,
        db_path=pack.db_path,
        tier_map=tier_map,
    )


def patch_paths(monkeypatch: Any, pack: DualSchemaFixturePack) -> None:
    patch_cyt_mcp_paths(monkeypatch, pack)


__all__ = [
    "CYT_BACKEND_INPUT_SCHEMA",
    "DualSchemaFixturePack",
    "EnsureMergeCase",
    "HintCase",
    "InjectionTableCase",
    "IntegrationScenario",
    "StampCase",
    "assert_injection_table_format",
    "build_ensure_merge_tool",
    "build_hint_tool",
    "build_injection_table_tool",
    "injection_table_cases_by_expected",
    "injection_table_format_cases",
    "tier_map_for_injection_table_prune",
    "live_tier_config",
    "load_ensure_merge_cases",
    "load_hint_cases",
    "load_injection_table_cases",
    "load_integration_scenarios",
    "load_stamp_cases",
    "load_tools_catalog",
    "materialize_fixture_pack",
    "patch_paths",
    "seed_tool_tiers",
    "tool_by_name",
    "write_disk_catalog",
]
