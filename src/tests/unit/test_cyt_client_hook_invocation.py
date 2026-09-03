"""Tests for cyt_client hook invocation and pairing dev/prod mode."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cyt_client.hook_executable import build_uv_run_dev_command
from cyt_client.hook_invocation import (
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


def test_pairing_does_not_modify_hooks_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "src" / "cyt_mcp").mkdir(parents=True)
    (repo_root / "src" / "cyt_mcp" / "cli.py").write_text("# stub\n", encoding="utf-8")

    hooks_before = {
        "version": 1,
        "hooks": {
            "preToolUse": [
                {"command": "CYT_LAUNCH_AGENT=cursor cyt-client", "timeout": 60},
            ],
            "beforeMCPExecution": [
                {"command": "CYT_LAUNCH_AGENT=cursor cyt-client", "timeout": 60},
            ],
        },
    }
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(json.dumps(hooks_before) + "\n", encoding="utf-8")
    mcp_path = tmp_path / "mcp.json"
    mcp_path.write_text(json.dumps({"mcpServers": {}}) + "\n", encoding="utf-8")
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

    repair_pairing(
        {"hook_event_name": "sessionStart", "session_id": "pair-dev"},
        verbose=False,
        runtime_repo=repo_root,
    )

    assert json.loads(hooks_path.read_text(encoding="utf-8")) == hooks_before
    mcp_payload = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert "cyt-mcp" in mcp_payload["mcpServers"]


def test_pairing_repairs_workspace_mcp_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    cyt_dir = repo_root / ".cursor" / "cyt"
    cyt_dir.mkdir(parents=True)
    (cyt_dir / "mcp").mkdir()
    (cyt_dir / "mcp" / "cursor.json").write_text(
        '{"mcpServers": {"backend": {"command": "echo"}}}',
        encoding="utf-8",
    )
    (cyt_dir / "config").mkdir()
    (cyt_dir / "config" / "mcp-aggregator.yaml").write_text(
        "default_agent: cursor\n",
        encoding="utf-8",
    )

    global_mcp = tmp_path / "global-mcp.json"
    global_mcp.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    workspace_mcp = repo_root / ".cursor" / "mcp.json"
    workspace_mcp.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

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

    monkeypatch.setattr("cyt_client.pairing._AGENT_MCP_PATHS", {"cursor": global_mcp})
    monkeypatch.setattr("cyt_client.mcp_entry.DEFAULT_AGGREGATOR_PATH", aggregator_path)
    monkeypatch.setattr("cyt_client.config.tools_from_includes_cyt_mcp", lambda: True)

    from cyt_client.mcp_entry import CYT_MCP_SERVER_KEY, CYT_MCP_WORKSPACE_SERVER_KEY

    repair_pairing(
        {
            "hook_event_name": "sessionStart",
            "session_id": "pair-ws",
            "workspace_roots": [str(repo_root.resolve())],
        },
        verbose=False,
    )

    global_payload = json.loads(global_mcp.read_text(encoding="utf-8"))
    workspace_payload = json.loads(workspace_mcp.read_text(encoding="utf-8"))
    assert CYT_MCP_SERVER_KEY in global_payload["mcpServers"]
    assert CYT_MCP_WORKSPACE_SERVER_KEY in workspace_payload["mcpServers"]
    ws_entry = workspace_payload["mcpServers"][CYT_MCP_WORKSPACE_SERVER_KEY]
    assert "--config" in ws_entry.get("args", [])


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
