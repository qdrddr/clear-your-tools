"""Tests for tier tool capture source configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt.tiers.config import (
    tier_disk_flush_seconds,
    tier_tool_capture_source,
    tier_tool_capture_via_hooks,
)


def test_tier_tool_capture_source_defaults_to_cyt_mcp() -> None:
    assert tier_tool_capture_source({}) == "cyt_mcp"
    assert tier_tool_capture_via_hooks({}) is False


def test_tier_disk_flush_seconds_default_when_unconfigured() -> None:
    assert tier_disk_flush_seconds({}) == 900.0


def test_bundled_defaults_include_tier_disk_flush_seconds() -> None:
    from cyt.config import load_bundled_defaults_yaml

    database = load_bundled_defaults_yaml()["tools"]["tiers"]["database"]
    assert database["disk_flush_seconds"] == 900


def test_cyt_config_exports_capture_helpers() -> None:
    from cyt.config import tier_tool_capture_source, tier_tool_capture_via_hooks

    assert tier_tool_capture_source({}) == "cyt_mcp"
    assert (
        tier_tool_capture_via_hooks({"tools": {"tiers": {"capture": {"source": "hooks"}}}}) is True
    )


def test_tier_tool_capture_source_hooks() -> None:
    cfg = {"tools": {"tiers": {"capture": {"source": "hooks"}}}}
    assert tier_tool_capture_source(cfg) == "hooks"
    assert tier_tool_capture_via_hooks(cfg) is True


def test_tier_client_config_reads_hooks_from_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt_client import config as client_config

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "tools:\n  tiers:\n    capture:\n      source: hooks\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert client_config.tier_tool_capture_via_hooks() is True
