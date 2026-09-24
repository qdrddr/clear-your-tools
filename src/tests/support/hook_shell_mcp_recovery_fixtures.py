"""Shared fixtures for fish-safe hook wrappers and corrupt MCP config recovery tests."""

from __future__ import annotations

from pathlib import Path

import yaml

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "hook_shell_mcp_recovery"
CORRUPT_MCP_CONFIG_FIXTURE = FIXTURES_DIR / "corrupt_mcp-config.yaml.fixture"
VALID_WORKSPACE_MCP_CONFIG_FIXTURE = FIXTURES_DIR / "valid_workspace_mcp-config.yaml"
CORRUPT_STUB_FRAGMENT = (
    "(no property descriptions; OpenAI Responses API expects description on wire)."
)


def corrupt_mcp_config_text() -> str:
    return CORRUPT_MCP_CONFIG_FIXTURE.read_text(encoding="utf-8")


def valid_workspace_mcp_config_text() -> str:
    return VALID_WORKSPACE_MCP_CONFIG_FIXTURE.read_text(encoding="utf-8")


def write_corrupt_workspace_mcp_config(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(corrupt_mcp_config_text(), encoding="utf-8")
    return target


def write_valid_workspace_mcp_config(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(valid_workspace_mcp_config_text(), encoding="utf-8")
    return target


def assert_valid_mcp_config_yaml(path: Path) -> dict:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    assert loaded.get("catalog_scope") == "workspace"
    assert loaded["agents"]["cursor"] == "mcp/cursor.json"
    return loaded
