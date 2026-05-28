#!/usr/bin/env python3
"""Tests for config path resolution and loading."""

from __future__ import annotations

from pathlib import Path

import pytest

import cyt.config as configs


@pytest.fixture
def isolated_config_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Path]:
    user_config = tmp_path / "home" / ".configs" / "cyt" / "config.yaml"
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(configs, "DEFAULT_USER_CONFIG_PATH", user_config)
    monkeypatch.chdir(work_dir)
    return {
        "root": tmp_path,
        "work": work_dir,
        "user_config": user_config,
        "cwd_config": tmp_path / "work" / "config.yaml",
    }


def test_resolve_config_path_explicit(isolated_config_paths: dict[str, Path]) -> None:
    explicit = isolated_config_paths["root"] / "custom.yaml"
    assert configs.resolve_config_path(explicit) == explicit


def test_resolve_config_path_prefers_cwd(isolated_config_paths: dict[str, Path]) -> None:
    cwd_config = isolated_config_paths["cwd_config"]
    cwd_config.write_text("defaults:\n  is_persistent: false\n", encoding="utf-8")
    assert configs.resolve_config_path(None) == cwd_config


def test_resolve_config_path_falls_back_to_user_config(
    isolated_config_paths: dict[str, Path],
) -> None:
    assert configs.resolve_config_path(None) == isolated_config_paths["user_config"]


def test_resolve_setup_config_path_defaults_to_user_config(
    isolated_config_paths: dict[str, Path],
) -> None:
    assert configs.resolve_setup_config_path(None) == isolated_config_paths["user_config"]


def test_resolve_setup_config_path_explicit(
    isolated_config_paths: dict[str, Path],
) -> None:
    explicit = isolated_config_paths["root"] / "setup.yaml"
    assert configs.resolve_setup_config_path(explicit) == explicit


def test_load_config_creates_user_config_when_missing(
    isolated_config_paths: dict[str, Path],
) -> None:
    user_config = isolated_config_paths["user_config"]
    assert not user_config.exists()

    loaded = configs.load_config()

    assert user_config.exists()
    assert loaded["network"]["proxy"]["reverse"]["port"] == 8834
    assert loaded["stats"]["database"]["path"] == configs.DEFAULT_STATS_DB_PATH


def test_load_config_uses_cwd_config(isolated_config_paths: dict[str, Path]) -> None:
    cwd_config = isolated_config_paths["cwd_config"]
    cwd_config.write_text(
        "network:\n  proxy:\n    reverse:\n      port: 9999\n",
        encoding="utf-8",
    )

    loaded = configs.load_config()

    assert loaded["network"]["proxy"]["reverse"]["port"] == 9999
    assert not isolated_config_paths["user_config"].exists()


def test_load_config_explicit_missing_returns_defaults(
    isolated_config_paths: dict[str, Path],
) -> None:
    missing = isolated_config_paths["root"] / "missing.yaml"
    loaded = configs.load_config(missing)

    assert loaded["network"]["proxy"]["reverse"]["port"] == 8834
    assert loaded["defaults"]["remote"]["reranking_model_nick"] == "rerank-qwen3-8b"
    assert not missing.exists()
    assert not isolated_config_paths["user_config"].exists()


def test_load_config_layers_bundled_defaults_under_user_overrides(
    isolated_config_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundled = isolated_config_paths["root"] / "bundled.yaml"
    bundled.write_text(
        "defaults:\n  remote:\n    reranking_model_nick: bundled-rerank\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        configs,
        "_load_bundled_defaults_yaml",
        lambda: configs._load_yaml_dict(bundled),
    )

    user_config = isolated_config_paths["user_config"]
    user_config.parent.mkdir(parents=True, exist_ok=True)
    user_config.write_text("defaults:\n  is_persistent: false\n", encoding="utf-8")

    loaded = configs.load_config()

    assert loaded["defaults"]["is_persistent"] is False
    assert loaded["defaults"]["remote"]["reranking_model_nick"] == "bundled-rerank"
