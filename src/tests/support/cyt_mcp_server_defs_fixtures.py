"""Fixtures for workspace MCP server defs (.agents/cyt/config/mcp/cursor.json) contract tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "cyt_mcp_server_defs"
YAML_CURSOR_DEFS_PATH = FIXTURES_DIR / "yaml_cursor.json.fixture"
VALID_CURSOR_DEFS_PATH = FIXTURES_DIR / "valid_cursor.json.fixture"
SCENARIOS_PATH = FIXTURES_DIR / "scenarios.json"

WORKSPACE_AGGREGATOR_YAML = """\
default_agent: cursor
agents:
  cursor: mcp/cursor.json
catalog_scope: workspace
transport: stdio
verify_only: false
http:
  host: 127.0.0.1
  port: 8766
  mcp_path: /mcp
  catalog_path: /catalog
"""


@dataclass(frozen=True)
class ServerDefsScenario:
    id: str
    description: str
    raw: dict[str, Any]


def load_server_defs_scenarios(path: Path = SCENARIOS_PATH) -> list[ServerDefsScenario]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError(f"{path}: expected scenarios array")
    loaded: list[ServerDefsScenario] = []
    for item in scenarios:
        if not isinstance(item, dict):
            continue
        scenario_id = str(item.get("id") or "").strip()
        if not scenario_id:
            continue
        loaded.append(
            ServerDefsScenario(
                id=scenario_id,
                description=str(item.get("description") or ""),
                raw=dict(item),
            ),
        )
    return loaded


def load_server_defs_scenario(scenario_id: str) -> ServerDefsScenario:
    for scenario in load_server_defs_scenarios():
        if scenario.id == scenario_id:
            return scenario
    raise KeyError(f"no server defs scenario for id {scenario_id!r}")


def load_yaml_cursor_defs_fixture(path: Path = YAML_CURSOR_DEFS_PATH) -> str:
    return path.read_text(encoding="utf-8")


def load_valid_cursor_defs_fixture(path: Path = VALID_CURSOR_DEFS_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def assert_mcp_server_defs_is_json(path: Path) -> dict[str, Any]:
    """Assert *path* is strict JSON with an ``mcpServers`` object (not YAML)."""
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith(("mcpServers:", "---")):
        raise AssertionError(f"{path}: server defs must be JSON, not YAML")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise AssertionError(f"{path}: expected JSON object")
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        raise AssertionError(f"{path}: expected mcpServers object")
    return payload


def workspace_server_defs_path(workspace: Path, agent: str = "cursor") -> Path:
    return workspace / ".agents" / "cyt" / "config" / "mcp" / f"{agent}.json"


def workspace_aggregator_path(workspace: Path) -> Path:
    return workspace / ".agents" / "cyt" / "config" / "mcp-config.yaml"


def materialize_workspace_with_mcp_config(
    tmp_path: Path,
    *,
    cursor_defs: str | dict[str, Any] | None = "valid",
) -> Path:
    """Create a git workspace with mcp-config.yaml and cursor.json backend defs."""
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
    workspace_aggregator_path(workspace).write_text(WORKSPACE_AGGREGATOR_YAML, encoding="utf-8")

    defs_path = workspace_server_defs_path(workspace)
    if cursor_defs == "valid":
        defs_path.write_text(
            json.dumps(load_valid_cursor_defs_fixture(), indent=2) + "\n",
            encoding="utf-8",
        )
    elif cursor_defs == "yaml":
        defs_path.write_text(load_yaml_cursor_defs_fixture(), encoding="utf-8")
    elif isinstance(cursor_defs, dict):
        defs_path.write_text(json.dumps(cursor_defs, indent=2) + "\n", encoding="utf-8")
    elif isinstance(cursor_defs, str):
        defs_path.write_text(cursor_defs, encoding="utf-8")
    else:
        defs_path.write_text('{"mcpServers": {}}', encoding="utf-8")
    return workspace.resolve()


def repo_root_from_tests() -> Path:
    return Path(__file__).resolve().parents[2]
