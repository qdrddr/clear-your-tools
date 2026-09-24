"""Integration tests for corrupt workspace mcp-config.yaml recovery during hook setup."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from cyt.hook.install_scope import CytInstallScope
from cyt.migrations.workspace_paths import ensure_canonical_workspace_mcp_config
from cyt.tools import cyt_mcp_setup
from cyt_mcp.config import load_aggregator_config
from tests.support.hook_shell_mcp_recovery_fixtures import (
    assert_valid_mcp_config_yaml,
    write_corrupt_workspace_mcp_config,
)

pytestmark = pytest.mark.integration


def test_workspace_mcp_setup_recovers_from_corrupt_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    (consumer / ".git").mkdir()
    scope = CytInstallScope(workspace_root=consumer.resolve())
    canonical = scope.workspace_all_agents_cyt_mcp_config_path()
    defs_path = scope.workspace_all_agents_cyt_mcp_defs_path("cursor")
    assert canonical is not None and defs_path is not None
    defs_path.parent.mkdir(parents=True, exist_ok=True)
    defs_path.write_text(json.dumps({"mcpServers": {}}) + "\n", encoding="utf-8")
    write_corrupt_workspace_mcp_config(canonical)

    ensure_canonical_workspace_mcp_config(scope)
    assert not canonical.is_file()

    cyt_mcp_setup.write_mcp_aggregator_yaml_at(
        canonical,
        "cursor",
        backends_path=defs_path,
        workspace_scoped=True,
        http_port=cyt_mcp_setup.DEFAULT_WORKSPACE_HTTP_PORT,
    )

    assert_valid_mcp_config_yaml(canonical)
    config = load_aggregator_config(
        agent="cursor",
        aggregator_path=canonical,
        workspace_folder=consumer.resolve(),
    )
    assert config.catalog_scope == "workspace"
    assert config.agent == "cursor"


def test_corrupt_fixture_yaml_is_unreadable_before_recovery(tmp_path: Path) -> None:
    path = write_corrupt_workspace_mcp_config(tmp_path / "mcp-config.yaml")
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(path.read_text(encoding="utf-8"))
