"""Fixture loader for tier DB ephemeral-path pollution regression tests."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cyt.config import load_config
from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.tiers.models import EffectiveStats, EntityKind, EntityTierState, Tier, TierProject
from cyt.tiers.store import TierStore

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tier_ephemeral_guard"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"
POLLUTED_SEED_PATH = FIXTURES_ROOT / "polluted_seed.json"


@dataclass(frozen=True)
class IntegrationScenario:
    id: str
    description: str


@dataclass(frozen=True)
class EphemeralGuardFixturePack:
    user_tier_db_path: Path
    global_config_path: Path
    ephemeral_workspace: Path
    real_repo_root: Path
    ephemeral_skill_path: Path
    config: dict[str, Any]


def load_scenarios(path: Path = SCENARIOS_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected object root")
    return payload


def load_integration_scenarios(
    path: Path = SCENARIOS_PATH,
) -> tuple[IntegrationScenario, ...]:
    payload = load_scenarios(path)
    rows = payload.get("integration_scenarios")
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected integration_scenarios array")
    return tuple(
        IntegrationScenario(
            id=str(row["id"]),
            description=str(row.get("description") or ""),
        )
        for row in rows
        if isinstance(row, dict)
    )


def load_polluted_seed(path: Path = POLLUTED_SEED_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected object root")
    return payload


def count_ephemeral_tier_rows(db_path: Path) -> dict[str, int]:
    empty = {"tier_project": 0, "entity_tier": 0, "entity_stats": 0}
    if not db_path.is_file():
        return empty
    conn = sqlite3.connect(str(db_path))
    try:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }
        if "tier_project" not in tables:
            return empty
        project_count = conn.execute(
            "SELECT COUNT(*) FROM tier_project "
            "WHERE root_path LIKE '%pytest%' OR root_path LIKE '/private/var/folders/%' "
            "OR root_path LIKE '/var/folders/%'",
        ).fetchone()[0]
        tier_count = conn.execute(
            "SELECT COUNT(*) FROM entity_tier "
            "WHERE entity_id LIKE '%pytest%' OR entity_id LIKE '/private/var/folders/%' "
            "OR entity_id LIKE '/var/folders/%'",
        ).fetchone()[0]
        stats_count = conn.execute(
            "SELECT COUNT(*) FROM entity_stats "
            "WHERE entity_id LIKE '%pytest%' OR entity_id LIKE '/private/var/folders/%' "
            "OR entity_id LIKE '/var/folders/%'",
        ).fetchone()[0]
        return {
            "tier_project": int(project_count),
            "entity_tier": int(tier_count),
            "entity_stats": int(stats_count),
        }
    finally:
        conn.close()


def seed_polluted_user_db(
    *,
    db_path: Path,
    real_project_root: Path,
    seed: dict[str, Any] | None = None,
) -> None:
    """Write the historical pollution shape into an isolated user-tier DB."""
    seed = seed or load_polluted_seed()
    ephemeral_projects = seed.get("ephemeral_projects")
    ephemeral_skill_ids = seed.get("ephemeral_skill_entity_ids")
    if not isinstance(ephemeral_projects, list) or not isinstance(ephemeral_skill_ids, list):
        raise ValueError("polluted seed requires ephemeral_projects and ephemeral_skill_entity_ids")

    store = TierStore(str(db_path))
    try:
        real_project_id = store.get_or_create_project(str(real_project_root))
        now_ms = 1_700_000_000_000
        for root_path in ephemeral_projects:
            store._conn.execute(
                "INSERT OR IGNORE INTO tier_project(root_path, created_ms, last_seen_ms) VALUES (?, ?, ?)",
                (str(root_path), now_ms, now_ms),
            )
        for entity_id in ephemeral_skill_ids:
            store._conn.execute(
                "INSERT OR REPLACE INTO entity_tier("
                "project_id, kind, entity_id, stable_tier, effective_tier, "
                "tier_since_epoch, wake_lease_until_cycle, sleep_cooldown_until_cycle"
                ") VALUES (?, 'skill', ?, 2, 2, 1, 0, 0)",
                (real_project_id, str(entity_id)),
            )
            store._conn.execute(
                "INSERT OR REPLACE INTO entity_stats("
                "project_id, kind, entity_id, pipeline, candidates, injected, used, attempts, "
                "used_without_injection, optional_used, shadow_hits, shadow_evaluations, "
                "last_seen_ms, requests_since_decay, epoch_used, epoch_attempts"
                ") VALUES (?, 'skill', ?, 'default', 1, 0, 0, 0, 0, 0, 0, 0, ?, 0, 0, 0)",
                (real_project_id, str(entity_id), now_ms),
            )
        store._conn.commit()
    finally:
        store.close()


def user_tier_config(
    *,
    global_config_path: Path,
    user_tier_db_path: Path,
    workspace: Path | None = None,
) -> dict[str, Any]:
    config = load_config(global_config_path)
    tools = dict(config.get("tools") or {})
    tiers = dict(tools.get("tiers") or {})
    tiers["enabled"] = True
    tiers["mode"] = "shadow"
    tiers["database"] = {"path": str(user_tier_db_path), "disk_flush_seconds": 0}
    tools["tiers"] = tiers
    config["tools"] = tools
    skills = dict(config.get("skills") or {})
    skills["enabled"] = True
    skills["tiers"] = {"mode": "shadow"}
    config["skills"] = skills
    if workspace is not None:
        config = set_hook_workspace_in_config(config, workspace)
    return config


def materialize_ephemeral_guard_pack(tmp_path: Path) -> EphemeralGuardFixturePack:
    scenarios = load_scenarios()
    skill_paths = scenarios.get("ephemeral_skill_paths")
    if not isinstance(skill_paths, list) or not skill_paths:
        raise ValueError("scenarios.json must define ephemeral_skill_paths")

    user_tier_db_path = tmp_path / "user_tier_state.db"
    global_config_path = tmp_path / "global" / "config.yaml"
    global_config_path.parent.mkdir(parents=True)
    global_config_path.write_text(
        "tools:\n"
        "  tiers:\n"
        "    enabled: true\n"
        "    mode: shadow\n"
        f"    database:\n"
        f"      path: {user_tier_db_path}\n"
        "      disk_flush_seconds: 0\n"
        "skills:\n"
        "  enabled: true\n"
        "  tiers:\n"
        "    mode: shadow\n",
        encoding="utf-8",
    )

    ephemeral_workspace = tmp_path / "repo"
    ephemeral_workspace.mkdir()
    (ephemeral_workspace / ".git").mkdir()
    skill_dir = ephemeral_workspace / ".cursor" / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\nname: demo\ndescription: demo skill\n---\n\n# Demo\n",
        encoding="utf-8",
    )

    real_repo_root = Path(__file__).resolve().parents[3]
    config = user_tier_config(
        global_config_path=global_config_path,
        user_tier_db_path=user_tier_db_path,
        workspace=ephemeral_workspace,
    )

    return EphemeralGuardFixturePack(
        user_tier_db_path=user_tier_db_path,
        global_config_path=global_config_path,
        ephemeral_workspace=ephemeral_workspace,
        real_repo_root=real_repo_root,
        ephemeral_skill_path=Path(str(skill_paths[0])),
        config=config,
    )


def open_real_project_with_polluted_seed(pack: EphemeralGuardFixturePack) -> TierStore:
    seed_polluted_user_db(
        db_path=pack.user_tier_db_path,
        real_project_root=pack.real_repo_root,
    )
    return TierStore.open(str(pack.user_tier_db_path))


def stale_ephemeral_skill_state(entity_id: str) -> EntityTierState:
    return EntityTierState(
        entity_id=entity_id,
        kind=EntityKind.SKILL,
        stable_tier=Tier.ACTIVE,
        effective_tier=Tier.ACTIVE,
        stats=EffectiveStats(candidates=9.0, injected=1.0),
    )


def real_project_from_store(store: TierStore, root: Path) -> TierProject:
    project_id = store.get_or_create_project(str(root))
    return TierProject(project_id=project_id, root_path=root.resolve())


@pytest.fixture
def ephemeral_guard_pack(tmp_path: Path) -> EphemeralGuardFixturePack:
    return materialize_ephemeral_guard_pack(tmp_path)


@pytest.fixture
def clear_tier_manager_cache() -> Iterator[None]:
    from cyt.tiers.manager import _managers

    _managers.clear()
    yield
    _managers.clear()


__all__ = [
    "EphemeralGuardFixturePack",
    "IntegrationScenario",
    "clear_tier_manager_cache",
    "count_ephemeral_tier_rows",
    "ephemeral_guard_pack",
    "load_integration_scenarios",
    "load_polluted_seed",
    "load_scenarios",
    "materialize_ephemeral_guard_pack",
    "open_real_project_with_polluted_seed",
    "real_project_from_store",
    "seed_polluted_user_db",
    "stale_ephemeral_skill_state",
    "user_tier_config",
]
