#!/usr/bin/env python3
"""Tests for mcp-config.yaml migration and stub catalog."""

from __future__ import annotations

from pathlib import Path

import yaml

from cyt.migrations.mcp_config import (
    maybe_repair_stale_mcp_config_file,
    migrate_mcp_config_file,
    repair_stale_mcp_config_agent_paths,
    upgrade_mcp_config_dict,
)
from cyt_mcp.stub_catalog import resolve_stub_name, resolve_stub_retain


def test_upgrade_replaces_codex_boolean_with_stub_by_agent() -> None:
    out = upgrade_mcp_config_dict({"codex_stubs_include_description": False})
    assert "codex_stubs_include_description" not in out
    by_agent = out["pruning"]["tools"]["stub_by_agent"]
    assert by_agent["codex"] == "basic"


def test_migrate_renames_legacy_filename(tmp_path: Path) -> None:
    legacy = tmp_path / "mcp-aggregator.yaml"
    legacy.write_text(
        "default_agent: cursor\ncodex_stubs_include_description: true\n",
        encoding="utf-8",
    )
    migrated = migrate_mcp_config_file(legacy)
    assert migrated is not None
    canonical = tmp_path / "mcp-config.yaml"
    assert canonical.is_file()
    assert not legacy.is_file()
    raw = yaml.safe_load(canonical.read_text(encoding="utf-8"))
    assert raw["pruning"]["tools"]["stub_by_agent"]["codex"] == "codex"


def test_resolve_stub_for_codex_agent() -> None:
    raw = {
        "pruning": {
            "tools": {
                "stub": "basic",
                "stub_by_agent": {"codex": "codex", "cursor": "basic"},
            },
        },
    }
    assert resolve_stub_name(raw, "codex") == "codex"
    retain = resolve_stub_retain(raw, "codex")
    assert "description" in retain.get("tool", [])
    assert retain.get("required_properties") == ["name"]


def test_upgrade_migrates_legacy_empty_required_properties_on_basic_codex() -> None:
    raw = {
        "pruning": {
            "tools": {
                "stubs": [
                    {
                        "name": "basic",
                        "always": {
                            "tool": ["name"],
                            "required_properties": [],
                            "optional_properties": [],
                        },
                    },
                    {
                        "name": "codex",
                        "always": {
                            "tool": ["name", "description"],
                            "required_properties": [],
                            "optional_properties": [],
                        },
                    },
                ],
            },
        },
    }
    out = upgrade_mcp_config_dict(raw)
    stubs = out["pruning"]["tools"]["stubs"]
    by_name = {item["name"]: item["always"] for item in stubs}
    assert by_name["basic"]["required_properties"] == ["name"]
    assert by_name["codex"]["required_properties"] == ["name"]


def test_repair_stale_mcp_config_agent_paths_replaces_pytest_tmp(tmp_path: Path) -> None:
    stale = tmp_path / "backends" / "cursor.json"
    stale.parent.mkdir(parents=True)
    raw = {
        "agents": {
            "cursor": str(stale),
            "claude": str(stale.parent / "claude.json"),
            "codex": str(stale.parent / "codex.json"),
        },
    }
    repaired, changed = repair_stale_mcp_config_agent_paths(raw)
    assert changed is True
    assert repaired["agents"]["cursor"] == "~/.config/cyt/mcp/cursor.json"
    assert repaired["agents"]["claude"] == "~/.config/cyt/mcp/claude.json"


def test_maybe_repair_stale_mcp_config_file_writes_canonical_paths(tmp_path: Path) -> None:
    config_path = tmp_path / "mcp-config.yaml"
    stale = tmp_path / "pytest-of-user" / "pytest-1" / "backends" / "cursor.json"
    stale.parent.mkdir(parents=True)
    config_path.write_text(
        "\n".join(
            [
                "default_agent: cursor",
                "agents:",
                f"  cursor: {stale}",
                f"  claude: {stale.parent / 'claude.json'}",
                f"  codex: {stale.parent / 'codex.json'}",
            ],
        ),
        encoding="utf-8",
    )
    maybe_repair_stale_mcp_config_file(config_path)
    text = config_path.read_text(encoding="utf-8")
    assert "~/.config/cyt/mcp/cursor.json" in text
    assert "pytest-of-user" not in text
