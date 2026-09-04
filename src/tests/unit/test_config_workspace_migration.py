#!/usr/bin/env python3
"""Tests for workspace config path promotion and fixture migrations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from cyt.hook.install_scope import CytInstallScope
from cyt.migrations import current_head, upgrade_config_dict
from cyt.migrations.base import read_schema_version
from cyt.migrations.migrate import maybe_migrate_workspace_config, migrate_config_file
from cyt.migrations.workspace_paths import ensure_canonical_workspace_config


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_ensure_canonical_workspace_config_promotes_legacy_cursor_path(
    workspace: Path,
) -> None:
    legacy = workspace / ".cursor" / "cyt" / "config" / "config.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        "agents:\n  cursor:\n    skills:\n      permissions:\n        deny: []\n",
        encoding="utf-8",
    )
    canonical = workspace / ".agents" / "cyt" / "config" / "config.yaml"

    scope = CytInstallScope.from_cwd()
    promoted = ensure_canonical_workspace_config(scope)

    assert promoted == canonical
    assert canonical.is_file()
    assert not legacy.is_file()


def test_ensure_canonical_workspace_aggregator_promotes_legacy_cursor_path(
    workspace: Path,
) -> None:
    from cyt.migrations.workspace_paths import ensure_canonical_workspace_aggregator

    legacy = workspace / ".cursor" / "cyt" / "config" / "mcp-aggregator.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("default_agent: cursor\n", encoding="utf-8")
    canonical = workspace / ".agents" / "cyt" / "config" / "mcp-aggregator.yaml"

    scope = CytInstallScope.from_cwd()
    promoted = ensure_canonical_workspace_aggregator(scope)

    assert promoted == canonical
    assert canonical.is_file()
    assert not legacy.is_file()


def test_ensure_canonical_workspace_server_defs_promotes_legacy_cursor_path(
    workspace: Path,
) -> None:
    from cyt.migrations.workspace_paths import ensure_canonical_workspace_server_defs

    legacy = workspace / ".cursor" / "cyt" / "mcp" / "cursor.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"mcpServers": {"backend": {}}}', encoding="utf-8")
    canonical = workspace / ".agents" / "cyt" / "config" / "mcp" / "cursor.json"

    scope = CytInstallScope.from_cwd()
    promoted = ensure_canonical_workspace_server_defs(scope, "cursor")

    assert promoted == canonical
    assert canonical.is_file()
    assert not legacy.is_file()
    payload = json.loads(canonical.read_text(encoding="utf-8"))
    assert "backend" in payload["mcpServers"]


def test_fixture_legacy_pruning_migrates_to_head() -> None:
    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "config_migrations"
        / "legacy_pruning.yaml"
    )
    raw = yaml.safe_load(fixture.read_text(encoding="utf-8"))
    result = upgrade_config_dict(raw, scope="global")
    assert result.cfg["pruning"]["tools"]["sequence"] == ["bm25", "rerank"]
    assert result.cfg["pruning"]["tools"]["pipelines"]["llm"]["model_nick"] == "mercury-2"
    assert read_schema_version(result.cfg) == current_head()


def test_maybe_migrate_workspace_config_promotes_and_stamps(
    workspace: Path,
) -> None:
    legacy = workspace / ".cursor" / "cyt" / "config" / "config.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        "skills:\n  permissions:\n    deny:\n      - path:.codex/skills/.system\n",
        encoding="utf-8",
    )

    path = maybe_migrate_workspace_config()
    assert path is not None
    assert path.is_file()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert read_schema_version(raw) == current_head()
    deny = raw["skills"]["permissions"]["deny"]
    assert "path:.codex/skills/.system" in deny


def test_migrate_workspace_legacy_file_in_place_when_canonical_exists(
    workspace: Path,
) -> None:
    legacy = workspace / ".cursor" / "cyt" / "config" / "config.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("pruning:\n  pipeline:\n    - bm25\n", encoding="utf-8")

    result = migrate_config_file(legacy, scope="workspace", backup=False)
    assert result is not None
    assert result.cfg["pruning"]["tools"]["sequence"] == ["bm25"]
