"""Integration tests: Cursor hook shell wrappers run under fish and bash.

Opt in with::

    uv run pytest src/tests/integration/test_cursor_hook_shell_wrapper_integration.py --run-integration -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cyt_client.rules_file import (
    RULES_REL_PATH,
    is_rules_placeholder_body,
    read_cursor_rules_injection,
)
from tests.support.cursor_hook_shell_wrapper_fixtures import (
    FISH_BREAKING_INLINE_PREFIX,
    SESSION_START_PAYLOAD,
    assert_fish_rejects_inline_hook_command,
    assert_wrapper_command_is_fish_safe,
    bash_available,
    cursor_dev_client_wrapper_command,
    dev_repo_root,
    fish_available,
    legacy_fish_breaking_client_command,
    run_hook_wrapper_direct,
    run_hook_wrapper_via_fish,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def hook_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "consumer-workspace"
    rules_path = workspace / RULES_REL_PATH
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(
        "---\nalwaysApply: true\n---\n\nstale injection from prior session\n",
        encoding="utf-8",
    )
    return workspace


@pytest.fixture
def client_wrapper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    command = cursor_dev_client_wrapper_command(tmp_path / "hooks", monkeypatch=monkeypatch)
    return Path(command)


@pytest.mark.integration
@pytest.mark.skipif(sys.platform == "win32", reason="Unix fish/bash wrapper integration")
def test_legacy_inline_hook_command_is_fish_incompatible() -> None:
    inline = legacy_fish_breaking_client_command()
    assert FISH_BREAKING_INLINE_PREFIX in inline
    if not fish_available():
        pytest.skip("fish not installed")
    assert_fish_rejects_inline_hook_command(inline)


@pytest.mark.integration
@pytest.mark.skipif(sys.platform == "win32", reason="Unix fish/bash wrapper integration")
def test_wrapper_hook_command_is_fish_compatible(
    client_wrapper: Path,
) -> None:
    if not fish_available():
        pytest.skip("fish not installed")
    assert_wrapper_command_is_fish_safe(str(client_wrapper))


@pytest.mark.integration
@pytest.mark.skipif(sys.platform == "win32", reason="Unix fish/bash wrapper integration")
@pytest.mark.skipif(not bash_available(), reason="bash not installed")
def test_wrapper_runs_under_direct_exec_and_resets_rules(
    client_wrapper: Path,
    hook_workspace: Path,
) -> None:
    result = run_hook_wrapper_direct(
        client_wrapper,
        workspace=hook_workspace,
        payload=SESSION_START_PAYLOAD,
    )

    assert result.returncode == 0, result.stderr
    assert '"continue"' in result.stdout
    body = read_cursor_rules_injection(hook_workspace)
    assert is_rules_placeholder_body(body)


@pytest.mark.integration
@pytest.mark.skipif(sys.platform == "win32", reason="Unix fish/bash wrapper integration")
@pytest.mark.skipif(not fish_available(), reason="fish not installed")
def test_wrapper_runs_when_invoked_from_fish_shell(
    client_wrapper: Path,
    hook_workspace: Path,
) -> None:
    result = run_hook_wrapper_via_fish(
        client_wrapper,
        workspace=hook_workspace,
        payload=SESSION_START_PAYLOAD,
    )

    assert result.returncode == 0, result.stderr
    assert '"continue"' in result.stdout
    body = read_cursor_rules_injection(hook_workspace)
    assert is_rules_placeholder_body(body)


@pytest.mark.integration
@pytest.mark.skipif(sys.platform == "win32", reason="Unix fish/bash wrapper integration")
@pytest.mark.skipif(not bash_available(), reason="bash not installed")
def test_wrapper_inner_command_targets_dev_repo(
    client_wrapper: Path,
) -> None:
    text = client_wrapper.read_text(encoding="utf-8")
    repo_root = dev_repo_root()
    assert str(repo_root) in text
    assert "src/cyt_client/cli.py" in text
