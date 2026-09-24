"""Tests for Cursor workspace path normalization on Windows."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cyt_client.rules_file import (
    is_valid_workspace_root,
    normalize_workspace_path_string,
    workspace_path_string,
    workspace_root_from_payload,
)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive-letter paths")
def test_normalize_cursor_git_bash_workspace_path() -> None:
    raw = "/c:/Users/DamienBerezenko/git/clear-your-tools"
    normalized = normalize_workspace_path_string(raw)
    path = Path(normalized)
    assert path.is_dir()
    assert is_valid_workspace_root(path)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive-letter paths")
def test_workspace_root_from_payload_accepts_git_bash_roots() -> None:
    payload = {
        "workspace_roots": ["/c:/Users/DamienBerezenko/git/clear-your-tools"],
        "conversation_id": "test-session",
    }
    workspace = workspace_root_from_payload(payload)
    assert workspace is not None
    assert workspace.is_dir()


def test_workspace_path_string_skips_unexpanded_cyt_workspace_template(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CYT_WORKSPACE", "${workspaceFolder}")
    monkeypatch.setenv("CURSOR_PROJECT_DIR", str(tmp_path))
    assert workspace_path_string({"hook_event_name": "sessionStart"}) == str(tmp_path)


def test_workspace_path_string_falls_back_to_cyt_workspace_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CYT_WORKSPACE", str(tmp_path))
    assert workspace_path_string({"hook_event_name": "sessionStart"}) == str(tmp_path)
    assert workspace_root_from_payload({"hook_event_name": "sessionStart"}) == tmp_path


def test_workspace_root_from_payload_rejects_user_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("cyt.hook.install_scope.Path.home", lambda: home)
    payload = {"cwd": str(home), "conversation_id": "test-session"}
    assert workspace_root_from_payload(payload) is None
    assert is_valid_workspace_root(home) is False
