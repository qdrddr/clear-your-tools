"""Tests for per-project tier root resolution."""

from __future__ import annotations

import subprocess
from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from cyt.tiers.config import resolve_git_toplevel, resolve_tier_project


def test_resolve_git_toplevel_falls_back_to_workspace_without_git(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    resolved = resolve_git_toplevel(workspace)
    assert resolved == workspace.resolve()


def test_resolve_git_toplevel_uses_git_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    nested = repo / "packages" / "app"
    nested.mkdir(parents=True)
    resolved = resolve_git_toplevel(nested)
    assert resolved == repo.resolve()


def test_resolve_tier_project_none_without_workspace(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert resolve_tier_project() is None


def test_resolve_tier_project_from_git_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    project = resolve_tier_project(workspace=repo)
    assert project == repo.resolve()
