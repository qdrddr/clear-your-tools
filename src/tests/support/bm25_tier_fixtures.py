"""Fixture loader for tier-scoped BM25 prune golden tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cyt.cyt_mcp.catalog import apply_fetched_catalog
from cyt.hook.workspace_config import set_hook_workspace_in_config
from tests.support.paths import FIXTURES_DIR
from tests.support.tier_seed_helpers import seed_tiers_from_mapping
from tests.support.tiers_stats_fixtures import patch_cyt_mcp_paths

TIERED_FIXTURE_DIR = FIXTURES_DIR / "cyt_mcp_catalog_tiered"
TIERED_INPUT_DIR = TIERED_FIXTURE_DIR / "input"
TIERED_OUTPUT_DIR = TIERED_FIXTURE_DIR / "out"
TIERED_SCENARIOS_PATH = TIERED_FIXTURE_DIR / "scenarios.json"
CATALOG_FIXTURE = FIXTURES_DIR / "cyt_mcp_catalog" / "input" / "tools.json"
CATALOG_INPUT_DIR = FIXTURES_DIR / "cyt_mcp_catalog" / "input"


@dataclass(frozen=True)
class Bm25TierScenario:
    id: str
    base_golden: str
    tool_tiers: dict[str, str]
    must_exclude_tools: frozenset[str]
    must_include_tools: frozenset[str]


@dataclass(frozen=True)
class Bm25TierRunContext:
    workspace: Path
    db_path: Path
    catalog_cache_dir: Path
    global_mcp_agg: Path
    global_mcp_defs: Path
    scenario: Bm25TierScenario
    golden_params: dict[str, Any]
    tools: list[dict[str, Any]]


def _load_json(path: Path) -> dict[str, Any]:
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: expected JSON object")
    return loaded


def load_bm25_tier_scenarios(path: Path = TIERED_SCENARIOS_PATH) -> tuple[Bm25TierScenario, ...]:
    payload = _load_json(path)
    rows = payload.get("scenarios")
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected scenarios array")
    scenarios: list[Bm25TierScenario] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        tool_tiers_raw = row.get("tool_tiers") or {}
        scenarios.append(
            Bm25TierScenario(
                id=str(row["id"]),
                base_golden=str(row["base_golden"]),
                tool_tiers={
                    str(entity_id): str(tier_name)
                    for entity_id, tier_name in dict(tool_tiers_raw).items()
                },
                must_exclude_tools=frozenset(
                    str(name) for name in row.get("must_exclude_tools", [])
                ),
                must_include_tools=frozenset(
                    str(name) for name in row.get("must_include_tools", [])
                ),
            ),
        )
    return tuple(scenarios)


def scenario_input_path(scenario_id: str) -> Path:
    return TIERED_INPUT_DIR / f"bm25_tier_{scenario_id}.json"


def scenario_output_path(scenario_id: str) -> Path:
    return TIERED_OUTPUT_DIR / f"bm25_tier_{scenario_id}.json"


def load_scenario_input(scenario_id: str) -> dict[str, Any]:
    return _load_json(scenario_input_path(scenario_id))


def load_tools_catalog() -> list[dict[str, Any]]:
    catalog = _load_json(CATALOG_FIXTURE)
    tools = catalog.get("tools")
    if not isinstance(tools, list):
        raise ValueError(f"{CATALOG_FIXTURE}: expected tools array")
    return [dict(tool) for tool in tools if isinstance(tool, dict)]


def materialize_bm25_tier_run(
    scenario: Bm25TierScenario,
    tmp_path: Path,
) -> Bm25TierRunContext:
    base_golden_path = CATALOG_INPUT_DIR / scenario.base_golden
    if not base_golden_path.is_file():
        raise FileNotFoundError(f"missing base golden: {base_golden_path}")
    golden_params = _load_json(base_golden_path)

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()

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
        '{"mcpServers": {"context-mode": {}, "gitnexus": {}, "jcodemunch": {}}}',
        encoding="utf-8",
    )

    db_path = workspace / "tier_state.db"
    catalog_cache_dir = tmp_path / "cyt-mcp-catalog"
    catalog_cache_dir.mkdir()
    global_mcp_agg = tmp_path / "global-mcp-aggregator.yaml"
    global_mcp_defs = tmp_path / "global-mcp" / "cursor.json"
    global_mcp_defs.parent.mkdir(parents=True)
    global_mcp_agg.write_text("default_agent: cursor\n", encoding="utf-8")
    global_mcp_defs.write_text('{"mcpServers": {}}', encoding="utf-8")

    tools = load_tools_catalog()
    seed_tiers_from_mapping(
        workspace=workspace,
        db_path=db_path,
        tool_tiers=scenario.tool_tiers,
    )

    return Bm25TierRunContext(
        workspace=workspace,
        db_path=db_path,
        catalog_cache_dir=catalog_cache_dir,
        global_mcp_agg=global_mcp_agg,
        global_mcp_defs=global_mcp_defs,
        scenario=scenario,
        golden_params=golden_params,
        tools=tools,
    )


def live_bm25_tier_config(
    ctx: Bm25TierRunContext,
    tmp_path: Path,
) -> dict[str, Any]:
    golden = ctx.golden_params
    index_dir = str(tmp_path / "bm25")
    bm25_pipeline: dict[str, Any] = {
        "index_dir": index_dir,
        "score_tool": float(golden["score_tool"]),
        "score_tool_enum": float(golden["score_tool_enum"]),
        "prune_enums": bool(golden["prune_enums"]),
    }
    return set_hook_workspace_in_config(
        {
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
            "tools": {
                "enabled": True,
                "tiers": {
                    "mode": "live",
                    "database": {"path": str(ctx.db_path)},
                },
                "sequence": ["bm25"],
                "policy": {
                    "system_tool": "prune_optional",
                    "mcp_tool": "prune_all",
                    "minimum_tools": 5,
                    "per_tool": {},
                },
                "pipelines": {"bm25": bm25_pipeline},
            },
            "models": {
                "bm25": {
                    "index_dir": index_dir,
                    "mmap": False,
                    "stem_language": "english",
                    "stopwords": "en",
                },
            },
        },
        ctx.workspace,
    )


def prepare_bm25_tier_run(
    ctx: Bm25TierRunContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Bm25TierRunContext, dict[str, Any]]:
    """Patch cyt-mcp paths, write disk catalog, return live config."""
    patch_cyt_mcp_paths(monkeypatch, ctx)
    config = live_bm25_tier_config(ctx, tmp_path)
    apply_fetched_catalog(config, ctx.tools)
    return ctx, config


def patch_cyt_mcp_paths_for_ctx(
    monkeypatch: pytest.MonkeyPatch,
    ctx: Bm25TierRunContext,
) -> None:
    patch_cyt_mcp_paths(monkeypatch, ctx)


__all__ = [
    "CATALOG_FIXTURE",
    "TIERED_FIXTURE_DIR",
    "TIERED_OUTPUT_DIR",
    "Bm25TierRunContext",
    "Bm25TierScenario",
    "live_bm25_tier_config",
    "load_bm25_tier_scenarios",
    "load_scenario_input",
    "materialize_bm25_tier_run",
    "patch_cyt_mcp_paths_for_ctx",
    "prepare_bm25_tier_run",
    "scenario_input_path",
    "scenario_output_path",
]
