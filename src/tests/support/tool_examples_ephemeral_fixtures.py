"""Fixtures for tool_examples.db ephemeral-path pollution regression tests."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cyt.config import load_config
from cyt.hook.workspace_config import set_hook_workspace_in_config

FIXTURES_ROOT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "tool_examples_ephemeral_guard"
)
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"
POLLUTED_SEED_PATH = FIXTURES_ROOT / "polluted_seed.json"


@dataclass(frozen=True)
class ToolExamplesIntegrationScenario:
    id: str
    description: str


@dataclass(frozen=True)
class ToolExamplesGuardPack:
    user_examples_db_path: Path
    global_config_path: Path
    ephemeral_workspace: Path
    real_repo_root: Path
    config: dict[str, Any]


def load_scenarios(path: Path = SCENARIOS_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected object root")
    return payload


def load_integration_scenarios(
    path: Path = SCENARIOS_PATH,
) -> tuple[ToolExamplesIntegrationScenario, ...]:
    payload = load_scenarios(path)
    rows = payload.get("integration_scenarios")
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected integration_scenarios array")
    return tuple(
        ToolExamplesIntegrationScenario(
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


def count_ephemeral_tool_example_projects(db_path: Path) -> int:
    if not db_path.is_file():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }
        if "tool_example_project" not in tables:
            return 0
        row = conn.execute(
            "SELECT COUNT(*) FROM tool_example_project "
            "WHERE root_path LIKE '%pytest%' OR root_path LIKE '/private/var/folders/%' "
            "OR root_path LIKE '/var/folders/%'",
        ).fetchone()
        return int(row[0]) if row is not None else 0
    finally:
        conn.close()


def seed_polluted_tool_examples_db(
    *,
    db_path: Path,
    real_project_root: Path,
    seed: dict[str, Any] | None = None,
) -> None:
    seed = seed or load_polluted_seed()
    ephemeral_projects = seed.get("ephemeral_projects")
    if not isinstance(ephemeral_projects, list):
        raise ValueError("polluted seed requires ephemeral_projects")

    from cyt.tool_examples.store import ToolExamplesStore

    store = ToolExamplesStore(str(db_path))
    try:
        real_project_id = store.get_or_create_project(str(real_project_root))
        now_ms = 1_700_000_000_000
        for root_path in ephemeral_projects:
            store._conn.execute(
                "INSERT OR IGNORE INTO tool_example_project(root_path, created_ms, last_seen_ms) "
                "VALUES (?, ?, ?)",
                (str(root_path), now_ms, now_ms),
            )
        store._conn.execute(
            "INSERT OR REPLACE INTO tool_input_schema("
            "project_id, mcp_server, tool_name, schema_json, input_json, "
            "schema_hash, input_hash, first_seen_ms, last_seen_ms"
            ") VALUES (?, 'gitnexus', 'query', '{}', '{\"search_query\":\"x\"}', "
            "'legacy', 'legacy', ?, ?)",
            (real_project_id, now_ms, now_ms),
        )
        store._conn.commit()
    finally:
        store.close()


def materialize_tool_examples_guard_pack(tmp_path: Path) -> ToolExamplesGuardPack:
    user_examples_db_path = tmp_path / "user_tool_examples.db"
    global_config_path = tmp_path / "global" / "config.yaml"
    global_config_path.parent.mkdir(parents=True, exist_ok=True)
    global_config_path.write_text(
        "tools:\n"
        "  examples:\n"
        "    enabled: true\n"
        f"    database:\n"
        f"      path: {user_examples_db_path}\n"
        "skills:\n"
        "  enabled: false\n",
        encoding="utf-8",
    )

    ephemeral_workspace = tmp_path / "repo"
    ephemeral_workspace.mkdir()
    (ephemeral_workspace / ".git").mkdir()

    real_repo_root = Path(__file__).resolve().parents[3]
    config = set_hook_workspace_in_config(
        load_config(global_config_path),
        ephemeral_workspace,
    )
    return ToolExamplesGuardPack(
        user_examples_db_path=user_examples_db_path,
        global_config_path=global_config_path,
        ephemeral_workspace=ephemeral_workspace,
        real_repo_root=real_repo_root,
        config=config,
    )


@pytest.fixture(autouse=True)
def allow_tool_examples_on_ephemeral_workspace(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Most tool-example unit tests use pytest tmp dirs as workspaces."""
    module_name = getattr(request.node.module, "__name__", "")
    if "ephemeral_guard" in module_name:
        return
    if "skips_ephemeral" in request.node.name:
        return
    monkeypatch.setattr(
        "cyt.tool_examples.record.is_ephemeral_workspace_path",
        lambda _path: False,
    )
    monkeypatch.setattr(
        "cyt.tool_examples.enrich.is_ephemeral_workspace_path",
        lambda _path: False,
    )


@pytest.fixture
def tool_examples_guard_pack(tmp_path: Path) -> ToolExamplesGuardPack:
    return materialize_tool_examples_guard_pack(tmp_path)


__all__ = [
    "ToolExamplesGuardPack",
    "ToolExamplesIntegrationScenario",
    "allow_tool_examples_on_ephemeral_workspace",
    "count_ephemeral_tool_example_projects",
    "load_integration_scenarios",
    "load_polluted_seed",
    "load_scenarios",
    "materialize_tool_examples_guard_pack",
    "seed_polluted_tool_examples_db",
    "tool_examples_guard_pack",
]
