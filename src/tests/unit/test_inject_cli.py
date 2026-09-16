"""Tests for cyt inject preview CLI helpers and workspace-scoped catalog loading."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from cyt.config import load_config
from cyt.cyt_mcp.catalog import cyt_mcp_catalog_slug
from cyt.cyt_mcp.catalog_disk import read_disk_catalog
from cyt.hook.workspace_config import hook_workspace_from_config
from cyt.tools.inject_cli import (
    _preview_token_stats,
    _print_preview_token_summary,
    config_for_inject_preview,
    run_inject_preview,
)
from cyt.tools.master_catalog import get_master_tool_catalog
from tests.support.inject_preview_fixtures import (
    InjectPreviewFixturePack,
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


def test_preview_token_stats_uses_agent_tools_xml_and_adjusts_for_frontend_stubs(
    tmp_path: Path,
) -> None:
    workspace = (tmp_path / "project").resolve()
    workspace.mkdir()
    grouped = {
        "cyt_mcp": [
            {
                "name": "fff_grep",
                "description": "grep",
                "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        ],
    }
    pruned_injection = "\n<agent-tools path='x'><cyt-mcp>demo</cyt-mcp></agent-tools>"
    stats = _preview_token_stats(
        grouped=grouped,
        pruned_injection=pruned_injection,
        agent="cursor",
        workspace=workspace,
    )
    assert stats["tokens_in"] > stats["tokens_out"]
    assert stats["tokens_out"] > 0
    assert stats["frontend_tool_count"] == 2
    assert stats["frontend_tokens"] > 0
    assert stats["net_tokens_out"] == int(stats["tokens_out"]) + int(stats["frontend_tokens"])
    assert stats["net_tokens_saved"] == int(stats["tokens_in"]) - int(stats["net_tokens_out"])


def test_print_preview_token_summary_writes_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _print_preview_token_summary(
        {
            "tokens_in": 100,
            "tokens_out": 20,
            "tokens_saved": 80,
            "savings_percent": 80.0,
            "tool_count_in": 50,
            "frontend_tool_count": 2,
            "frontend_tokens": 10,
            "net_tokens_out": 30,
            "net_tokens_saved": 70,
            "net_savings_percent": 70.0,
        },
    )
    err = capsys.readouterr().err
    assert "Tools before cleaning (compact JSON):" in err
    assert "tools=" in err
    assert "Tools after cleaning agent-tools (compact JSON):" in err
    assert "Frontend stubs (compact JSON):" in err
    assert "Saved (tokens):" in err
    assert "= 100-20+10" in err


def test_run_inject_preview_defers_token_logging_to_stderr(
    disk_catalog_inject_preview_pack: InjectPreviewFixturePack,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pack = disk_catalog_inject_preview_pack
    monkeypatch.chdir(pack.workspace)

    logged: list[tuple[int, int | None]] = []

    def _capture_log(tokens_in: int, tokens_out: int | None) -> None:
        logged.append((tokens_in, tokens_out))

    monkeypatch.setattr(
        "cyt.pruners.tools_filter._log_tool_token_counts",
        _capture_log,
    )

    args = argparse.Namespace(
        query=pack.scenario.query,
        workspace=pack.workspace,
        json=False,
        definitions=False,
        source=["cyt_mcp"],
    )
    code = run_inject_preview(args)
    captured = capsys.readouterr()
    assert code == 0
    assert logged == []
    assert captured.out.startswith("\n<agent-tools")
    assert "Tools before cleaning (compact JSON):" in captured.err
    assert "tools=" in captured.err
    assert "Tools after cleaning agent-tools (compact JSON):" in captured.err
    assert "Frontend stubs (compact JSON):" in captured.err
    assert "Saved (tokens):" in captured.err
    assert captured.out.find("<agent-tools") < len(captured.out)


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
