#!/usr/bin/env python3
"""Tests for config migration runner and revision chain."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt.migrations import current_head, migration_history, pending_revisions, upgrade_config_dict
from cyt.migrations.base import read_schema_version
from cyt.migrations.migrate import migrate_config_file, skip_auto_migrate, write_config_dict
from cyt.migrations.runner import write_backup


def test_revision_chain_is_linear_and_reaches_head() -> None:
    history = migration_history()
    assert history
    assert history[-1][0] == current_head()
    downs = {item[1] for item in history}
    assert "000_baseline" in downs


def test_upgrade_baseline_to_head() -> None:
    result = upgrade_config_dict({}, scope="global")
    assert result.changed is True
    assert result.to_revision == current_head()
    assert read_schema_version(result.cfg) == current_head()


def test_upgrade_is_idempotent_at_head() -> None:
    first = upgrade_config_dict({}, scope="global")
    second = upgrade_config_dict(first.cfg, scope="global")
    assert second.changed is False
    assert second.steps == ()


def test_pending_revisions_from_baseline() -> None:
    pending = pending_revisions({}, scope="global")
    assert pending
    assert pending[-1] == current_head()


def test_migrate_legacy_pruning_pipeline(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    write_config_dict(
        path,
        {
            "pruning": {
                "pipeline": ["bm25", "rerank"],
                "policy": {"minimum_tools": 40},
            },
        },
    )
    result = migrate_config_file(path, scope="global", backup=False)
    assert result is not None
    assert result.changed is True
    assert result.cfg["pruning"]["tools"]["sequence"] == ["bm25", "rerank"]
    assert result.cfg["pruning"]["tools"]["policy"]["minimum_tools"] == 40
    assert "pipeline" not in result.cfg.get("pruning", {})
    assert read_schema_version(result.cfg) == current_head()


def test_migrate_writes_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.yaml"
    write_config_dict(path, {"defaults": {"is_persistent": True}})
    migrate_config_file(path, scope="global", backup=True)
    backups = list(tmp_path.glob("config.yaml.bak.*"))
    assert backups


def test_migrate_config_file_dry_run_does_not_write(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    write_config_dict(path, {"pruning": {"pipeline": ["bm25"]}})
    original = path.read_text(encoding="utf-8")
    migrate_config_file(path, scope="global", dry_run=True)
    assert path.read_text(encoding="utf-8") == original


def test_skip_auto_migrate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYT_SKIP_CONFIG_MIGRATE", "1")
    assert skip_auto_migrate() is True


def test_write_backup_rotates(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("a: 1\n", encoding="utf-8")
    for _ in range(5):
        write_backup(path)
    backups = list(tmp_path.glob("config.yaml.bak.*"))
    assert len(backups) <= 3
