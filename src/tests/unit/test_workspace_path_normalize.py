"""Tests for Cursor workspace path normalization on Windows."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cyt_client.rules_file import (
    is_valid_workspace_root,
    normalize_workspace_path_string,
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
