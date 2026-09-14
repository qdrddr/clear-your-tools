"""Tests for cyt inject preview CLI helpers and workspace-scoped catalog loading."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from cyt.config import load_config
from cyt.cyt_mcp.catalog import cyt_mcp_catalog_slug
from cyt.cyt_mcp.catalog_disk import read_disk_catalog
from cyt.hook.workspace_config import hook_workspace_from_config
from cyt.tools.inject_cli import config_for_inject_preview, run_inject_preview
from cyt.tools.master_catalog import get_master_tool_catalog
from tests.support.inject_preview_fixtures import (
    InjectPreviewFixturePack,
    disk_catalog_inject_preview_pack,
    inject_preview_pack,
    json_payload_from_stdout,
    patch_inject_preview_environment,
    scoped_hook_config,
    seed_workspace_disk_catalog,
)


def test_config_for_inject_preview_sets_hook_workspace(tmp_path: Path) -> None:
    workspace = (tmp_path / "project").resolve()
    workspace.mkdir()

    config = config_for_inject_preview(workspace)

    assert hook_workspace_from_config(config) == workspace


def test_workspace_only_disk_catalog_invisible_without_hook_workspace(
    inject_preview_pack: InjectPreviewFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: unscoped config resolves global slug and misses workspace disk cache."""
    patch_inject_preview_environment(monkeypatch, inject_preview_pack)
    seed_workspace_disk_catalog(inject_preview_pack)

    unscoped = load_config(inject_preview_pack.global_config_path)
    scoped = scoped_hook_config(inject_preview_pack)

    unscoped_slug = cyt_mcp_catalog_slug(unscoped)
    scoped_slug = cyt_mcp_catalog_slug(scoped)
    assert unscoped_slug != scoped_slug
    assert read_disk_catalog(unscoped_slug) is None
    assert read_disk_catalog(scoped_slug) is not None

    assert get_master_tool_catalog(unscoped, blocking=True) == []
    catalog = get_master_tool_catalog(scoped, blocking=True)
    assert catalog is not None
    assert len(catalog) == inject_preview_pack.scenario.expected_catalog_tool_count


def test_config_for_inject_preview_resolves_workspace_scoped_disk_catalog(
    disk_catalog_inject_preview_pack: InjectPreviewFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = disk_catalog_inject_preview_pack
    monkeypatch.chdir(pack.workspace)

    config = config_for_inject_preview(pack.workspace)
    catalog = get_master_tool_catalog(config, blocking=True)

    assert hook_workspace_from_config(config) == pack.workspace
    assert catalog is not None
    assert len(catalog) == pack.scenario.expected_catalog_tool_count
    assert {tool["name"] for tool in catalog} == set(pack.scenario.expected_pruned_tool_names)


def test_run_inject_preview_loads_workspace_scoped_catalog(
    disk_catalog_inject_preview_pack: InjectPreviewFixturePack,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pack = disk_catalog_inject_preview_pack
    monkeypatch.chdir(pack.workspace)

    args = argparse.Namespace(
        query=pack.scenario.query,
        workspace=pack.workspace,
        json=True,
        definitions=False,
        source=["cyt_mcp"],
    )
    code = run_inject_preview(args)
    captured = capsys.readouterr()

    assert code == 0, captured.err
    payload = json_payload_from_stdout(captured.out)
    assert payload["prune"]["cyt_mcp"]["tools_in"] == pack.scenario.expected_catalog_tool_count
    pruned_names = {tool["name"] for tool in payload["tools"]["cyt_mcp"]}
    assert pruned_names.issubset(set(pack.scenario.expected_pruned_tool_names))
    assert pruned_names
    for marker in pack.scenario.expected_injection_markers:
        assert marker in payload["injection"]
    for name in pruned_names:
        assert f"name='{name}'" in payload["injection"]


def test_run_inject_preview_fails_without_workspace_scoping_when_only_workspace_cache_exists(
    inject_preview_pack: InjectPreviewFixturePack,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Document failure mode if preview stops calling config_for_inject_preview."""
    patch_inject_preview_environment(monkeypatch, inject_preview_pack)
    seed_workspace_disk_catalog(inject_preview_pack)
    monkeypatch.chdir(inject_preview_pack.workspace)

    monkeypatch.setattr(
        "cyt.tools.inject_cli.config_for_inject_preview",
        lambda workspace: load_config(inject_preview_pack.global_config_path),
    )

    args = argparse.Namespace(
        query=inject_preview_pack.scenario.query,
        workspace=inject_preview_pack.workspace,
        json=False,
        definitions=False,
        source=["cyt_mcp"],
    )
    code = run_inject_preview(args)
    err = capsys.readouterr().err

    assert code == 1
    assert "No tools in master hook catalog" in err
    assert "disk_cache=miss" in err
