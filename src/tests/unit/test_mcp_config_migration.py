#!/usr/bin/env python3
"""Tests for mcp-config.yaml migration and stub catalog."""

from __future__ import annotations

from pathlib import Path

import yaml

from cyt.migrations.mcp_config import migrate_mcp_config_file, upgrade_mcp_config_dict
from cyt_mcp.stub_catalog import resolve_stub_name, resolve_stub_retain


def test_upgrade_replaces_codex_boolean_with_stub_by_agent() -> None:
    out = upgrade_mcp_config_dict({"codex_stubs_include_description": False})
    assert "codex_stubs_include_description" not in out
    by_agent = out["pruning"]["tools"]["stub_by_agent"]
    assert by_agent["codex"] == "basic"


def test_migrate_renames_legacy_filename(tmp_path: Path) -> None:
    legacy = tmp_path / "mcp-aggregator.yaml"
    legacy.write_text("default_agent: cursor\ncodex_stubs_include_description: true\n", encoding="utf-8")
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
