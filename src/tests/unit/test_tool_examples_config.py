"""Unit tests for tool examples configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt.tool_examples.config import examples_active, tool_examples_config, tool_examples_db_path


def test_tool_examples_db_path_from_config(tmp_path: Path) -> None:
    db = tmp_path / "custom.db"
    cfg = {"tools": {"examples": {"database": {"path": str(db)}}}}
    assert tool_examples_db_path(cfg) == str(db)


def test_tool_examples_db_path_default() -> None:
    path = tool_examples_db_path({})
    assert path.endswith("tool_examples.db")
    assert ".config/cyt" in path


def test_tool_examples_config_defaults() -> None:
    cfg = tool_examples_config({})
    assert cfg.enabled is False
    assert cfg.post_tool_use is True
    assert cfg.post_tool_matcher == "MCP:*|mcp__*"
    assert cfg.require_success is True
    assert cfg.max_per_property == 3
    assert cfg.max_value_chars == 120
    assert cfg.full_call_examples is True
    assert cfg.max_full_call_examples == 2
    assert cfg.cross_schema_fallback is False
    assert cfg.max_per_path == 20
    assert cfg.min_per_path == 3
    assert cfg.max_captures_per_tool == 50
    assert cfg.min_captures_per_tool == 5
    assert cfg.max_age_days == 90
    assert cfg.min_per_schema_hash == 5
    assert cfg.min_historical_per_path == 20
    assert cfg.vacuum_after_maintenance is True
    assert len(cfg.redact_key_patterns) >= 1
    assert cfg.ranking.pipeline == "inherit"
    assert cfg.ranking.rrf_k == 60
    assert cfg.ranking.diversity_threshold == 0.6


def test_tool_examples_config_overrides(tmp_path: Path) -> None:
    db = tmp_path / "examples.db"
    cfg = tool_examples_config(
        {
            "tools": {
                "examples": {
                    "enabled": True,
                    "database": {"path": str(db)},
                    "capture": {
                        "post_tool_use": False,
                        "post_tool_matcher": "custom:*",
                        "require_success": False,
                    },
                    "inject": {
                        "max_per_property": 5,
                        "max_value_chars": 80,
                        "full_call_examples": False,
                        "max_full_call_examples": 1,
                        "cross_schema_fallback": True,
                    },
                    "retention": {
                        "max_per_path": 10,
                        "min_per_path": 2,
                        "max_captures_per_tool": 20,
                        "min_captures_per_tool": 4,
                        "max_age_days": 30,
                    },
                    "redact_key_patterns": [r"(?i)credential"],
                },
            },
        },
    )
    assert cfg.enabled is True
    assert cfg.db_path == str(db)
    assert cfg.post_tool_use is False
    assert cfg.post_tool_matcher == "custom:*"
    assert cfg.require_success is False
    assert cfg.max_per_property == 5
    assert cfg.max_value_chars == 80
    assert cfg.full_call_examples is False
    assert cfg.max_full_call_examples == 1
    assert cfg.cross_schema_fallback is True
    assert cfg.max_per_path == 10
    assert cfg.min_per_path == 2
    assert cfg.max_captures_per_tool == 20
    assert cfg.min_captures_per_tool == 4
    assert cfg.max_age_days == 30


def test_examples_active() -> None:
    assert examples_active({"tools": {"examples": {"enabled": True}}}) is True
    assert examples_active({"tools": {"examples": {"enabled": False}}}) is False
    assert examples_active({}) is False


def test_cyt_client_post_tool_capture_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt_client.config import tool_examples_post_tool_capture_enabled

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr("cyt_client.config.resolve_config_path", lambda: config_path)

    config_path.write_text("tools:\n  examples:\n    enabled: false\n", encoding="utf-8")
    assert tool_examples_post_tool_capture_enabled() is False

    config_path.write_text(
        "tools:\n  examples:\n    enabled: true\n    capture:\n      post_tool_use: false\n",
        encoding="utf-8",
    )
    assert tool_examples_post_tool_capture_enabled() is False

    config_path.write_text(
        "tools:\n  examples:\n    enabled: true\n    capture:\n      post_tool_use: true\n",
        encoding="utf-8",
    )
    assert tool_examples_post_tool_capture_enabled() is True
