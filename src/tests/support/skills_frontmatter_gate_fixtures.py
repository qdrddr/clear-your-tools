"""Shared fixture loader for BM25 frontmatter gate tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.skills.catalog import SkillEntryRef, build_registry, clear_registry_cache
from tests.support.skills_helpers import isolated_skills_agents_block

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "skills_frontmatter_gate"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"


@dataclass(frozen=True)
class FrontmatterGateScenario:
    id: str
    query: str
    blocked_doc_ids: frozenset[str]
    eligible_doc_ids: frozenset[str]


@dataclass(frozen=True)
class FrontmatterGateFixturePack:
    frontmatter_upper_limit: float
    scenarios: tuple[FrontmatterGateScenario, ...]
    skills_dir: Path
    catalog_dir: Path
    workspace: Path


def load_scenarios(
    path: Path = SCENARIOS_PATH,
) -> tuple[float, tuple[FrontmatterGateScenario, ...]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    upper = float(payload["frontmatter_upper_limit"])
    scenarios: list[FrontmatterGateScenario] = []
    for row in payload["scenarios"]:
        scenarios.append(
            FrontmatterGateScenario(
                id=str(row["id"]),
                query=str(row["query"]),
                blocked_doc_ids=frozenset(str(doc_id) for doc_id in row["blocked_doc_ids"]),
                eligible_doc_ids=frozenset(str(doc_id) for doc_id in row["eligible_doc_ids"]),
            ),
        )
    return upper, tuple(scenarios)


def skills_gate_config(
    *,
    workspace: Path,
    catalog_dir: Path,
    skills_dir: Path,
    frontmatter_upper_limit: float,
    score_skills: float = 0.0,
) -> dict[str, Any]:
    return set_hook_workspace_in_config(
        {
            "cache": {"skills_dir": str(catalog_dir)},
            "skills": {
                "enabled": True,
                "pipeline": "bm25",
                "catalog_dir": str(catalog_dir),
                "directories": [str(skills_dir)],
                "frontmatter_upper_limit": frontmatter_upper_limit,
                "max_tokens_per_request": 4000,
                "pageindex": {"enable_bm25_chunking": True},
                "hook": {
                    "request_budget_fraction": 50.0,
                    "inject_cap_multiplier_of_request_tokens": 5.0,
                },
                "proxy": {
                    "request_budget_fraction": 10.0,
                    "inject_cap_fraction_of_savings": 0.5,
                    "savings_budget_fraction": 0.1,
                    "savings_rate_threshold": 0.20,
                },
            },
            "pruning": {
                "inject_via": {"cursor": "hook", "claude": "proxy", "codex": "proxy"},
                "tools": {"pipelines": {"bm25": {"score_skills": score_skills}}},
            },
            "agents": isolated_skills_agents_block(),
        },
        workspace,
    )


def materialize_fixture_pack(tmp_path: Path) -> FrontmatterGateFixturePack:
    upper, scenarios = load_scenarios()
    workspace = tmp_path / "workspace"
    catalog_dir = tmp_path / "catalog"
    # Mirror real agent layout so pytest temp paths are not treated as ephemeral
    # copies (see ``is_ephemeral_skill_path``).
    skills_dir = workspace / ".cursor" / "skills"
    workspace.mkdir()
    catalog_dir.mkdir()
    skills_dir.mkdir(parents=True)

    for fixture_path in sorted(FIXTURES_ROOT.glob("*.md")):
        (skills_dir / fixture_path.name).write_text(
            fixture_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    return FrontmatterGateFixturePack(
        frontmatter_upper_limit=upper,
        scenarios=scenarios,
        skills_dir=skills_dir,
        catalog_dir=catalog_dir,
        workspace=workspace,
    )


def build_registry_from_fixture_pack(pack: FrontmatterGateFixturePack) -> list[SkillEntryRef]:
    config = skills_gate_config(
        workspace=pack.workspace,
        catalog_dir=pack.catalog_dir,
        skills_dir=pack.skills_dir,
        frontmatter_upper_limit=pack.frontmatter_upper_limit,
    )
    clear_registry_cache()
    return build_registry(config)


def client_payload_for_fixture_pack(pack: FrontmatterGateFixturePack) -> dict[str, Any]:
    cyt_skills: list[dict[str, str]] = []
    for path in sorted(pack.skills_dir.glob("*.md")):
        cyt_skills.append(
            {
                "path": str(path.resolve()),
                "content": path.read_text(encoding="utf-8"),
            },
        )
    return {"cyt_skills": cyt_skills}
