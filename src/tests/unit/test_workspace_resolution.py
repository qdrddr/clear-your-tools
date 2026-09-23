"""Tests for language-agnostic consumer workspace resolution."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from cyt.hook.active_workspace import list_active_workspaces, touch_active_workspace
from cyt.hook.workspace_resolution import (
    CYT_SHELL_WORKSPACE_ENV,
    CYT_UV_INVOCATION_FILENAME,
    CYT_WORKSPACE_ENV,
    WorkspacePathNotAbsoluteError,
    WorkspaceResolutionConflictError,
    WorkspaceResolutionSource,
    absolute_workspace_arg,
    build_cyt_uv_wrapper_invocation_payload,
    cyt_uv_wrapper_script_paths,
    ensure_cyt_mcp_dev_wrapper,
    ensure_cyt_uv_wrapper_scripts,
    ensure_vscode_terminal_workspace_env,
    format_hook_setup_workspace_report,
    hook_setup_mcp_workspace_root,
    hook_setup_workspace_required_message,
    print_hook_setup_workspace_report,
    require_absolute_workspace_dir,
    resolve_consumer_project_root,
    resolve_consumer_workspace,
    resolve_hook_setup_consumer_root,
    resolve_hook_setup_workspace,
    write_cyt_uv_wrapper_invocation,
)


@pytest.fixture(autouse=True)
def _isolate_workspace_resolution_env(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> Iterator[None]:
    for key in (
        CYT_WORKSPACE_ENV,
        CYT_SHELL_WORKSPACE_ENV,
        "PWD",
        "OLDPWD",
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


def test_resolve_consumer_workspace_prefers_terminal_env_over_shell(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(CYT_SHELL_WORKSPACE_ENV, str(repo))
    monkeypatch.setenv(CYT_WORKSPACE_ENV, str(repo))

    resolution = resolve_consumer_workspace()
    assert resolution.root == repo.resolve()
    assert resolution.source == WorkspaceResolutionSource.TERMINAL_ENV


def test_resolve_consumer_workspace_falls_back_to_shell_when_terminal_unset(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    shell_repo = tmp_path / "shell"
    shell_repo.mkdir()
    subprocess.run(["git", "init"], cwd=shell_repo, check=True, capture_output=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(CYT_SHELL_WORKSPACE_ENV, str(shell_repo))

    resolution = resolve_consumer_workspace()
    assert resolution.root == shell_repo.resolve()
    assert resolution.source == WorkspaceResolutionSource.SHELL


def test_resolve_consumer_workspace_uses_terminal_env_when_shell_unset(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(CYT_WORKSPACE_ENV, str(repo))

    resolution = resolve_consumer_workspace()
    assert resolution.root == repo.resolve()
    assert resolution.source == WorkspaceResolutionSource.TERMINAL_ENV


def test_resolve_consumer_workspace_explicit_overrides_env(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    explicit_repo = tmp_path / "explicit"
    other_repo = tmp_path / "other"
    for repo in (explicit_repo, other_repo):
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(CYT_SHELL_WORKSPACE_ENV, str(other_repo))
    monkeypatch.setenv(CYT_WORKSPACE_ENV, str(other_repo))

    resolution = resolve_consumer_workspace(workspace=explicit_repo)
    assert resolution.root == explicit_repo.resolve()
    assert resolution.source == WorkspaceResolutionSource.EXPLICIT


def test_resolve_consumer_workspace_raises_on_shell_terminal_conflict(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    shell_repo = tmp_path / "shell"
    terminal_repo = tmp_path / "terminal"
    for repo in (shell_repo, terminal_repo):
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(CYT_SHELL_WORKSPACE_ENV, str(shell_repo))
    monkeypatch.setenv(CYT_WORKSPACE_ENV, str(terminal_repo))

    with pytest.raises(WorkspaceResolutionConflictError):
        resolve_consumer_workspace()


def test_resolve_consumer_workspace_ignores_template_workspace_env(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setenv(CYT_WORKSPACE_ENV, "${workspaceFolder}")

    resolution = resolve_consumer_workspace()
    assert resolution.root == repo.resolve()


def test_resolve_consumer_workspace_uses_active_registry_when_cwd_is_cyt_repo(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cyt_repo = tmp_path / "clear-your-tools"
    tra_repo = tmp_path / "tra"
    for repo in (cyt_repo, tra_repo):
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)

    registry_path = tmp_path / "active-workspaces" / "by-agent.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "cursor": {
                    str(tra_repo.resolve()): 200,
                    str(cyt_repo.resolve()): 100,
                },
            },
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(cyt_repo)
    monkeypatch.setattr("cyt.hook.active_workspace._ACTIVE_WORKSPACE_FILE", registry_path)
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.cyt_package_git_root",
        lambda: cyt_repo.resolve(),
    )

    resolution = resolve_consumer_workspace()
    assert resolution.root == tra_repo.resolve()
    assert resolution.source == WorkspaceResolutionSource.ACTIVE_REGISTRY


def test_touch_active_workspace_persists_recent_paths(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    registry_path = tmp_path / "active-workspaces" / "by-agent.json"
    monkeypatch.setattr("cyt.hook.active_workspace._ACTIVE_WORKSPACE_FILE", registry_path)

    repo = tmp_path / "repo"
    repo.mkdir()
    touch_active_workspace("cursor", repo, seen_ms=123)

    rows = list_active_workspaces("cursor")
    assert rows == [{"root_path": str(repo.resolve()), "last_seen_ms": 123}]


def test_resolve_consumer_project_root_delegates_to_workspace_resolution(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setenv(CYT_WORKSPACE_ENV, str(repo))

    assert resolve_consumer_project_root() == repo.resolve()


def test_ensure_vscode_terminal_workspace_env_writes_cyt_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    changed = ensure_vscode_terminal_workspace_env(workspace)
    assert changed is True
    settings_path = workspace / ".vscode" / "settings.json"
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    for env_key in (
        "terminal.integrated.env",
        "terminal.integrated.env.windows",
        "terminal.integrated.env.linux",
        "terminal.integrated.env.osx",
    ):
        assert payload[env_key][CYT_WORKSPACE_ENV] == "${workspaceFolder}"

    changed_again = ensure_vscode_terminal_workspace_env(workspace)
    assert changed_again is False


def test_ensure_vscode_terminal_workspace_env_upgrades_windows_only(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    vscode_dir = workspace / ".vscode"
    vscode_dir.mkdir(parents=True)
    settings_path = vscode_dir / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "terminal.integrated.env.windows": {
                    CYT_WORKSPACE_ENV: "${workspaceFolder}",
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    changed = ensure_vscode_terminal_workspace_env(workspace)
    assert changed is True
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    assert payload["terminal.integrated.env.windows"][CYT_WORKSPACE_ENV] == "${workspaceFolder}"
    assert payload["terminal.integrated.env.linux"][CYT_WORKSPACE_ENV] == "${workspaceFolder}"
    assert payload["terminal.integrated.env.osx"][CYT_WORKSPACE_ENV] == "${workspaceFolder}"
    assert payload["terminal.integrated.env"][CYT_WORKSPACE_ENV] == "${workspaceFolder}"


def test_ensure_cyt_uv_wrapper_scripts_copies_to_agent_hooks_dir(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cyt_dir = tmp_path / "cursor" / "hooks" / "cyt"
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.agent_cyt_uv_dir",
        lambda _agent: cyt_dir,
    )

    ps1, sh = ensure_cyt_uv_wrapper_scripts("cursor")

    assert ps1 == cyt_dir / "uv.ps1"
    assert sh == cyt_dir / "uv.sh"
    assert ps1.is_file()
    assert sh.is_file()
    ps1_text = ps1.read_text(encoding="utf-8")
    assert "CYT_SHELL_WORKSPACE" in ps1_text
    assert "Add-HookWorkspaceArg" not in ps1_text
    assert "Invoke-CytViaUv -CytArgs $Args" in ps1_text
    assert "while ($dir)" in ps1_text
    sidecar = cyt_dir / CYT_UV_INVOCATION_FILENAME
    assert sidecar.is_file()
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["mode"] in {"dev", "tool"}


def test_write_cyt_uv_wrapper_invocation_prefers_dev_repo(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cyt_dir = tmp_path / "cursor" / "hooks" / "cyt"
    dev_repo = tmp_path / "clear-your-tools"
    dev_repo.mkdir()
    (dev_repo / ".git").mkdir()
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.agent_cyt_uv_dir",
        lambda _agent: cyt_dir,
    )
    from cyt.hook import cli_invocation as cli_invocation_mod

    monkeypatch.setattr(
        cli_invocation_mod,
        "detect_hook_cli_invocation",
        lambda: cli_invocation_mod.HookCliInvocation(mode="dev", repo_root=dev_repo),
    )
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.cyt_package_git_root",
        lambda: None,
    )

    path = write_cyt_uv_wrapper_invocation("cursor")

    assert path is not None
    assert path == cyt_dir / CYT_UV_INVOCATION_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {
        "mode": "dev",
        "repo_root": str(dev_repo.resolve()),
        "cli": "src/cyt/cli/app.py",
    }


def test_build_cyt_uv_wrapper_invocation_payload_falls_back_to_tool(
    monkeypatch: MonkeyPatch,
) -> None:
    from cyt.hook import cli_invocation as cli_invocation_mod

    monkeypatch.setattr(
        cli_invocation_mod,
        "detect_hook_cli_invocation",
        lambda: cli_invocation_mod.HookCliInvocation(mode="installed", repo_root=None),
    )
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.cyt_package_git_root",
        lambda: None,
    )

    payload = build_cyt_uv_wrapper_invocation_payload()

    assert payload == {
        "mode": "tool",
        "package": "clear-your-tools",
        "executable": "cyt",
    }


def test_ensure_cyt_mcp_dev_wrapper_writes_hooks_cyt_mcp_dev_cmd(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cyt_dir = tmp_path / "cursor" / "hooks" / "cyt"
    dev_repo = tmp_path / "clear-your-tools"
    dev_repo.mkdir()
    (dev_repo / "src" / "cyt_mcp").mkdir(parents=True)
    (dev_repo / "src" / "cyt_mcp" / "cli.py").write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.agent_cyt_uv_dir",
        lambda _agent: cyt_dir,
    )
    from cyt.hook import cli_invocation as cli_invocation_mod

    monkeypatch.setattr(
        cli_invocation_mod,
        "detect_hook_cli_invocation",
        lambda: cli_invocation_mod.HookCliInvocation(mode="dev", repo_root=dev_repo),
    )
    monkeypatch.setattr("cyt.platform.compat.is_windows", lambda: True)

    wrapper = ensure_cyt_mcp_dev_wrapper("cursor")

    assert wrapper is not None
    assert wrapper == cyt_dir / "mcp-dev.cmd"
    assert wrapper.is_file()
    text = wrapper.read_text(encoding="utf-8")
    assert str(dev_repo.resolve()) in text
    assert "src/cyt_mcp/cli.py" in text


def test_ensure_cyt_uv_wrapper_scripts_removes_legacy_flat_wrappers(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    hooks_dir = tmp_path / "cursor" / "hooks"
    cyt_dir = hooks_dir / "cyt"
    legacy = hooks_dir / "cyt-uv.ps1"
    hooks_dir.mkdir(parents=True)
    legacy.write_text("legacy\n", encoding="utf-8")
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.agent_cyt_uv_dir",
        lambda _agent: cyt_dir,
    )

    ensure_cyt_uv_wrapper_scripts("cursor")

    assert not legacy.is_file()
    assert (cyt_dir / "uv.ps1").is_file()


def test_hook_setup_workspace_required_message_includes_agent_hooks_wrappers(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cyt_dir = tmp_path / "cursor" / "hooks" / "cyt"
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.agent_cyt_uv_dir",
        lambda _agent: cyt_dir,
    )

    ps1, sh = cyt_uv_wrapper_script_paths("cursor")
    message = hook_setup_workspace_required_message(agent="cursor")

    assert str(ps1) in message
    assert str(sh) in message
    assert "hooks\\cyt" in message or "hooks/cyt" in message
    assert "hook cursor" in message
    assert ps1.name == "uv.ps1"
    assert sh.name == "uv.sh"


def test_format_hook_setup_workspace_report_shows_detection_source(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cyt_repo = tmp_path / "clear-your-tools"
    tra_repo = tmp_path / "tra"
    for repo in (cyt_repo, tra_repo):
        repo.mkdir()
        (repo / ".git").mkdir()
    monkeypatch.chdir(cyt_repo)
    monkeypatch.setenv("PWD", str(tra_repo))
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.cyt_package_git_root",
        lambda: cyt_repo.resolve(),
    )

    resolution = resolve_hook_setup_workspace()
    report = format_hook_setup_workspace_report(resolution)

    assert "Process cwd:" not in report
    assert str(tra_repo) in report
    assert "Detected via: shell env ($CYT_SHELL_WORKSPACE=" in report


def test_print_hook_setup_workspace_report_always_includes_detected_via(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    resolution = resolve_hook_setup_workspace()

    print_hook_setup_workspace_report(resolution)

    captured = capsys.readouterr()
    assert "Workspace detection:" in captured.out
    assert "Consumer workspace: (none)" in captured.out
    assert "Detected via: none (unresolved)" in captured.out


def test_resolve_hook_setup_consumer_root_uses_explicit_workspace(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cyt_repo = tmp_path / "clear-your-tools"
    tra_repo = tmp_path / "tra"
    for repo in (cyt_repo, tra_repo):
        repo.mkdir()
        (repo / ".git").mkdir()
    monkeypatch.chdir(cyt_repo)
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.cyt_package_git_root",
        lambda: cyt_repo.resolve(),
    )

    assert resolve_hook_setup_consumer_root(workspace=tra_repo) == tra_repo.resolve()


def test_resolve_hook_setup_consumer_root_uses_pwd_env(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cyt_repo = tmp_path / "clear-your-tools"
    tra_repo = tmp_path / "tra"
    for repo in (cyt_repo, tra_repo):
        repo.mkdir()
        (repo / ".git").mkdir()
    monkeypatch.chdir(cyt_repo)
    monkeypatch.setenv("PWD", str(tra_repo))
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.cyt_package_git_root",
        lambda: cyt_repo.resolve(),
    )

    assert resolve_hook_setup_consumer_root() == tra_repo.resolve()


def test_resolve_hook_setup_consumer_root_prefers_pwd_over_terminal_env(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cyt_repo = tmp_path / "clear-your-tools"
    tra_repo = tmp_path / "tra"
    other_repo = tmp_path / "other"
    for repo in (cyt_repo, tra_repo, other_repo):
        repo.mkdir()
        (repo / ".git").mkdir()
    monkeypatch.chdir(cyt_repo)
    monkeypatch.setenv("PWD", str(tra_repo))
    monkeypatch.setenv(CYT_WORKSPACE_ENV, str(other_repo))
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.cyt_package_git_root",
        lambda: cyt_repo.resolve(),
    )

    assert resolve_hook_setup_consumer_root() == tra_repo.resolve()


def test_resolve_hook_setup_consumer_root_uses_shell_env(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cyt_repo = tmp_path / "clear-your-tools"
    tra_repo = tmp_path / "tra"
    for repo in (cyt_repo, tra_repo):
        repo.mkdir()
        (repo / ".git").mkdir()
    monkeypatch.chdir(cyt_repo)
    monkeypatch.setenv(CYT_SHELL_WORKSPACE_ENV, str(tra_repo))
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.cyt_package_git_root",
        lambda: cyt_repo.resolve(),
    )

    assert resolve_hook_setup_consumer_root() == tra_repo.resolve()


def test_resolve_hook_setup_consumer_root_continues_when_cyt_workspace_env_set(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cyt_repo = tmp_path / "clear-your-tools"
    cyt_repo.mkdir()
    (cyt_repo / ".git").mkdir()
    monkeypatch.chdir(cyt_repo)
    monkeypatch.setenv(CYT_WORKSPACE_ENV, str(cyt_repo))
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.cyt_package_git_root",
        lambda: cyt_repo.resolve(),
    )

    assert resolve_hook_setup_consumer_root() is None


def test_hook_setup_mcp_workspace_root_uses_cyt_checkout_when_consumer_skipped(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cyt_repo = tmp_path / "clear-your-tools"
    consumer = tmp_path / "consumer"
    for repo in (cyt_repo, consumer):
        repo.mkdir()
        (repo / ".git").mkdir()
    monkeypatch.chdir(cyt_repo)
    monkeypatch.setenv(CYT_WORKSPACE_ENV, str(cyt_repo))
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.cyt_package_git_root",
        lambda: cyt_repo.resolve(),
    )

    resolution = resolve_hook_setup_workspace()
    consumer_root = resolve_hook_setup_consumer_root()
    assert consumer_root is None
    assert hook_setup_mcp_workspace_root(resolution, consumer_root) == cyt_repo.resolve()


def test_hook_setup_mcp_workspace_root_prefers_consumer_root(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cyt_repo = tmp_path / "clear-your-tools"
    consumer = tmp_path / "consumer"
    for repo in (cyt_repo, consumer):
        repo.mkdir()
        (repo / ".git").mkdir()
    monkeypatch.chdir(cyt_repo)
    monkeypatch.setenv(CYT_WORKSPACE_ENV, str(consumer))
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.cyt_package_git_root",
        lambda: cyt_repo.resolve(),
    )

    resolution = resolve_hook_setup_workspace()
    consumer_root = resolve_hook_setup_consumer_root()
    assert consumer_root == consumer.resolve()
    assert hook_setup_mcp_workspace_root(resolution, consumer_root) == consumer.resolve()


def test_resolve_hook_setup_consumer_root_continues_when_explicit_cyt_workspace(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cyt_repo = tmp_path / "clear-your-tools"
    cyt_repo.mkdir()
    (cyt_repo / ".git").mkdir()
    monkeypatch.chdir(cyt_repo)
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.cyt_package_git_root",
        lambda: cyt_repo.resolve(),
    )

    assert resolve_hook_setup_consumer_root(workspace=cyt_repo) is None


def test_resolve_hook_setup_consumer_root_requires_workspace_from_cyt_cwd(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cyt_repo = tmp_path / "clear-your-tools"
    cyt_repo.mkdir()
    (cyt_repo / ".git").mkdir()
    monkeypatch.chdir(cyt_repo)
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.cyt_package_git_root",
        lambda: cyt_repo.resolve(),
    )

    with pytest.raises(SystemExit, match="requires a consumer workspace"):
        resolve_hook_setup_consumer_root()


def test_resolve_hook_setup_consumer_root_ignores_active_workspace_registry(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    from cyt.hook.active_workspace import touch_active_workspace

    cyt_repo = tmp_path / "clear-your-tools"
    tra_repo = tmp_path / "tra"
    for repo in (cyt_repo, tra_repo):
        repo.mkdir()
        (repo / ".git").mkdir()
    monkeypatch.chdir(cyt_repo)
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.cyt_package_git_root",
        lambda: cyt_repo.resolve(),
    )
    touch_active_workspace("cursor", tra_repo)

    with pytest.raises(SystemExit, match="requires a consumer workspace"):
        resolve_hook_setup_consumer_root()


def test_resolve_hook_setup_consumer_root_requires_env_when_only_cwd(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cyt_repo = tmp_path / "clear-your-tools"
    tra_repo = tmp_path / "tra"
    for repo in (cyt_repo, tra_repo):
        repo.mkdir()
        (repo / ".git").mkdir()
    monkeypatch.chdir(tra_repo)
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.cyt_package_git_root",
        lambda: cyt_repo.resolve(),
    )

    with pytest.raises(SystemExit, match="requires a consumer workspace"):
        resolve_hook_setup_consumer_root()


def test_require_absolute_workspace_dir_rejects_home_shorthand() -> None:
    with pytest.raises(WorkspacePathNotAbsoluteError, match=r"not \./, \../, or ~"):
        require_absolute_workspace_dir("~/projects/repo")


def test_require_absolute_workspace_dir_rejects_relative_path() -> None:
    with pytest.raises(WorkspacePathNotAbsoluteError, match=r"not \./, \../, or ~"):
        require_absolute_workspace_dir("./repo")


def test_absolute_workspace_arg_rejects_relative_path() -> None:
    with pytest.raises(WorkspacePathNotAbsoluteError):
        absolute_workspace_arg("../repo")


def test_resolve_consumer_workspace_rejects_relative_env(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(CYT_WORKSPACE_ENV, "./repo")

    with pytest.raises(WorkspacePathNotAbsoluteError, match=r"\$CYT_WORKSPACE"):
        resolve_consumer_workspace()


def test_resolve_consumer_workspace_explicit_requires_absolute_path(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(WorkspacePathNotAbsoluteError, match="--workspace"):
        resolve_consumer_workspace(workspace=Path("./repo"))

    resolution = resolve_consumer_workspace(workspace=repo.resolve())
    assert resolution.root == repo.resolve()
