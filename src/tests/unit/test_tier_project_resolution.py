"""Tests for per-project tier root resolution."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from cyt.tiers.config import (
    cyt_package_git_root,
    resolve_git_toplevel,
    resolve_tier_project,
)
from cyt.tiers.store import TierStore


@pytest.fixture(autouse=True)
def _isolate_workspace_resolution_env(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> Iterator[None]:
    for key in (
        "CYT_WORKSPACE",
        "CYT_SHELL_WORKSPACE",
        "WORKSPACE_FOLDER",
        "VSCODE_WORKSPACE_FOLDER",
        "CURSOR_WORKSPACE_FOLDER",
        "CYT_HOOK_CWD",
        "CYT_TIER_WORKSPACE",
        "CURSOR_WORKSPACE_LABEL",
    ):
        monkeypatch.delenv(key, raising=False)
    registry_path = tmp_path / "active-workspaces.json"
    registry_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("cyt.hook.active_workspace._ACTIVE_WORKSPACE_FILE", registry_path)
    yield


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


def test_resolve_tier_project_from_cwd_git_subdir(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    nested = repo / "packages" / "app"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert resolve_tier_project() == repo.resolve()


def test_resolve_tier_project_from_cwd_cursor_marker(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".cursor").mkdir()
    monkeypatch.chdir(workspace)
    assert resolve_tier_project() == workspace.resolve()


def test_resolve_tier_project_uses_cursor_label_when_cwd_is_cyt_repo(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cyt_repo = tmp_path / "clear-your-tools"
    tra_repo = tmp_path / "tra"
    for repo in (cyt_repo, tra_repo):
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)

    db_path = tmp_path / "tier_state.db"
    store = TierStore.open(str(db_path))
    try:
        store.get_or_create_project(str(cyt_repo))
        store.get_or_create_project(str(tra_repo))
    finally:
        store.close()

    monkeypatch.chdir(cyt_repo)
    monkeypatch.setenv("CURSOR_WORKSPACE_LABEL", "tra")
    monkeypatch.setattr("cyt.hook.workspace_resolution.cyt_package_git_root", lambda: cyt_repo.resolve())
    monkeypatch.setattr(
        "cyt.config.load_config",
        lambda *args, **kwargs: {"tools": {"tiers": {"database": {"path": str(db_path)}}}},
    )

    assert resolve_tier_project() == tra_repo.resolve()


def test_resolve_tier_project_prefers_terminal_env_over_shell_workspace(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cyt_repo = tmp_path / "clear-your-tools"
    tra_repo = tmp_path / "tra"
    for repo in (cyt_repo, tra_repo):
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)

    monkeypatch.chdir(cyt_repo)
    monkeypatch.setenv("CYT_SHELL_WORKSPACE", str(tra_repo))
    monkeypatch.setenv("CYT_WORKSPACE", str(tra_repo))
    monkeypatch.setattr("cyt.hook.workspace_resolution.cyt_package_git_root", lambda: cyt_repo.resolve())

    assert resolve_tier_project() == tra_repo.resolve()


def test_resolve_tier_project_prefers_cyt_workspace_env_over_cyt_repo_cwd(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cyt_repo = tmp_path / "clear-your-tools"
    tra_repo = tmp_path / "tra"
    for repo in (cyt_repo, tra_repo):
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)

    monkeypatch.chdir(cyt_repo)
    monkeypatch.setenv("CYT_WORKSPACE", str(tra_repo))
    monkeypatch.setattr("cyt.hook.workspace_resolution.cyt_package_git_root", lambda: cyt_repo.resolve())

    assert resolve_tier_project() == tra_repo.resolve()


def test_cyt_package_git_root_resolves_to_checkout(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("CURSOR_WORKSPACE_LABEL", raising=False)
    root = cyt_package_git_root()
    assert root is not None
    assert (root / "src" / "cyt").is_dir()
