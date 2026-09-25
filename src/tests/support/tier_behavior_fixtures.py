"""Shared fixture loader for tier behavior (T0-T4) tests."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.skills.catalog import SkillEntryRef, build_registry, clear_registry_cache
from cyt.skills.search import MatchedSkill
from cyt.tiers.adapters.skills import skill_entity_id
from cyt.tiers.models import Tier
from tests.support.skills_helpers import isolated_skills_agents_block
from tests.support.tier_seed_helpers import parse_tier
from tests.support.tier_seed_helpers import seed_entity_tier as _seed_entity_tier
from tests.support.tier_seed_helpers import seed_tool_tiers as _seed_tool_tiers_on_db
from tests.support.tiers_stats_fixtures import (
    SKILLS_SOURCE_ROOT,
    load_tools_catalog,
    patch_cyt_mcp_paths,
)

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tier_behavior"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"
SKILL_FIXTURE_NAMES = ("create-hook.md", "context7.md", "database-shards.md")


@dataclass(frozen=True)
class ToolTierExpectation:
    tier: Tier
    raw: dict[str, Any]


@dataclass(frozen=True)
class ToolScenario:
    entity_id: str
    name: str
    tier_expectations: tuple[ToolTierExpectation, ...]


@dataclass(frozen=True)
class SkillTierExpectation:
    tier: Tier
    raw: dict[str, Any]


@dataclass(frozen=True)
class SkillScenario:
    doc_id: str
    tier_expectations: tuple[SkillTierExpectation, ...]


@dataclass(frozen=True)
class PipelineToolExpectation:
    entity_id: str
    tier: Tier
    pipeline_schema_property_keys: tuple[str, ...]
    injection_omits_schema: bool


@dataclass(frozen=True)
class PipelineSkillExpectation:
    doc_id: str
    tier: Tier
    search_markdown_includes: tuple[str, ...]
    search_markdown_excludes: tuple[str, ...]
    search_representation: str | None


@dataclass(frozen=True)
class IntegrationScenario:
    id: str
    query: str
    tool_tiers: dict[str, Tier]
    skill_tiers: dict[str, Tier]
    expected_eligible_tool_names: frozenset[str]
    expected_excluded_entity_ids: frozenset[str]
    expected_search_doc_ids: frozenset[str]
    expected_t4_doc_ids: frozenset[str]
    expected_pruned_tool_names: frozenset[str]
    expected_pruned_must_exclude: frozenset[str]
    expected_injection_omits_schema_for: frozenset[str]
    expected_skill_search_excludes_body: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class TierBehaviorFixturePack:
    workspace: Path
    db_path: Path
    catalog_cache_dir: Path
    tools: list[dict[str, Any]]
    skills_dir: Path
    catalog_dir: Path
    global_mcp_agg: Path
    global_mcp_defs: Path
    skill_paths_by_doc_id: dict[str, Path]


def _tier_value(raw: object, *, default: Tier = Tier.ACTIVE) -> Tier:
    return parse_tier(raw, default=default)


def _load_payload(path: Path = SCENARIOS_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def load_tool_scenarios(path: Path = SCENARIOS_PATH) -> tuple[ToolScenario, ...]:
    scenarios: list[ToolScenario] = []
    for row in _load_payload(path).get("tools", []):
        if not isinstance(row, dict):
            continue
        expectations: list[ToolTierExpectation] = []
        for item in row.get("tier_expectations", []):
            if not isinstance(item, dict):
                continue
            expectations.append(
                ToolTierExpectation(tier=_tier_value(item.get("tier")), raw=dict(item)),
            )
        scenarios.append(
            ToolScenario(
                entity_id=str(row["entity_id"]),
                name=str(row["name"]),
                tier_expectations=tuple(expectations),
            ),
        )
    return tuple(scenarios)


def load_skill_scenarios(path: Path = SCENARIOS_PATH) -> tuple[SkillScenario, ...]:
    scenarios: list[SkillScenario] = []
    for row in _load_payload(path).get("skills", []):
        if not isinstance(row, dict):
            continue
        expectations: list[SkillTierExpectation] = []
        for item in row.get("tier_expectations", []):
            if not isinstance(item, dict):
                continue
            expectations.append(
                SkillTierExpectation(tier=_tier_value(item.get("tier")), raw=dict(item)),
            )
        scenarios.append(
            SkillScenario(
                doc_id=str(row["doc_id"]),
                tier_expectations=tuple(expectations),
            ),
        )
    return tuple(scenarios)


def load_pipeline_tool_expectations(
    path: Path = SCENARIOS_PATH,
) -> tuple[PipelineToolExpectation, ...]:
    payload = _load_payload(path).get("pipeline") or {}
    rows = payload.get("tools") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return ()
    expectations: list[PipelineToolExpectation] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        keys_raw = row.get("pipeline_schema_property_keys", [])
        expectations.append(
            PipelineToolExpectation(
                entity_id=str(row["entity_id"]),
                tier=_tier_value(row.get("tier")),
                pipeline_schema_property_keys=tuple(str(key) for key in keys_raw),
                injection_omits_schema=bool(row.get("injection_omits_schema")),
            ),
        )
    return tuple(expectations)


def load_pipeline_skill_expectations(
    path: Path = SCENARIOS_PATH,
) -> tuple[PipelineSkillExpectation, ...]:
    payload = _load_payload(path).get("pipeline") or {}
    rows = payload.get("skills") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return ()
    expectations: list[PipelineSkillExpectation] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        expectations.append(
            PipelineSkillExpectation(
                doc_id=str(row["doc_id"]),
                tier=_tier_value(row.get("tier")),
                search_markdown_includes=tuple(
                    str(item) for item in row.get("search_markdown_includes", [])
                ),
                search_markdown_excludes=tuple(
                    str(item) for item in row.get("search_markdown_excludes", [])
                ),
                search_representation=(
                    str(row["search_representation"])
                    if row.get("search_representation") is not None
                    else None
                ),
            ),
        )
    return tuple(expectations)


def iter_pipeline_tool_cases() -> list[tuple[str, str, PipelineToolExpectation]]:
    cases: list[tuple[str, str, PipelineToolExpectation]] = []
    for scenario in load_tool_scenarios():
        for expectation in load_pipeline_tool_expectations():
            if expectation.entity_id != scenario.entity_id:
                continue
            cases.append((scenario.entity_id, scenario.name, expectation))
    return cases


def iter_pipeline_skill_cases() -> list[tuple[str, PipelineSkillExpectation]]:
    return [(item.doc_id, item) for item in load_pipeline_skill_expectations()]


def load_integration_scenarios(path: Path = SCENARIOS_PATH) -> tuple[IntegrationScenario, ...]:
    scenarios: list[IntegrationScenario] = []
    for row in _load_payload(path).get("integration", []):
        if not isinstance(row, dict):
            continue
        tool_tiers = {
            str(entity_id): _tier_value(tier_name)
            for entity_id, tier_name in dict(row.get("tool_tiers") or {}).items()
        }
        skill_tiers = {
            str(doc_id): _tier_value(tier_name)
            for doc_id, tier_name in dict(row.get("skill_tiers") or {}).items()
        }
        search_excludes_raw = row.get("expected_skill_search_excludes_body") or {}
        search_excludes: dict[str, tuple[str, ...]] = {}
        if isinstance(search_excludes_raw, dict):
            for doc_id, fragments in search_excludes_raw.items():
                if isinstance(fragments, list):
                    search_excludes[str(doc_id)] = tuple(str(item) for item in fragments)
        scenarios.append(
            IntegrationScenario(
                id=str(row["id"]),
                query=str(row["query"]),
                tool_tiers=tool_tiers,
                skill_tiers=skill_tiers,
                expected_eligible_tool_names=frozenset(
                    str(name) for name in row.get("expected_eligible_tool_names", [])
                ),
                expected_excluded_entity_ids=frozenset(
                    str(entity_id) for entity_id in row.get("expected_excluded_entity_ids", [])
                ),
                expected_search_doc_ids=frozenset(
                    str(doc_id) for doc_id in row.get("expected_search_doc_ids", [])
                ),
                expected_t4_doc_ids=frozenset(
                    str(doc_id) for doc_id in row.get("expected_t4_doc_ids", [])
                ),
                expected_pruned_tool_names=frozenset(
                    str(name) for name in row.get("expected_pruned_tool_names", [])
                ),
                expected_pruned_must_exclude=frozenset(
                    str(name) for name in row.get("expected_pruned_must_exclude", [])
                ),
                expected_injection_omits_schema_for=frozenset(
                    str(name) for name in row.get("expected_injection_omits_schema_for", [])
                ),
                expected_skill_search_excludes_body=search_excludes,
            ),
        )
    return tuple(scenarios)


def iter_tool_tier_cases() -> list[tuple[str, str, ToolTierExpectation]]:
    cases: list[tuple[str, str, ToolTierExpectation]] = []
    for scenario in load_tool_scenarios():
        for expectation in scenario.tier_expectations:
            cases.append((scenario.entity_id, scenario.name, expectation))
    return cases


def skill_fixture_key(source: SkillEntryRef | MatchedSkill | str | Path) -> str:
    """Stable scenario key for ``.agents/skills/<name>/SKILL.md`` layout."""
    if isinstance(source, SkillEntryRef):
        path = Path(source.source_path)
    elif isinstance(source, MatchedSkill):
        path = Path(source.file_path)
    else:
        path = Path(source)
    if path.name.lower() in {"skill.md", "skills.md"} and path.parent.name:
        return path.parent.name.replace("\\", "/")
    if path.suffix.lower() == ".md":
        return path.stem
    return path.name.replace("\\", "/")


def iter_skill_tier_cases() -> list[tuple[str, SkillTierExpectation]]:
    cases: list[tuple[str, SkillTierExpectation]] = []
    for scenario in load_skill_scenarios():
        for expectation in scenario.tier_expectations:
            cases.append((scenario.doc_id, expectation))
    return cases


def tool_by_name(pack: TierBehaviorFixturePack, name: str) -> dict[str, Any]:
    for tool in pack.tools:
        if tool.get("name") == name:
            return tool
    raise KeyError(f"unknown tool name: {name}")


def tool_by_entity_id(pack: TierBehaviorFixturePack, entity_id: str) -> dict[str, Any]:
    from cyt.tiers.adapters.tools import tool_entity_id

    for tool in pack.tools:
        if tool_entity_id(tool) == entity_id:
            return tool
    raise KeyError(f"unknown tool entity_id: {entity_id}")


def live_tier_config(
    pack: TierBehaviorFixturePack,
    *,
    kind: str = "both",
    frontmatter_upper_limit: float = 0.4,
) -> dict[str, Any]:
    tools_section: dict[str, Any] = {
        "tiers": {
            "mode": "live" if kind in {"tool", "both"} else "shadow",
            "database": {"path": str(pack.db_path)},
        },
    }
    skills_section: dict[str, Any] = {
        "enabled": True,
        "pipeline": "bm25",
        "catalog_dir": str(pack.catalog_dir),
        "directories": [str(pack.skills_dir)],
        "frontmatter_upper_limit": frontmatter_upper_limit,
        "max_tokens_per_request": 4000,
        "pageindex": {"enable_bm25_chunking": True},
        "tiers": {
            "mode": "live" if kind in {"skill", "both"} else "shadow",
        },
    }
    return set_hook_workspace_in_config(
        {
            "cache": {"skills_dir": str(pack.catalog_dir)},
            "skills": skills_section,
            "tools": tools_section,
            "pruning": {
                "inject_via": {"cursor": "hook", "claude": "proxy", "codex": "proxy"},
                "tools": {
                    "enabled": True,
                    "hook": {
                        "tools_from": ["cyt_mcp"],
                        "cyt_mcp": {"agent": "cursor"},
                    },
                },
            },
            "agents": isolated_skills_agents_block(),
        },
        pack.workspace,
    )


def seed_entity_tier(
    pack: TierBehaviorFixturePack,
    *,
    kind: str,
    entity_id: str,
    tier: Tier,
) -> None:
    _seed_entity_tier(
        workspace=pack.workspace,
        db_path=pack.db_path,
        kind=kind,
        entity_id=entity_id,
        tier=tier,
    )


def seed_tool_tiers(
    pack: TierBehaviorFixturePack,
    tier_map: dict[str, Tier],
) -> None:
    _seed_tool_tiers_on_db(
        workspace=pack.workspace,
        db_path=pack.db_path,
        tier_map=tier_map,
    )


def seed_skill_tiers_by_doc_id(
    pack: TierBehaviorFixturePack,
    tier_map: dict[str, Tier],
) -> None:
    entity_ids = skill_entity_ids_by_doc_id(pack)
    for doc_id, tier in tier_map.items():
        entity_id = entity_ids.get(doc_id)
        if not entity_id:
            raise KeyError(f"no skill entity id for doc_id {doc_id!r}")
        seed_entity_tier(pack, kind="skill", entity_id=entity_id, tier=tier)


def build_registry_from_pack(pack: TierBehaviorFixturePack) -> list[SkillEntryRef]:
    config = live_tier_config(pack, kind="skill")
    clear_registry_cache()
    return build_registry(config)


def skill_entity_ids_by_doc_id(pack: TierBehaviorFixturePack) -> dict[str, str]:
    entries = build_registry_from_pack(pack)
    mapping: dict[str, str] = {}
    for entry in entries:
        entity_id = skill_entity_id(entry)
        if entity_id:
            mapping[skill_fixture_key(entry)] = entity_id
    return mapping


def materialize_fixture_pack(tmp_path: Path) -> TierBehaviorFixturePack:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()

    skills_root = workspace / ".agents" / "skills"
    skills_root.mkdir(parents=True)
    skill_paths_by_doc_id: dict[str, Path] = {}
    for fixture_name in SKILL_FIXTURE_NAMES:
        source = SKILLS_SOURCE_ROOT / fixture_name
        if not source.is_file():
            raise FileNotFoundError(f"missing skill fixture: {source}")
        doc_id = fixture_name.replace(".md", "")
        target_dir = skills_root / doc_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "SKILL.md"
        shutil.copy2(source, target)
        skill_paths_by_doc_id[doc_id] = target

    catalog_dir = tmp_path / "catalog"
    catalog_dir.mkdir()
    cyt_config_dir = workspace / ".agents" / "cyt" / "config"
    mcp_dir = cyt_config_dir / "mcp"
    mcp_dir.mkdir(parents=True)
    (cyt_config_dir / "config.yaml").write_text(
        "skills:\n  directories:\n  - .agents/skills\n",
        encoding="utf-8",
    )
    (cyt_config_dir / "mcp-aggregator.yaml").write_text(
        "default_agent: cursor\n",
        encoding="utf-8",
    )
    (mcp_dir / "cursor.json").write_text(
        '{"mcpServers": {"context-mode": {}, "gitnexus": {}}}',
        encoding="utf-8",
    )

    db_path = workspace / "tier_state.db"
    catalog_cache_dir = tmp_path / "cyt-mcp-catalog"
    catalog_cache_dir.mkdir(parents=True, exist_ok=True)
    global_mcp_agg = tmp_path / "global-mcp-aggregator.yaml"
    global_mcp_defs = tmp_path / "global-mcp" / "cursor.json"
    global_mcp_defs.parent.mkdir(parents=True)
    global_mcp_agg.write_text("default_agent: cursor\n", encoding="utf-8")
    global_mcp_defs.write_text('{"mcpServers": {}}', encoding="utf-8")

    tools = load_tools_catalog()
    return TierBehaviorFixturePack(
        workspace=workspace,
        db_path=db_path,
        catalog_cache_dir=catalog_cache_dir,
        tools=tools,
        skills_dir=skills_root,
        catalog_dir=catalog_dir,
        global_mcp_agg=global_mcp_agg,
        global_mcp_defs=global_mcp_defs,
        skill_paths_by_doc_id=skill_paths_by_doc_id,
    )


def prepare_disk_catalog_pack(
    pack: TierBehaviorFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> TierBehaviorFixturePack:
    patch_cyt_mcp_paths(monkeypatch, pack)
    config = live_tier_config(pack, kind="tool")
    write_cyt_mcp_disk_catalog_for_pack(pack, config)
    return pack


def write_cyt_mcp_disk_catalog_for_pack(
    pack: TierBehaviorFixturePack,
    config: dict[str, Any] | None = None,
) -> None:
    from cyt.cyt_mcp.catalog import apply_fetched_catalog

    apply_fetched_catalog(config or live_tier_config(pack, kind="tool"), pack.tools)


__all__ = [
    "IntegrationScenario",
    "PipelineSkillExpectation",
    "PipelineToolExpectation",
    "TierBehaviorFixturePack",
    "build_registry_from_pack",
    "iter_pipeline_skill_cases",
    "iter_pipeline_tool_cases",
    "iter_skill_tier_cases",
    "iter_tool_tier_cases",
    "live_tier_config",
    "load_integration_scenarios",
    "load_pipeline_skill_expectations",
    "load_pipeline_tool_expectations",
    "load_skill_scenarios",
    "load_tool_scenarios",
    "materialize_fixture_pack",
    "patch_cyt_mcp_paths",
    "prepare_disk_catalog_pack",
    "seed_skill_tiers_by_doc_id",
    "seed_tool_tiers",
    "skill_entity_ids_by_doc_id",
    "skill_fixture_key",
    "tool_by_entity_id",
    "tool_by_name",
    "write_cyt_mcp_disk_catalog_for_pack",
]
