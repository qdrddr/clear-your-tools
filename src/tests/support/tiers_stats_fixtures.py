"""Shared fixture loader for ``cyt tiers stats`` tool/skill tiering tests."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import pytest

from cyt.cyt_mcp.catalog import apply_fetched_catalog
from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.tiers.models import EffectiveStats, EntityTierState, Tier, TierProject
from cyt.tiers.store import TierStore

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tiers_stats"
SKILLS_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "skills_frontmatter_gate"
TOOLS_CATALOG_PATH = FIXTURES_ROOT / "tools_catalog.json"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"

_TIER_BY_NAME = {
    "DORMANT": Tier.DORMANT,
    "COLD": Tier.COLD,
    "ACTIVE": Tier.ACTIVE,
    "HOT": Tier.HOT,
    "EXTRA_HOT": Tier.EXTRA_HOT,
}


@dataclass(frozen=True)
class TiersStatsScenario:
    skill_fixture_names: tuple[str, ...]
    tier_states: dict[str, dict[str, Any]]
    expected: dict[str, Any]


@dataclass(frozen=True)
class TiersStatsFixturePack:
    workspace: Path
    db_path: Path
    catalog_cache_dir: Path
    tools: list[dict[str, Any]]
    scenario: TiersStatsScenario
    global_mcp_agg: Path
    global_mcp_defs: Path


def load_tools_catalog(path: Path = TOOLS_CATALOG_PATH) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tools = payload.get("tools")
    if not isinstance(tools, list):
        raise ValueError(f"{path}: expected tools array")
    return [dict(tool) for tool in tools if isinstance(tool, dict)]


def load_scenario(path: Path = SCENARIOS_PATH) -> TiersStatsScenario:
    payload = json.loads(path.read_text(encoding="utf-8"))
    skill_names_raw = payload.get("skill_fixture_names")
    if not isinstance(skill_names_raw, list):
        raise ValueError(f"{path}: expected skill_fixture_names array")
    tier_states_raw = payload.get("tier_states")
    if not isinstance(tier_states_raw, dict):
        raise ValueError(f"{path}: expected tier_states object")
    expected_raw = payload.get("expected")
    if not isinstance(expected_raw, dict):
        raise ValueError(f"{path}: expected expected object")
    return TiersStatsScenario(
        skill_fixture_names=tuple(str(name) for name in skill_names_raw),
        tier_states={
            str(kind): dict(states) if isinstance(states, dict) else {}
            for kind, states in tier_states_raw.items()
        },
        expected=dict(expected_raw),
    )


def _tier_value(raw: object, *, default: Tier = Tier.DORMANT) -> Tier:
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
        "used_without_injection",
        "optional_used",
        "shadow_hits",
        "shadow_evaluations",
    ):
        value = raw.get(key)
        if isinstance(value, (int, float)):
            setattr(stats, key, float(value))
    return stats


def seed_tier_db(pack: TiersStatsFixturePack) -> int:
    """Insert scenario tier states; returns project_id."""
    store = TierStore.open(str(pack.db_path))
    try:
        project_id = store.get_or_create_project(str(pack.workspace))
        project = TierProject(project_id=project_id, root_path=pack.workspace)
        for kind_key, states in pack.scenario.tier_states.items():
            kind = "tool" if kind_key == "tools" else "skill"
            for entity_id, row in states.items():
                if not isinstance(row, dict):
                    continue
                store.upsert_entity_state(
                    project,
                    EntityTierState(
                        entity_id=str(entity_id),
                        kind=kind,
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


def tier_stats_config(pack: TiersStatsFixturePack) -> dict[str, Any]:
    return set_hook_workspace_in_config(
        {
            "pruning": {
                "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
                "tools": {
                    "enabled": True,
                    "hook": {
                        "tools_from": ["cyt_mcp"],
                        "cyt_mcp": {"agent": "cursor"},
                    },
                },
            },
            "tools": {
                "tiers": {
                    "mode": "shadow",
                    "database": {"path": str(pack.db_path)},
                },
            },
            "skills": {
                "tiers": {
                    "mode": "shadow",
                },
                "directories": [str(pack.workspace / ".agents" / "skills")],
            },
        },
        pack.workspace,
    )


def write_cyt_mcp_disk_catalog(pack: TiersStatsFixturePack) -> None:
    config = tier_stats_config(pack)
    apply_fetched_catalog(config, pack.tools)


def materialize_fixture_pack(tmp_path: Path) -> TiersStatsFixturePack:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()

    skills_dir = workspace / ".agents" / "skills"
    skills_dir.mkdir(parents=True)
    scenario = load_scenario()
    for fixture_name in scenario.skill_fixture_names:
        source = SKILLS_SOURCE_ROOT / fixture_name
        if not source.is_file():
            raise FileNotFoundError(f"missing skill fixture: {source}")
        skill_name = fixture_name.replace(".md", "")
        target_dir = skills_dir / skill_name
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target_dir / "SKILL.md")

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
    (workspace / "config.yaml").write_text(
        f"""
tools:
  tiers:
    mode: shadow
    database:
      path: {db_path}
skills:
  tiers:
    mode: shadow
""",
        encoding="utf-8",
    )

    catalog_cache_dir = tmp_path / "cyt-mcp-catalog"
    catalog_cache_dir.mkdir()
    global_mcp_agg = tmp_path / "global-mcp-aggregator.yaml"
    global_mcp_defs = tmp_path / "global-mcp" / "cursor.json"
    global_mcp_defs.parent.mkdir(parents=True)
    global_mcp_agg.write_text("default_agent: cursor\n", encoding="utf-8")
    global_mcp_defs.write_text('{"mcpServers": {}}', encoding="utf-8")

    tools = load_tools_catalog()
    pack = TiersStatsFixturePack(
        workspace=workspace,
        db_path=db_path,
        catalog_cache_dir=catalog_cache_dir,
        tools=tools,
        scenario=scenario,
        global_mcp_agg=global_mcp_agg,
        global_mcp_defs=global_mcp_defs,
    )
    seed_tier_db(pack)
    return pack


class _CytMcpPathPack(Protocol):
    @property
    def catalog_cache_dir(self) -> Path: ...

    @property
    def global_mcp_agg(self) -> Path: ...

    @property
    def global_mcp_defs(self) -> Path: ...


def patch_cyt_mcp_paths(
    monkeypatch: pytest.MonkeyPatch,
    pack: _CytMcpPathPack,
) -> None:
    monkeypatch.setattr(
        "cyt.cyt_mcp.catalog_disk.cyt_mcp_catalog_cache_dir",
        lambda: pack.catalog_cache_dir,
    )
    monkeypatch.setattr(
        "cyt.cyt_mcp.catalog._global_scope_paths",
        lambda agent: (pack.global_mcp_agg, pack.global_mcp_defs),
    )
