"""Unit tests for corrupt workspace mcp-config.yaml recovery."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cyt.hook.install_scope import CytInstallScope
from cyt.migrations.mcp_config import migrate_mcp_config_file
from cyt.migrations.workspace_paths import ensure_canonical_workspace_mcp_config
from cyt_mcp.config import load_mcp_config_yaml
from tests.support.hook_shell_mcp_recovery_fixtures import (
    CORRUPT_STUB_FRAGMENT,
    assert_valid_mcp_config_yaml,
    corrupt_mcp_config_text,
    write_corrupt_workspace_mcp_config,
    write_valid_workspace_mcp_config,
)


def test_corrupt_mcp_config_fixture_matches_reported_scanner_error_fragment() -> None:
    assert CORRUPT_STUB_FRAGMENT in corrupt_mcp_config_text()


def test_corrupt_mcp_config_fixture_is_invalid_yaml(tmp_path: Path) -> None:
    path = write_corrupt_workspace_mcp_config(tmp_path / "mcp-config.yaml")
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(path.read_text(encoding="utf-8"))


def test_valid_workspace_mcp_config_fixture_is_valid_yaml(tmp_path: Path) -> None:
    path = write_valid_workspace_mcp_config(tmp_path / "mcp-config.yaml")
    assert_valid_mcp_config_yaml(path)


def test_migrate_mcp_config_file_backs_up_and_removes_corrupt_yaml(tmp_path: Path) -> None:
    config_path = write_corrupt_workspace_mcp_config(tmp_path / "mcp-config.yaml")

    migrated = migrate_mcp_config_file(config_path)

    assert migrated is None
    assert not config_path.is_file()
    backups = list(tmp_path.glob("mcp-config.yaml.corrupt.*"))
    assert len(backups) == 1
    assert CORRUPT_STUB_FRAGMENT in backups[0].read_text(encoding="utf-8")


def test_load_mcp_config_yaml_returns_empty_after_corrupt_file_removed(
    tmp_path: Path,
) -> None:
    config_path = write_corrupt_workspace_mcp_config(tmp_path / "mcp-config.yaml")

    loaded = load_mcp_config_yaml(config_path)

    assert loaded == {}
    assert not config_path.is_file()


def test_ensure_canonical_workspace_mcp_config_survives_corrupt_yaml(
    tmp_path: Path,
) -> None:
    scope = CytInstallScope(workspace_root=tmp_path)
    canonical = scope.workspace_all_agents_cyt_mcp_config_path()
    assert canonical is not None
    write_corrupt_workspace_mcp_config(canonical)

    result = ensure_canonical_workspace_mcp_config(scope)

    assert result == canonical
    assert not canonical.is_file()
    assert list(tmp_path.rglob("mcp-config.yaml.corrupt.*"))
