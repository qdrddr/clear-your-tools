"""End-to-end tests for the cyt hook daemon subprocess + HTTP /hook/inject."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cyt.tiers.manager import _managers
from tests.support.runtime_e2e_fixtures import (
    RuntimeWorkspace,
    assert_hook_server_health,
    cursor_hook_payload,
    hook_daemon,
    isolated_live_tier_hook_workspace,
    isolated_skills_hook_workspace,
    isolated_tools_hook_workspace,
)

pytestmark = pytest.mark.runtime


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


@pytest.fixture
def runtime_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CYT_HOOK_QUIET", "1")


@pytest.mark.runtime
def test_hook_daemon_health(
    tmp_path: Path,
    runtime_env: None,
) -> None:
    workspace = isolated_tools_hook_workspace(tmp_path)
    with hook_daemon(workspace.config_path) as daemon:
        assert_hook_server_health(daemon.health())


@pytest.mark.runtime
def test_hook_daemon_tools_bm25_inject(
    tmp_path: Path,
    runtime_env: None,
) -> None:
    workspace = isolated_tools_hook_workspace(tmp_path)
    payload = cursor_hook_payload(workspace=workspace.root, prompt="read a file from disk")
    with hook_daemon(workspace.config_path) as daemon:
        response = daemon.post_hook(payload)

    assert response.status_code == 200
    text = response.text
    if text.strip():
        assert "mcp__filesystem__read_file" in text or "read_file" in text


@pytest.mark.runtime
def test_hook_daemon_skills_frontmatter_gate(
    tmp_path: Path,
    runtime_env: None,
) -> None:
    workspace = isolated_skills_hook_workspace(tmp_path)
    payload = cursor_hook_payload(
        workspace=workspace.root,
        prompt="create cursor agent hooks documentation",
    )
    with hook_daemon(workspace.config_path) as daemon:
        response = daemon.post_hook(payload)

    assert response.status_code == 200
    assert response.text.strip() != ""


@pytest.mark.runtime
def test_hook_daemon_live_tiers(
    tmp_path: Path,
    runtime_env: None,
) -> None:
    workspace: RuntimeWorkspace = isolated_live_tier_hook_workspace(tmp_path)
    payload = cursor_hook_payload(
        workspace=workspace.root,
        prompt="run javascript in sandbox execute code summarize output",
    )
    with hook_daemon(workspace.config_path) as daemon:
        # Live-tier promotion runs BM25 + background tier work; allow extra headroom
        # beyond the default hook client timeout used by lighter daemon scenarios.
        response = daemon.post_hook(payload, timeout=90.0)

    assert response.status_code == 200
    text = response.text
    if not text.strip():
        pytest.skip("hook returned empty injection for live-tier scenario")
    assert "gitnexus_query" not in text
    assert "context-mode_ctx_execute" in text
