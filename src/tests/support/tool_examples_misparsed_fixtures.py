"""Fixtures for tool_examples.db misparsed-identity regression tests."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cyt.config import load_config
from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt_mcp.tool_identity import is_canonical_schema_identity

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tool_examples_misparsed_identity"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"
POLLUTED_SEED_PATH = FIXTURES_ROOT / "polluted_seed.json"


@dataclass(frozen=True)
class CaptureToolScenario:
    id: str
    catalog_tool: dict[str, Any]
    payload_tool_name: str
    tool_input: dict[str, Any]
    expected_mcp_server: str
    expected_tool_name: str


@dataclass(frozen=True)
class MisparsedIntegrationScenario:
    id: str
    description: str


@dataclass(frozen=True)
class ToolExamplesMisparsedPack:
    user_examples_db_path: Path
    global_config_path: Path
    workspace: Path
    config: dict[str, Any]
    server_keys: tuple[str, ...]


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


def load_server_keys(path: Path = SCENARIOS_PATH) -> tuple[str, ...]:
    payload = load_scenarios(path)
    raw = payload.get("server_keys")
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected server_keys array")
    return tuple(str(item).strip() for item in raw if str(item).strip())


def load_capture_tool_scenarios(path: Path = SCENARIOS_PATH) -> tuple[CaptureToolScenario, ...]:
    payload = load_scenarios(path)
    rows = payload.get("capture_tools")
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected capture_tools array")
    return tuple(
        CaptureToolScenario(
            id=str(row["id"]),
            catalog_tool=dict(row["catalog_tool"]),
            payload_tool_name=str(row["payload_tool_name"]),
            tool_input=dict(row.get("tool_input") or {}),
            expected_mcp_server=str(row["expected_mcp_server"]),
            expected_tool_name=str(row["expected_tool_name"]),
        )
        for row in rows
        if isinstance(row, dict)
    )


def load_integration_scenarios(
    path: Path = SCENARIOS_PATH,
) -> tuple[MisparsedIntegrationScenario, ...]:
    payload = load_scenarios(path)
    rows = payload.get("integration_scenarios")
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected integration_scenarios array")
    return tuple(
        MisparsedIntegrationScenario(
            id=str(row["id"]),
            description=str(row.get("description") or ""),
        )
        for row in rows
        if isinstance(row, dict)
    )


def count_misparsed_schemas(db_path: Path, server_keys: list[str]) -> int:
    if not db_path.is_file():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT mcp_server, tool_name FROM tool_input_schema",
        ).fetchall()
        return sum(
            1
            for mcp_server, tool_name in rows
            if not is_canonical_schema_identity(str(mcp_server), str(tool_name), server_keys)
        )
    finally:
        conn.close()


def count_orphan_examples(db_path: Path) -> int:
    if not db_path.is_file():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM tool_example "
            "WHERE schema_id NOT IN (SELECT schema_id FROM tool_input_schema)",
        ).fetchone()
        return int(row[0]) if row is not None else 0
    finally:
        conn.close()


def seed_polluted_misparsed_db(
    *,
    db_path: Path,
    real_project_root: Path,
    seed: dict[str, Any] | None = None,
) -> None:
    seed = seed or load_polluted_seed()
    misparsed = seed.get("misparsed_schemas")
    canonical = seed.get("canonical_schemas")
    if not isinstance(misparsed, list) or not isinstance(canonical, list):
        raise ValueError("polluted seed requires misparsed_schemas and canonical_schemas")

    from cyt.tool_examples.store import ToolExamplesStore

    store = ToolExamplesStore(str(db_path))
    try:
        project_id = store.get_or_create_project(str(real_project_root))
        now_ms = 1_700_000_000_000
        for row in misparsed:
            if not isinstance(row, dict):
                continue
            store._conn.execute(
                "INSERT INTO tool_input_schema("
                "project_id, mcp_server, tool_name, schema_json, input_json, "
                "schema_hash, input_hash, first_seen_ms, last_seen_ms"
                ") VALUES (?, ?, ?, '{}', '{}', 'bad', 'bad', ?, ?)",
                (
                    project_id,
                    str(row.get("mcp_server") or ""),
                    str(row.get("tool_name") or ""),
                    now_ms,
                    now_ms,
                ),
            )
        for row in canonical:
            if not isinstance(row, dict):
                continue
            store._conn.execute(
                "INSERT INTO tool_input_schema("
                "project_id, mcp_server, tool_name, schema_json, input_json, "
                "schema_hash, input_hash, first_seen_ms, last_seen_ms"
                ") VALUES (?, ?, ?, '{}', '{}', 'good', 'good', ?, ?)",
                (
                    project_id,
                    str(row.get("mcp_server") or ""),
                    str(row.get("tool_name") or ""),
                    now_ms,
                    now_ms,
                ),
            )
        store._conn.commit()
    finally:
        store.close()

    orphan_conn = sqlite3.connect(str(db_path))
    try:
        orphan_conn.execute("PRAGMA foreign_keys=OFF")
        for schema_id in (9001, 9002):
            orphan_conn.execute(
                "INSERT INTO tool_example("
                "schema_id, json_path, value, value_type, timestamp_ms, success_count"
                ") VALUES (?, 'inputSchema.properties.query', '\"x\"', 'string', ?, 1)",
                (schema_id, now_ms),
            )
        orphan_conn.commit()
    finally:
        orphan_conn.close()


def materialize_misparsed_pack(tmp_path: Path) -> ToolExamplesMisparsedPack:
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
    return ToolExamplesMisparsedPack(
        user_examples_db_path=user_examples_db_path,
        global_config_path=global_config_path,
        workspace=workspace,
        config=config,
        server_keys=load_server_keys(),
    )


def write_capture_session_log(
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
                "hash": "misparsed-test",
                "tools": [catalog_tool],
            },
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def tool_examples_misparsed_pack(tmp_path: Path) -> ToolExamplesMisparsedPack:
    return materialize_misparsed_pack(tmp_path)


__all__ = [
    "CaptureToolScenario",
    "MisparsedIntegrationScenario",
    "ToolExamplesMisparsedPack",
    "count_misparsed_schemas",
    "count_orphan_examples",
    "load_capture_tool_scenarios",
    "load_integration_scenarios",
    "load_polluted_seed",
    "load_scenarios",
    "load_server_keys",
    "materialize_misparsed_pack",
    "seed_polluted_misparsed_db",
    "tool_examples_misparsed_pack",
    "write_capture_session_log",
]
