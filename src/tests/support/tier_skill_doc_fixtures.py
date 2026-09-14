"""Fixtures for tier_state.db stale skill:doc entity regression tests."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cyt.config import load_config
from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.tiers.store import TierStore

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tier_skill_doc_guard"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"
POLLUTED_SEED_PATH = FIXTURES_ROOT / "polluted_seed.json"


@dataclass(frozen=True)
class SkillDocIntegrationScenario:
    id: str
    description: str


@dataclass(frozen=True)
class TierSkillDocGuardPack:
    user_tier_db_path: Path
    global_config_path: Path
    workspace: Path
    config: dict[str, Any]
    canonical_skill_path: Path


def load_scenarios(path: Path = SCENARIOS_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected object root")
    return payload


def load_polluted_seed(path: Path = POLLUTED_SEED_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected object root")
    return payload


def load_integration_scenarios(
    path: Path = SCENARIOS_PATH,
) -> tuple[SkillDocIntegrationScenario, ...]:
    payload = load_scenarios(path)
    rows = payload.get("integration_scenarios")
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected integration_scenarios array")
    return tuple(
        SkillDocIntegrationScenario(
            id=str(row["id"]),
            description=str(row.get("description") or ""),
        )
        for row in rows
        if isinstance(row, dict)
    )


def count_stale_skill_doc_rows(db_path: Path) -> int:
    if not db_path.is_file():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM entity_tier "
            "WHERE kind = 'skill' AND ("
            "entity_id IN ('skill:doc:skill', 'skill:doc:doc') "
            "OR entity_id LIKE 'skill:doc:%' "
            "OR entity_id = 'executor/execute'"
            ")",
        ).fetchone()
        return int(row[0]) if row is not None else 0
    finally:
        conn.close()


def count_skill_entity(db_path: Path, entity_id: str) -> int:
    if not db_path.is_file():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM entity_tier WHERE kind = 'skill' AND entity_id = ?",
            (entity_id,),
        ).fetchone()
        return int(row[0]) if row is not None else 0
    finally:
        conn.close()


def read_skill_used(db_path: Path, entity_id: str) -> float:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT used FROM entity_stats WHERE kind = 'skill' AND entity_id = ?",
            (entity_id,),
        ).fetchone()
        return float(row[0]) if row is not None else 0.0
    finally:
        conn.close()


def seed_polluted_skill_doc_db(
    *,
    db_path: Path,
    real_project_root: Path,
    canonical_skill_path: Path,
    seed: dict[str, Any] | None = None,
) -> None:
    seed = seed or load_polluted_seed()
    stale_ids = seed.get("stale_skill_doc_ids")
    duplicate = seed.get("duplicate_skill_doc")
    artifacts = seed.get("artifact_skill_entity_ids")
    if not isinstance(stale_ids, list) or not isinstance(duplicate, dict):
        raise ValueError("polluted seed requires stale_skill_doc_ids and duplicate_skill_doc")

    store = TierStore(str(db_path))
    try:
        project_id = store.get_or_create_project(str(real_project_root))
        now_ms = 1_700_000_000_000
        canonical = str(canonical_skill_path.resolve())

        store._conn.execute(
            "INSERT OR REPLACE INTO entity_tier("
            "project_id, kind, entity_id, stable_tier, effective_tier, "
            "tier_since_epoch, wake_lease_until_cycle, sleep_cooldown_until_cycle"
            ") VALUES (?, 'skill', ?, 1, 1, 1, 0, 0)",
            (project_id, canonical),
        )
        store._conn.execute(
            "INSERT OR REPLACE INTO entity_stats("
            "project_id, kind, entity_id, pipeline, candidates, injected, used, attempts, "
            "used_without_injection, optional_used, shadow_hits, shadow_evaluations, "
            "last_seen_ms, requests_since_decay, epoch_used, epoch_attempts"
            ") VALUES (?, 'skill', ?, 'default', 1, 0, 1, 0, 0, 0, 0, 0, ?, 0, 0, 0)",
            (project_id, canonical, now_ms),
        )

        for entity_id in stale_ids:
            store._conn.execute(
                "INSERT OR REPLACE INTO entity_tier("
                "project_id, kind, entity_id, stable_tier, effective_tier, "
                "tier_since_epoch, wake_lease_until_cycle, sleep_cooldown_until_cycle"
                ") VALUES (?, 'skill', ?, 2, 2, 1, 0, 0)",
                (project_id, str(entity_id)),
            )
            store._conn.execute(
                "INSERT OR REPLACE INTO entity_stats("
                "project_id, kind, entity_id, pipeline, candidates, injected, used, attempts, "
                "used_without_injection, optional_used, shadow_hits, shadow_evaluations, "
                "last_seen_ms, requests_since_decay, epoch_used, epoch_attempts"
                ") VALUES (?, 'skill', ?, 'default', 0, 0, 10, 0, 10, 0, 0, 0, 0, 0, 0, 0)",
                (project_id, str(entity_id)),
            )

        doc_entity = str(duplicate.get("doc_entity_id") or "")
        if doc_entity:
            store._conn.execute(
                "INSERT OR REPLACE INTO entity_tier("
                "project_id, kind, entity_id, stable_tier, effective_tier, "
                "tier_since_epoch, wake_lease_until_cycle, sleep_cooldown_until_cycle"
                ") VALUES (?, 'skill', ?, 0, 0, 1, 0, 0)",
                (project_id, doc_entity),
            )
            store._conn.execute(
                "INSERT OR REPLACE INTO entity_stats("
                "project_id, kind, entity_id, pipeline, candidates, injected, used, attempts, "
                "used_without_injection, optional_used, shadow_hits, shadow_evaluations, "
                "last_seen_ms, requests_since_decay, epoch_used, epoch_attempts"
                ") VALUES (?, 'skill', ?, 'default', 30, 0, 0, 0, 0, 0, 0, 0, ?, 0, 0, 0)",
                (project_id, doc_entity, now_ms),
            )

        if isinstance(artifacts, list):
            for entity_id in artifacts:
                store._conn.execute(
                    "INSERT OR REPLACE INTO entity_tier("
                    "project_id, kind, entity_id, stable_tier, effective_tier, "
                    "tier_since_epoch, wake_lease_until_cycle, sleep_cooldown_until_cycle"
                    ") VALUES (?, 'skill', ?, 0, 0, 1, 0, 0)",
                    (project_id, str(entity_id)),
                )
                store._conn.execute(
                    "INSERT OR REPLACE INTO entity_stats("
                    "project_id, kind, entity_id, pipeline, candidates, injected, used, attempts, "
                    "used_without_injection, optional_used, shadow_hits, shadow_evaluations, "
                    "last_seen_ms, requests_since_decay, epoch_used, epoch_attempts"
                    ") VALUES (?, 'skill', ?, 'default', 0, 0, 0, 0, 0, 0, 0, 0, ?, 0, 0, 0)",
                    (project_id, str(entity_id), now_ms),
                )

        store._conn.commit()
    finally:
        store.close()


def materialize_skill_doc_guard_pack(tmp_path: Path) -> TierSkillDocGuardPack:
    user_tier_db_path = tmp_path / "user_tier_state.db"
    global_config_path = tmp_path / "global" / "config.yaml"
    global_config_path.parent.mkdir(parents=True, exist_ok=True)
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
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    skill_dir = workspace / ".cursor" / "skills-cursor" / "create-hook"
    skill_dir.mkdir(parents=True)
    canonical_skill_path = skill_dir / "SKILL.md"
    canonical_skill_path.write_text(
        "---\nname: create-hook\ndescription: create hook skill\n---\n\n# Create Hook\n",
        encoding="utf-8",
    )
    config = set_hook_workspace_in_config(load_config(global_config_path), workspace)
    return TierSkillDocGuardPack(
        user_tier_db_path=user_tier_db_path,
        global_config_path=global_config_path,
        workspace=workspace,
        config=config,
        canonical_skill_path=canonical_skill_path,
    )


@pytest.fixture
def tier_skill_doc_guard_pack(tmp_path: Path) -> TierSkillDocGuardPack:
    return materialize_skill_doc_guard_pack(tmp_path)


__all__ = [
    "SkillDocIntegrationScenario",
    "TierSkillDocGuardPack",
    "count_skill_entity",
    "count_stale_skill_doc_rows",
    "load_integration_scenarios",
    "load_polluted_seed",
    "load_scenarios",
    "materialize_skill_doc_guard_pack",
    "read_skill_used",
    "seed_polluted_skill_doc_db",
    "tier_skill_doc_guard_pack",
]
