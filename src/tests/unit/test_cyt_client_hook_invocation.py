"""Tests for cyt_client hook invocation and pairing dev/prod mode."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cyt_client.hook_invocation import (
    build_uv_run_dev_command,
    cursor_pairing_hooks,
    is_cyt_hook_command,
    resolve_pairing_dev_context,
)
from cyt_client.mcp_entry import build_cyt_mcp_mcp_server_entry
from cyt_client.pairing import repair_pairing


def test_is_cyt_hook_command_detects_prod_and_dev() -> None:
    assert is_cyt_hook_command("cyt-client")
    assert is_cyt_hook_command("CYT_LAUNCH_AGENT=cursor cyt-client")
    assert is_cyt_hook_command(
        "uv run --directory /tmp/repo src/cyt_client/cli.py",
    )
    assert not is_cyt_hook_command("echo hello")


def test_is_cyt_hook_command_detects_windows_wrapper() -> None:
    from cyt_client.hook_invocation import is_windows_hook_wrapper_command

    wrapper = r"C:\Users\me\.cursor\hooks\cyt-client-dev.cmd"
    assert is_windows_hook_wrapper_command(wrapper)
    assert is_cyt_hook_command(wrapper)


def test_is_cyt_hook_command_detects_windows_dev_uv_command() -> None:
    repo = Path(r"C:\Users\me\git\clear-your-tools")
    command = build_uv_run_dev_command(repo, "src/cyt_client/cli.py")
    assert is_cyt_hook_command(command)


def test_cursor_pairing_hooks_dev_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:


    hooks_dir = tmp_path / "hooks"
    monkeypatch.setattr(
        "cyt_client.hook_invocation.cursor_hooks_dir",
        lambda: hooks_dir,
    )
    monkeypatch.setattr(
        "cyt_client.hook_invocation.use_windows_hook_wrappers",
        lambda *, use_dev: False,
    )

    repo = Path("/tmp/clear-your-tools")
    hooks = cursor_pairing_hooks(
        "cursor",
        use_dev=True,
        dev_repo_root=repo,
        set_launch_agent=False,
    )
    client_cmd = hooks["preToolUse"][0]["command"]
    expected = build_uv_run_dev_command(repo, "src/cyt_client/cli.py")
    assert client_cmd == expected
    assert hooks["postToolUse"][0]["command"] == client_cmd
    assert "get-tool-definitions" in hooks["postToolUse"][0]["matcher"]
    assert hooks["preCompact"][0]["command"] == client_cmd
    assert set(hooks) == {
        "sessionStart",
        "sessionEnd",
        "beforeSubmitPrompt",
        "preToolUse",
        "postToolUse",
        "preCompact",
    }
    daemon_cmd = hooks["sessionStart"][0]["command"]
    assert "src/cyt/proxy/cli.py" in daemon_cmd
    assert str(repo) in daemon_cmd or "clear-your-tools" in daemon_cmd


def test_pairing_replaces_prod_hooks_with_dev_from_mcp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "src" / "cyt_mcp").mkdir(parents=True)
    (repo_root / "src" / "cyt_mcp" / "cli.py").write_text("# stub\n", encoding="utf-8")

    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "preToolUse": [
                        {"command": "CYT_LAUNCH_AGENT=cursor cyt-client", "timeout": 60},
                    ],
                    "beforeMCPExecution": [
                        {"command": "CYT_LAUNCH_AGENT=cursor cyt-client", "timeout": 60},
                    ],
                    "afterMCPExecution": [
                        {
                            "command": "CYT_LAUNCH_AGENT=cursor cyt-client",
                            "matcher": "cyt-mcp_get-tool-definitions|mcp__cyt-mcp__get-tool-definitions",
                            "timeout": 60,
                        },
                    ],
                },
            },
        ),
        encoding="utf-8",
    )
    mcp_path = tmp_path / "mcp.json"
    mcp_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "cyt-mcp": build_cyt_mcp_mcp_server_entry(
                        "cursor",
                        dev_repo_root=repo_root,
                        dev_script_rel="src/cyt_mcp/cli.py",
                    ),
                },
            },
        ),
        encoding="utf-8",
    )
    aggregator_path = tmp_path / "mcp-aggregator.yaml"
    aggregator_path.write_text(
        "\n".join(
            [
                "default_agent: cursor",
                "transport: stdio",
                "http:",
                "  host: 127.0.0.1",
                "  port: 8765",
                "  mcp_path: /mcp",
                "  catalog_path: /catalog",
                "",
            ],
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("cyt_client.pairing._AGENT_HOOK_PATHS", {"cursor": hooks_path})
    monkeypatch.setattr("cyt_client.pairing._AGENT_MCP_PATHS", {"cursor": mcp_path})
    monkeypatch.setattr("cyt_client.mcp_entry.DEFAULT_AGGREGATOR_PATH", aggregator_path)
    monkeypatch.setattr("cyt_client.config.tools_from_includes_cyt_mcp", lambda: True)
    hooks_wrapper_dir = tmp_path / "cursor" / "hooks"
    monkeypatch.setattr(
        "cyt_client.hook_invocation.cursor_hooks_dir",
        lambda: hooks_wrapper_dir,
    )

    repair_pairing(
        {"hook_event_name": "sessionStart", "session_id": "pair-dev"},
        verbose=False,
        runtime_repo=repo_root,
    )

    payload = json.loads(hooks_path.read_text(encoding="utf-8"))
    pre_tool_cmd = payload["hooks"]["preToolUse"][0]["command"]
    from cyt_client.mcp_entry import _inner_command_from_windows_wrapper, _strip_env_prefix

    resolved_cmd = _strip_env_prefix(pre_tool_cmd)
    if sys.platform == "win32":
        assert "cyt-client-dev.cmd" in pre_tool_cmd
        wrapper = hooks_wrapper_dir / "cyt-client-dev.cmd"
        assert wrapper.is_file()
        inner = _inner_command_from_windows_wrapper(str(wrapper))
        assert inner is not None
        assert " run --directory " in inner
        assert str(repo_root) in inner or repo_root.as_posix() in inner
        assert "src/cyt_client/cli.py" in inner
    else:
        assert "uv run --directory" in resolved_cmd
        assert str(repo_root) in resolved_cmd
        assert "src/cyt_client/cli.py" in resolved_cmd
    assert payload["hooks"]["postToolUse"][0]["command"] == pre_tool_cmd
    assert "beforeMCPExecution" not in payload["hooks"]
    assert "afterMCPExecution" not in payload["hooks"]
    assert len(payload["hooks"]["preToolUse"]) == 1


def test_resolve_pairing_dev_context_from_mcp_file(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "src" / "cyt_mcp").mkdir(parents=True)
    (repo_root / "src" / "cyt_mcp" / "cli.py").write_text("# stub\n", encoding="utf-8")
    mcp_path = tmp_path / "mcp.json"
    mcp_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "cyt-mcp": build_cyt_mcp_mcp_server_entry(
                        "cursor",
                        dev_repo_root=repo_root,
                        dev_script_rel="src/cyt_mcp/cli.py",
                    ),
                },
            },
        ),
        encoding="utf-8",
    )
    use_dev, resolved_repo = resolve_pairing_dev_context(
        "cursor",
        hooks_path=None,
        mcp_path=mcp_path,
        runtime_repo=None,
    )
    assert use_dev is True
    assert resolved_repo == repo_root


def test_resolve_pairing_dev_context_from_hooks_wrapper_file(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "src" / "cyt_mcp").mkdir(parents=True)
    (repo_root / "src" / "cyt_mcp" / "cli.py").write_text("# stub\n", encoding="utf-8")
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    wrapper = hooks_dir / "cyt-client-dev.cmd"
    wrapper.write_text(
        "\n".join(
            (
                "@echo off",
                build_uv_run_dev_command(repo_root, "src/cyt_client/cli.py"),
            ),
        )
        + "\n",
        encoding="utf-8",
    )
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "preToolUse": [{"command": str(wrapper), "timeout": 60}],
                },
            },
        ),
        encoding="utf-8",
    )
    use_dev, resolved_repo = resolve_pairing_dev_context(
        "cursor",
        hooks_path=hooks_path,
        mcp_path=None,
        runtime_repo=None,
    )
    assert use_dev is True
    assert resolved_repo == repo_root
