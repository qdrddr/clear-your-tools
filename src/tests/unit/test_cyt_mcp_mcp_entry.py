"""Tests for cyt-mcp MCP server entry builders."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cyt.hook.cli_invocation import (
    HookCliInvocation,
    build_uv_run_dev_command,
    cyt_mcp_cli_script_relpath,
    cyt_mcp_mcp_server_entry,
)
from cyt.tools import cyt_mcp_setup
from cyt_client.hook_executable import repo_root_from_uv_run_hook_command
from cyt_client.mcp_entry import (
    backend_mcp_servers,
    build_cyt_mcp_mcp_server_entry,
    dev_invocation_from_hooks_file,
    is_cyt_dev_hook_command,
    is_cyt_mcp_frontend_server,
)


def test_build_installed_cyt_mcp_entry() -> None:
    entry = build_cyt_mcp_mcp_server_entry("cursor")
    assert entry == {"command": "cyt-mcp", "args": ["--agent", "cursor"]}


def test_build_dev_cyt_mcp_entry() -> None:
    repo_root = Path("/tmp/clear-your-tools")
    script_rel = cyt_mcp_cli_script_relpath()
    entry = build_cyt_mcp_mcp_server_entry(
        "cursor",
        dev_repo_root=repo_root,
        dev_script_rel=script_rel,
    )
    assert entry == {
        "command": "uv",
        "args": ["run", "--directory", str(repo_root), script_rel, "--agent", "cursor"],
    }


def test_build_dev_workspace_cyt_mcp_entry_uses_workspace_folder() -> None:
    repo_root = Path("/tmp/clear-your-tools")
    script_rel = cyt_mcp_cli_script_relpath()
    entry = build_cyt_mcp_mcp_server_entry(
        "cursor",
        dev_repo_root=repo_root,
        dev_script_rel=script_rel,
        workspace_cwd="${workspaceFolder}",
        aggregator_config="${workspaceFolder}/.cursor/cyt/config/mcp-aggregator.yaml",
    )
    assert entry["cwd"] == "${workspaceFolder}"
    assert entry["args"][1:3] == ["--directory", "${workspaceFolder}"]
    assert entry["args"][-2:] == [
        "--config",
        "${workspaceFolder}/.cursor/cyt/config/mcp-aggregator.yaml",
    ]


def test_cyt_mcp_mcp_server_entry_uses_dev_invocation() -> None:
    repo_root = Path("/tmp/clear-your-tools")
    invocation = HookCliInvocation(mode="dev", repo_root=repo_root)
    entry = cyt_mcp_mcp_server_entry("cursor", invocation=invocation)
    assert entry["command"] == "uv"
    assert entry["args"][0:3] == ["run", "--directory", str(repo_root)]
    assert entry["args"][-2:] == ["--agent", "cursor"]


def test_repo_root_from_uv_run_hook_command() -> None:
    command = "uv run --directory /tmp/clear-your-tools src/cyt_client/cli.py"
    assert repo_root_from_uv_run_hook_command(command) == Path("/tmp/clear-your-tools")


def test_is_cyt_dev_hook_command() -> None:
    assert is_cyt_dev_hook_command(
        "uv run --directory /tmp/repo src/cyt_client/cli.py",
    )
    assert is_cyt_dev_hook_command(
        "CYT_LAUNCH_AGENT=cursor uv run --directory /tmp/repo "
        "src/cyt/proxy/cli.py hook daemon start --unattended",
    )
    assert not is_cyt_dev_hook_command("cyt-client")


def test_dev_invocation_from_hooks_file(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "src" / "cyt_mcp").mkdir(parents=True)
    (repo_root / "src" / "cyt_mcp" / "cli.py").write_text("# stub\n", encoding="utf-8")
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "sessionStart": [
                        {
                            "command": build_uv_run_dev_command(
                                repo_root,
                                "src/cyt_client/cli.py",
                            ),
                        },
                    ],
                },
            },
        ),
        encoding="utf-8",
    )
    dev = dev_invocation_from_hooks_file(hooks_path)
    assert dev is not None
    assert dev[0] == repo_root
    assert dev[1] == "src/cyt_mcp/cli.py"


def test_write_agent_cyt_mcp_entry_dev_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp_path = tmp_path / "mcp.json"
    mcp_path.write_text(
        json.dumps({"mcpServers": {"other": {"command": "echo"}}}),
        encoding="utf-8",
    )
    monkeypatch.setitem(cyt_mcp_setup._AGENT_SOURCE_PATHS, "cursor", mcp_path)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    invocation = HookCliInvocation(mode="dev", repo_root=repo_root)
    cyt_mcp_setup.write_agent_cyt_mcp_entry("cursor", invocation=invocation)
    payload = json.loads(mcp_path.read_text(encoding="utf-8"))
    entry = payload["mcpServers"]["cyt-mcp"]
    assert entry["command"] == "uv"
    assert entry["args"][1:3] == ["--directory", str(repo_root)]
    assert payload["mcpServers"]["other"]["command"] == "echo"


def test_prompt_cyt_mcp_transport_defaults_to_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cyt_mcp_setup, "_prompt", lambda _label, default: default)
    assert cyt_mcp_setup.prompt_cyt_mcp_transport() == "http"


def test_write_agent_cyt_mcp_entry_http_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp_path = tmp_path / "mcp.json"
    monkeypatch.setitem(cyt_mcp_setup._AGENT_SOURCE_PATHS, "cursor", mcp_path)
    cyt_mcp_setup.write_agent_cyt_mcp_entry("cursor", transport="http")
    payload = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["cyt-mcp"] == {"url": "http://127.0.0.1:8765/mcp"}


def test_cyt_mcp_hook_settings_overlay_http() -> None:
    overlay = cyt_mcp_setup.cyt_mcp_hook_settings_overlay(transport="http", agent="cursor")
    assert overlay["agent"] == "cursor"
    assert overlay["catalog_url"] == "http://127.0.0.1:8765/catalog"


def test_is_cyt_mcp_frontend_server() -> None:
    prod = {"command": "cyt-mcp", "args": ["--agent", "cursor"]}
    dev = {
        "command": "uv",
        "args": ["run", "--directory", "/tmp/repo", "src/cyt_mcp/cli.py", "--agent", "cursor"],
    }
    backend = {"command": "npx", "args": ["-y", "some-mcp-server"]}
    assert is_cyt_mcp_frontend_server("cyt-mcp", prod)
    assert is_cyt_mcp_frontend_server("other", dev)
    assert not is_cyt_mcp_frontend_server("wiseinfotec", backend)


def test_migrate_agent_backends_excludes_cyt_mcp_self(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "mcp.json"
    target_dir = tmp_path / "cyt_mcp"
    source.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "cyt-mcp": {
                        "command": "uv",
                        "args": [
                            "run",
                            "--directory",
                            "/tmp/repo",
                            "src/cyt_mcp/cli.py",
                            "--agent",
                            "cursor",
                        ],
                    },
                    "wiseinfotec": {"url": "https://mcp.example.com/mcp"},
                },
            },
        ),
        encoding="utf-8",
    )
    monkeypatch.setitem(cyt_mcp_setup._AGENT_SOURCE_PATHS, "cursor", source)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_DIR", target_dir)
    cyt_mcp_setup.migrate_agent_backends("cursor")
    payload = json.loads((target_dir / "cursor.json").read_text(encoding="utf-8"))
    assert "cyt-mcp" not in payload["mcpServers"]
    assert "wiseinfotec" in payload["mcpServers"]


def test_backend_mcp_servers_filters_self() -> None:
    servers = {
        "cyt-mcp": {"command": "cyt-mcp", "args": ["--agent", "cursor"]},
        "backend": {"url": "https://example.com/mcp"},
    }
    filtered = backend_mcp_servers(servers)
    assert filtered == {"backend": {"url": "https://example.com/mcp"}}


def test_setup_cyt_mcp_strips_backends_from_agent_mcp_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "mcp.json"
    target_dir = tmp_path / "backends"
    aggregator_path = tmp_path / "mcp-aggregator.yaml"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    source.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "cyt-mcp": {"command": "cyt-mcp", "args": ["--agent", "cursor"]},
                    "codebase-memory-mcp": {
                        "command": "/usr/local/bin/codebase-memory-mcp",
                        "enabled": False,
                    },
                },
            },
        ),
        encoding="utf-8",
    )
    monkeypatch.setitem(cyt_mcp_setup._AGENT_SOURCE_PATHS, "cursor", source)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_DIR", target_dir)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_AGGREGATOR_PATH", aggregator_path)
    invocation = HookCliInvocation(mode="dev", repo_root=repo_root)
    cyt_mcp_setup.setup_cyt_mcp_for_agent("cursor", invocation=invocation, transport="stdio")
    agent_payload = json.loads(source.read_text(encoding="utf-8"))
    assert set(agent_payload["mcpServers"]) == {"cyt-mcp"}
    assert agent_payload["mcpServers"]["cyt-mcp"]["command"] == "uv"
    backend_payload = json.loads((target_dir / "cursor.json").read_text(encoding="utf-8"))
    assert "codebase-memory-mcp" in backend_payload["mcpServers"]
    assert "cyt-mcp" not in backend_payload["mcpServers"]


def test_write_mcp_aggregator_yaml_writes_explicit_verify_only_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregator_path = tmp_path / "mcp-aggregator.yaml"
    mcp_dir = tmp_path / "mcp"
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_AGGREGATOR_PATH", aggregator_path)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_DIR", mcp_dir)

    cyt_mcp_setup.write_mcp_aggregator_yaml("cursor", verify_only=False)

    text = aggregator_path.read_text(encoding="utf-8")
    assert "verify_only: false" in text
    assert "verify_only: true" not in text


def test_write_mcp_aggregator_yaml_writes_explicit_verify_only_true(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregator_path = tmp_path / "mcp-aggregator.yaml"
    mcp_dir = tmp_path / "mcp"
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_AGGREGATOR_PATH", aggregator_path)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_DIR", mcp_dir)

    cyt_mcp_setup.write_mcp_aggregator_yaml("cursor", verify_only=True)

    assert "verify_only: true" in aggregator_path.read_text(encoding="utf-8")
