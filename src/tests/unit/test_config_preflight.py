#!/usr/bin/env python3
"""Tests for config preflight helpers used by setup/hook/daemon commands."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cyt.config import _load_yaml_dict
from cyt.migrations import current_head, upgrade_config_dict
from cyt.migrations.base import read_schema_version
from cyt.migrations.migrate import (
    ensure_config_file_current,
    ensure_workspace_config_current,
)


@pytest.fixture
def user_config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_ensure_config_file_current_migrates_legacy_and_prints(
    user_config_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CYT_SKIP_CONFIG_MIGRATE", raising=False)
    user_config_path.write_text(
        "pruning:\n  pipeline:\n    - bm25\n",
        encoding="utf-8",
    )

    result = ensure_config_file_current(user_config_path, scope="global")

    assert result is not None
    assert result.changed is True
    written = _load_yaml_dict(user_config_path)
    assert written["pruning"]["tools"]["sequence"] == ["bm25"]
    assert read_schema_version(written) == current_head()
    err = capsys.readouterr().err
    assert "config: migrated" in err
    assert str(user_config_path) in err


def test_ensure_config_file_current_at_head_is_silent(
    user_config_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CYT_SKIP_CONFIG_MIGRATE", raising=False)
    cfg = upgrade_config_dict({}, scope="global").cfg
    user_config_path.write_text(yaml.dump(cfg, default_flow_style=False), encoding="utf-8")

    result = ensure_config_file_current(user_config_path, scope="global")

    assert result is not None
    assert result.changed is False
    assert capsys.readouterr().err == ""


def test_ensure_config_file_current_skip_env_warns_without_migrating(
    user_config_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CYT_SKIP_CONFIG_MIGRATE", "1")
    user_config_path.write_text(
        "pruning:\n  pipeline:\n    - bm25\n",
        encoding="utf-8",
    )

    result = ensure_config_file_current(user_config_path, scope="global")

    assert result is None
    written = _load_yaml_dict(user_config_path)
    assert "pipeline" in written.get("pruning", {})
    err = capsys.readouterr().err
    assert "pending migrations" in err
    assert "CYT_SKIP_CONFIG_MIGRATE=1" in err


def test_ensure_config_file_current_missing_file_is_silent(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing.yaml"

    result = ensure_config_file_current(missing, scope="global")

    assert result is None
    assert capsys.readouterr().err == ""


def test_ensure_workspace_config_current_promotes_and_migrates(
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CYT_SKIP_CONFIG_MIGRATE", raising=False)
    legacy = workspace / ".cursor" / "cyt" / "config" / "config.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        "pruning:\n  pipeline:\n    - bm25\n",
        encoding="utf-8",
    )
    canonical = workspace / ".agents" / "cyt" / "config" / "config.yaml"

    path = ensure_workspace_config_current()

    assert path == canonical
    assert canonical.is_file()
    assert not legacy.is_file()
    raw = yaml.safe_load(canonical.read_text(encoding="utf-8"))
    assert raw["pruning"]["tools"]["sequence"] == ["bm25"]
    assert read_schema_version(raw) == current_head()
    err = capsys.readouterr().err
    assert "config: migrated" in err


def test_ensure_workspace_config_current_no_workspace_returns_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert ensure_workspace_config_current() is None
