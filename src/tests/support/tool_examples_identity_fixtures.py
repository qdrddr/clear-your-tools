"""Fixtures for tool_examples.db identity regression tests."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cyt.config import load_config
from cyt.hook.workspace_config import set_hook_workspace_in_config

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tool_examples_identity"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"
POLLUTED_SEED_PATH = FIXTURES_ROOT / "polluted_seed.json"


@dataclass(frozen=True)
class SessionCatalogScenario:
    id: str
    catalog_tool: dict[str, Any]
    payload_tool_name: str
    tool_input: dict[str, Any]
    expected_mcp_server: str | None
    expected_tool_name: str | None


@dataclass(frozen=True)
class IdentityIntegrationScenario:
    id: str
    description: str


@dataclass(frozen=True)
class ToolExamplesIdentityPack:
    user_examples_db_path: Path
    global_config_path: Path
    workspace: Path
    config: dict[str, Any]


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


def load_session_catalog_scenarios(
    path: Path = SCENARIOS_PATH,
) -> tuple[SessionCatalogScenario, ...]:
    payload = load_scenarios(path)
    rows = payload.get("session_catalog_tools")
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected session_catalog_tools array")
    return tuple(
        SessionCatalogScenario(
            id=str(row["id"]),
            catalog_tool=dict(row["catalog_tool"]),
            payload_tool_name=str(row["payload_tool_name"]),
            tool_input=dict(row.get("tool_input") or {}),
            expected_mcp_server=(
                str(row["expected_mcp_server"])
                if row.get("expected_mcp_server") not in (None, "")
                else None
            ),
            expected_tool_name=(
                str(row["expected_tool_name"])
                if row.get("expected_tool_name") not in (None, "")
                else None
            ),
        )
        for row in rows
        if isinstance(row, dict)
    )


def load_integration_scenarios(
    path: Path = SCENARIOS_PATH,
) -> tuple[IdentityIntegrationScenario, ...]:
    payload = load_scenarios(path)
    rows = payload.get("integration_scenarios")
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected integration_scenarios array")
    return tuple(
        IdentityIntegrationScenario(
            id=str(row["id"]),
            description=str(row.get("description") or ""),
        )
        for row in rows
        if isinstance(row, dict)
    )


def count_invalid_identity_schemas(db_path: Path) -> int:
    if not db_path.is_file():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM tool_input_schema "
            "WHERE trim(mcp_server) = '' OR lower(trim(mcp_server)) = 'unknown' "
            "OR trim(tool_name) = '' OR lower(trim(tool_name)) = 'unknown'",
        ).fetchone()
        return int(row[0]) if row is not None else 0
    finally:
        conn.close()


def seed_polluted_identity_db(
    *,
    db_path: Path,
    real_project_root: Path,
    seed: dict[str, Any] | None = None,
) -> None:
    seed = seed or load_polluted_seed()
    invalid_schemas = seed.get("invalid_schemas")
    valid_schema = seed.get("valid_schema")
    if not isinstance(invalid_schemas, list) or not isinstance(valid_schema, dict):
        raise ValueError("polluted seed requires invalid_schemas and valid_schema")

    from cyt.tool_examples.store import ToolExamplesStore

    store = ToolExamplesStore(str(db_path))
    try:
        project_id = store.get_or_create_project(str(real_project_root))
        now_ms = 1_700_000_000_000
        for row in invalid_schemas:
            if not isinstance(row, dict):
                continue
            store._conn.execute(
                "INSERT INTO tool_input_schema("
                "project_id, mcp_server, tool_name, schema_json, input_json, "
                "schema_hash, input_hash, first_seen_ms, last_seen_ms"
                ") VALUES (?, ?, ?, '{}', '{}', 'legacy', 'legacy', ?, ?)",
                (
                    project_id,
                    str(row.get("mcp_server") or ""),
                    str(row.get("tool_name") or ""),
                    now_ms,
                    now_ms,
                ),
            )
        store._conn.execute(
            "INSERT OR REPLACE INTO tool_input_schema("
            "project_id, mcp_server, tool_name, schema_json, input_json, "
            "schema_hash, input_hash, first_seen_ms, last_seen_ms"
            ") VALUES (?, ?, ?, '{}', '{\"search_query\":\"x\"}', "
            "'valid', 'valid', ?, ?)",
            (
                project_id,
                str(valid_schema["mcp_server"]),
                str(valid_schema["tool_name"]),
                now_ms,
                now_ms,
            ),
        )
        store._conn.commit()
    finally:
        store.close()


def materialize_identity_pack(tmp_path: Path) -> ToolExamplesIdentityPack:
    user_examples_db_path = tmp_path / "user_tool_examples.db"
    global_config_path = tmp_path / "global" / "config.yaml"
    global_config_path.parent.mkdir(parents=True, exist_ok=True)
    global_config_path.write_text(
        "tools:\n"
        "  examples:\n"
        "    enabled: true\n"
        f"    database:\n"
        f"      path: {user_examples_db_path}\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    config = set_hook_workspace_in_config(load_config(global_config_path), workspace)
    return ToolExamplesIdentityPack(
        user_examples_db_path=user_examples_db_path,
        global_config_path=global_config_path,
        workspace=workspace,
        config=config,
    )


def write_session_catalog_log(
    log_path: Path,
    *,
    catalog_tool: dict[str, Any],
) -> None:
    log_path.write_text(
        json.dumps(
            {
                "kind": "tool_catalog",
                "key": "tool_catalog:cyt_mcp",
                "catalog": "cyt_mcp",
                "hash": "identity-test",
                "tools": [catalog_tool],
            },
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def tool_examples_identity_pack(tmp_path: Path) -> ToolExamplesIdentityPack:
    return materialize_identity_pack(tmp_path)


__all__ = [
    "IdentityIntegrationScenario",
    "SessionCatalogScenario",
    "ToolExamplesIdentityPack",
    "count_invalid_identity_schemas",
    "load_integration_scenarios",
    "load_polluted_seed",
    "load_scenarios",
    "load_session_catalog_scenarios",
    "materialize_identity_pack",
    "seed_polluted_identity_db",
    "tool_examples_identity_pack",
    "write_session_catalog_log",
]
