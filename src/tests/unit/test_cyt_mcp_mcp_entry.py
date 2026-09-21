"""Tests for cyt-mcp MCP server entry builders."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cyt_client.hook_executable import resolve_hook_executable
from cyt_client.hook_invocation import cyt_mcp_dev_wrapper_path

from cyt.hook.cli_invocation import (
    HookCliInvocation,
    build_uv_run_dev_command,
    cyt_mcp_cli_script_relpath,
    cyt_mcp_mcp_server_entry,
)
from cyt.tools import cyt_mcp_setup
from cyt_client.hook_executable import repo_root_from_uv_run_hook_command
from cyt_client.mcp_entry import (
    CYT_MCP_SERVER_KEY,
    CYT_MCP_USER_SERVER_KEY,
    LEGACY_CYT_MCP_SERVER_KEY,
    backend_mcp_servers,
    build_cyt_mcp_mcp_server_entry,
    dev_invocation_from_hooks_file,
    is_cyt_dev_hook_command,
    is_cyt_mcp_frontend_server,
)


def test_build_installed_cyt_mcp_entry() -> None:
    entry = build_cyt_mcp_mcp_server_entry("cursor")
    assert entry == {
        "command": resolve_hook_executable("cyt-mcp"),
        "args": ["--agent", "cursor", "--workspace", "${workspaceFolder}"],
        "env": {"CYT_WORKSPACE": "${workspaceFolder}"},
        "cwd": "${workspaceFolder}",
    }


def test_build_dev_cyt_mcp_entry() -> None:
    repo_root = Path("/tmp/clear-your-tools")
    script_rel = cyt_mcp_cli_script_relpath()
    entry = build_cyt_mcp_mcp_server_entry(
        "cursor",
        dev_repo_root=repo_root,
        dev_script_rel=script_rel,
    )
    if sys.platform == "win32":
        assert entry["command"] == str(cyt_mcp_dev_wrapper_path("cursor"))
        assert entry["args"] == [
            "--agent",
            "cursor",
            "--workspace",
            "${workspaceFolder}",
        ]
    else:
        assert entry["command"] == resolve_hook_executable("uv")
        assert entry["args"] == [
            "run",
            "--directory",
            str(repo_root),
            script_rel,
            "--agent",
            "cursor",
            "--workspace",
            "${workspaceFolder}",
        ]
    assert entry["env"] == {"CYT_WORKSPACE": "${workspaceFolder}"}
    assert entry["cwd"] == "${workspaceFolder}"


def test_build_dev_workspace_cyt_mcp_entry_uses_env_and_relative_config() -> None:
    repo_root = Path("/tmp/clear-your-tools")
    script_rel = cyt_mcp_cli_script_relpath()
    entry = build_cyt_mcp_mcp_server_entry(
        "cursor",
        dev_repo_root=repo_root,
        dev_script_rel=script_rel,
        aggregator_config=".agents/cyt/config/mcp-config.yaml",
    )
    assert entry["env"] == {"CYT_WORKSPACE": "${workspaceFolder}"}
    assert entry["cwd"] == "${workspaceFolder}"
    assert entry["args"][-4:] == [
        "--config",
        ".agents/cyt/config/mcp-config.yaml",
        "--workspace",
        "${workspaceFolder}",
    ]
    if sys.platform != "win32":
        assert entry["args"][1:3] == ["--directory", str(repo_root)]


def test_cyt_mcp_mcp_server_entry_uses_dev_invocation() -> None:
    repo_root = Path("/tmp/clear-your-tools")
    invocation = HookCliInvocation(mode="dev", repo_root=repo_root)
    entry = cyt_mcp_mcp_server_entry("cursor", invocation=invocation)
    if sys.platform == "win32":
        assert entry["command"].replace("\\", "/").endswith("cyt/mcp-dev.cmd")
    else:
        assert entry["command"] == resolve_hook_executable("uv")
        assert entry["args"][0:3] == ["run", "--directory", str(repo_root)]
    assert entry["args"][-4:] == [
        "--agent",
        "cursor",
        "--workspace",
        "${workspaceFolder}",
    ]
    assert entry["cwd"] == "${workspaceFolder}"


def test_repo_root_from_uv_run_hook_command() -> None:
    command = "uv run --directory /tmp/clear-your-tools src/cyt_client/cli.py"
    assert repo_root_from_uv_run_hook_command(command) == Path("/tmp/clear-your-tools")


def test_is_cyt_dev_hook_command() -> None:
    assert is_cyt_dev_hook_command(
        "uv run --directory /tmp/repo src/cyt_client/cli.py",
    )
    assert is_cyt_dev_hook_command(
        "CYT_LAUNCH_AGENT=cursor uv run --directory /tmp/repo "
        "src/cyt/cli/app.py hook daemon start --unattended",
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
    entry = payload["mcpServers"][CYT_MCP_USER_SERVER_KEY]
    if sys.platform == "win32":
        assert entry["command"].replace("\\", "/").endswith("cyt/mcp-dev.cmd")
    else:
        assert entry["command"] == resolve_hook_executable("uv")
        assert entry["args"][1:3] == ["--directory", str(repo_root)]
    assert payload["mcpServers"]["other"]["command"] == "echo"
    assert CYT_MCP_SERVER_KEY not in payload["mcpServers"]


def test_prompt_cyt_mcp_transport_defaults_to_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cyt_mcp_setup, "_prompt", lambda _label, default: default)
    assert cyt_mcp_setup.prompt_cyt_mcp_transport() == "stdio"


def test_write_agent_cyt_mcp_entry_http_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp_path = tmp_path / "mcp.json"
    monkeypatch.setitem(cyt_mcp_setup._AGENT_SOURCE_PATHS, "cursor", mcp_path)
    cyt_mcp_setup.write_agent_cyt_mcp_entry("cursor", transport="http")
    payload = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert payload["mcpServers"][CYT_MCP_USER_SERVER_KEY] == {"url": "http://127.0.0.1:8765/mcp"}


def test_cyt_mcp_hook_settings_overlay() -> None:
    overlay = cyt_mcp_setup.cyt_mcp_hook_settings_overlay(transport="http", agent="cursor")
    assert overlay == {"agent": "cursor"}


def test_is_cyt_mcp_frontend_server() -> None:
    prod = {"command": "cyt-mcp", "args": ["--agent", "cursor"]}
    dev = {
        "command": "uv",
        "args": ["run", "--directory", "/tmp/repo", "src/cyt_mcp/cli.py", "--agent", "cursor"],
    }
    backend = {"command": "npx", "args": ["-y", "some-mcp-server"]}
    assert is_cyt_mcp_frontend_server("cyt-mcp-usr", prod)
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
    aggregator_path = tmp_path / "mcp-config.yaml"
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
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_CONFIG_PATH", aggregator_path)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_AGGREGATOR_PATH", aggregator_path)
    invocation = HookCliInvocation(mode="dev", repo_root=repo_root)
    cyt_mcp_setup.setup_cyt_mcp_for_agent("cursor", invocation=invocation, transport="stdio")
    agent_payload = json.loads(source.read_text(encoding="utf-8"))
    assert set(agent_payload["mcpServers"]) == {CYT_MCP_USER_SERVER_KEY}
    user_command = agent_payload["mcpServers"][CYT_MCP_USER_SERVER_KEY]["command"]
    if sys.platform == "win32":
        assert str(user_command).replace("\\", "/").endswith("cyt/mcp-dev.cmd")
    else:
        assert user_command == resolve_hook_executable("uv")
    backend_payload = json.loads((target_dir / "cursor.json").read_text(encoding="utf-8"))
    assert "codebase-memory-mcp" in backend_payload["mcpServers"]
    assert CYT_MCP_SERVER_KEY not in backend_payload["mcpServers"]
    assert LEGACY_CYT_MCP_SERVER_KEY not in backend_payload["mcpServers"]


def test_write_mcp_aggregator_yaml_workspace_uses_relative_backend_path(
    tmp_path: Path,
) -> None:
    aggregator_path = tmp_path / ".agents" / "cyt" / "config" / "mcp-config.yaml"
    backends_path = tmp_path / ".agents" / "cyt" / "config" / "mcp" / "cursor.json"
    backends_path.parent.mkdir(parents=True, exist_ok=True)
    backends_path.write_text('{"mcpServers": {}}', encoding="utf-8")

    cyt_mcp_setup.write_mcp_aggregator_yaml_at(
        aggregator_path,
        "cursor",
        backends_path=backends_path,
        workspace_scoped=True,
    )

    text = aggregator_path.read_text(encoding="utf-8")
    assert "cursor: mcp/cursor.json" in text


def test_write_mcp_aggregator_yaml_writes_explicit_verify_only_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregator_path = tmp_path / "mcp-config.yaml"
    mcp_dir = tmp_path / "mcp"
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_CONFIG_PATH", aggregator_path)
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
    aggregator_path = tmp_path / "mcp-config.yaml"
    mcp_dir = tmp_path / "mcp"
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_CONFIG_PATH", aggregator_path)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_AGGREGATOR_PATH", aggregator_path)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_DIR", mcp_dir)

    cyt_mcp_setup.write_mcp_aggregator_yaml("cursor", verify_only=True)

    assert "verify_only: true" in aggregator_path.read_text(encoding="utf-8")


def test_autouse_isolates_agent_mcp_paths_from_real_home(tmp_path: Path) -> None:
    from cyt.hook.install_scope import CytInstallScope

    scope = CytInstallScope(workspace_root=None)
    agent_mcp = scope.global_agent_mcp_path("cursor")
    real_home_mcp = Path("~/.cursor/mcp.json").expanduser()
    assert agent_mcp.resolve() != real_home_mcp.resolve()
    assert agent_mcp.resolve().is_relative_to(tmp_path.resolve())


def test_restore_user_cyt_mcp_merges_backends_and_removes_cyt_mcp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt.hook.install_scope import CytInstallScope

    home = tmp_path / "home"
    home.mkdir()
    user_mcp = home / ".cursor" / "mcp.json"
    user_mcp.parent.mkdir(parents=True)
    user_mcp.write_text(
        json.dumps({"mcpServers": {CYT_MCP_SERVER_KEY: {"command": "cyt-mcp"}}}),
        encoding="utf-8",
    )
    defs_path = home / "cyt" / "mcp" / "cursor.json"
    defs_path.parent.mkdir(parents=True)
    defs_path.write_text(
        json.dumps({"mcpServers": {"backend-a": {"command": "echo", "args": ["a"]}}}),
        encoding="utf-8",
    )

    import cyt.hook.install_scope as install_scope

    monkeypatch.setattr(install_scope, "GLOBAL_MCP_DIR", home / "cyt" / "mcp")
    monkeypatch.setitem(
        install_scope.GLOBAL_AGENT_MCP_PATHS,
        "cursor",
        Path(str(user_mcp)),
    )
    scope = CytInstallScope(workspace_root=None)

    changed = cyt_mcp_setup.restore_user_cyt_mcp_for_agent("cursor", scope)
    assert changed is True
    payload = json.loads(user_mcp.read_text(encoding="utf-8"))
    assert CYT_MCP_SERVER_KEY not in payload["mcpServers"]
    assert payload["mcpServers"]["backend-a"]["command"] == "echo"
    assert defs_path.is_file()


def test_restore_workspace_cyt_mcp_merges_backends(tmp_path: Path) -> None:
    from cyt.hook.install_scope import CytInstallScope

    scope = CytInstallScope(workspace_root=tmp_path)
    defs_path = tmp_path / ".agents" / "cyt" / "config" / "mcp" / "cursor.json"
    defs_path.parent.mkdir(parents=True)
    defs_path.write_text(
        json.dumps({"mcpServers": {"backend-b": {"command": "echo", "args": ["b"]}}}),
        encoding="utf-8",
    )
    project_mcp = tmp_path / ".cursor" / "mcp.json"
    project_mcp.parent.mkdir(parents=True)
    project_mcp.write_text(
        json.dumps({"mcpServers": {CYT_MCP_SERVER_KEY: {"command": "cyt-mcp"}}}),
        encoding="utf-8",
    )

    changed = cyt_mcp_setup.restore_workspace_cyt_mcp_for_agent("cursor", scope)
    assert changed is True
    payload = json.loads(project_mcp.read_text(encoding="utf-8"))
    assert CYT_MCP_SERVER_KEY not in payload["mcpServers"]
    assert payload["mcpServers"]["backend-b"]["args"] == ["b"]


def test_restore_empty_defs_only_removes_cyt_mcp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt.hook.install_scope import CytInstallScope

    home = tmp_path / "home"
    user_mcp = home / ".cursor" / "mcp.json"
    user_mcp.parent.mkdir(parents=True)
    user_mcp.write_text(
        json.dumps({"mcpServers": {CYT_MCP_SERVER_KEY: {"command": "cyt-mcp"}}}),
        encoding="utf-8",
    )

    import cyt.hook.install_scope as install_scope

    monkeypatch.setattr(install_scope, "GLOBAL_MCP_DIR", home / "cyt" / "mcp")
    monkeypatch.setitem(
        install_scope.GLOBAL_AGENT_MCP_PATHS,
        "cursor",
        Path(str(user_mcp)),
    )
    scope = CytInstallScope(workspace_root=None)

    changed = cyt_mcp_setup.restore_user_cyt_mcp_for_agent("cursor", scope)
    assert changed is True
    payload = json.loads(user_mcp.read_text(encoding="utf-8"))
    assert CYT_MCP_SERVER_KEY not in payload["mcpServers"]
    assert payload["mcpServers"] == {}


def test_has_migratable_mcp_backends_false_when_all_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt.hook.install_scope import CytInstallScope

    home = tmp_path / "home"
    home.mkdir()
    user_mcp = home / ".cursor" / "mcp.json"
    user_mcp.parent.mkdir(parents=True)
    user_mcp.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    import cyt.hook.install_scope as install_scope

    monkeypatch.setattr(install_scope, "GLOBAL_MCP_DIR", home / "cyt" / "mcp")
    monkeypatch.setitem(
        install_scope.GLOBAL_AGENT_MCP_PATHS,
        "cursor",
        Path(str(user_mcp)),
    )
    scope = CytInstallScope(workspace_root=tmp_path)
    (tmp_path / ".git").mkdir()
    project_mcp = tmp_path / ".cursor" / "mcp.json"
    project_mcp.parent.mkdir(parents=True)
    project_mcp.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    assert cyt_mcp_setup.has_migratable_mcp_backends("cursor", scope) is False


def test_has_migratable_mcp_backends_true_from_codex_defs_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt.hook.install_scope import CytInstallScope

    home = tmp_path / "home"
    home.mkdir()
    defs_path = home / "cyt" / "mcp" / "codex.json"
    defs_path.parent.mkdir(parents=True)
    defs_path.write_text(
        json.dumps({"mcpServers": {"my-server": {"command": "npx", "args": ["-y", "server"]}}}),
        encoding="utf-8",
    )
    codex_config = home / ".codex" / "config.toml"
    codex_config.parent.mkdir(parents=True)
    codex_config.write_text("", encoding="utf-8")

    import cyt.hook.install_scope as install_scope

    monkeypatch.setattr(install_scope, "GLOBAL_MCP_DIR", home / "cyt" / "mcp")
    monkeypatch.setitem(
        install_scope.GLOBAL_AGENT_MCP_PATHS,
        "codex",
        Path(str(codex_config)),
    )
    scope = CytInstallScope(workspace_root=None)

    assert cyt_mcp_setup.has_migratable_mcp_backends("codex", scope) is True


def test_has_migratable_mcp_backends_true_from_agent_native_before_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt.hook.install_scope import CytInstallScope

    home = tmp_path / "home"
    home.mkdir()
    user_mcp = home / ".cursor" / "mcp.json"
    user_mcp.parent.mkdir(parents=True)
    user_mcp.write_text(
        json.dumps({"mcpServers": {"backend-a": {"command": "echo"}}}),
        encoding="utf-8",
    )

    import cyt.hook.install_scope as install_scope

    monkeypatch.setattr(install_scope, "GLOBAL_MCP_DIR", home / "cyt" / "mcp")
    monkeypatch.setitem(
        install_scope.GLOBAL_AGENT_MCP_PATHS,
        "cursor",
        Path(str(user_mcp)),
    )
    scope = CytInstallScope(workspace_root=None)

    assert cyt_mcp_setup.has_migratable_mcp_backends("cursor", scope) is True


def test_has_migratable_mcp_backends_split_by_user_and_workspace_layers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt.hook.install_scope import CytInstallScope

    home = tmp_path / "home"
    home.mkdir()
    user_mcp = home / ".cursor" / "mcp.json"
    user_mcp.parent.mkdir(parents=True)
    user_mcp.write_text(
        json.dumps({"mcpServers": {"backend-a": {"command": "echo"}}}),
        encoding="utf-8",
    )
    (tmp_path / ".git").mkdir()
    project_mcp = tmp_path / ".cursor" / "mcp.json"
    project_mcp.parent.mkdir(parents=True)
    project_mcp.write_text(
        json.dumps({"mcpServers": {"backend-b": {"command": "echo"}}}),
        encoding="utf-8",
    )

    import cyt.hook.install_scope as install_scope

    monkeypatch.setattr(install_scope, "GLOBAL_MCP_DIR", home / "cyt" / "mcp")
    monkeypatch.setitem(
        install_scope.GLOBAL_AGENT_MCP_PATHS,
        "cursor",
        Path(str(user_mcp)),
    )
    scope = CytInstallScope(workspace_root=tmp_path.resolve())

    assert cyt_mcp_setup.has_migratable_user_mcp_backends("cursor", scope) is True
    assert cyt_mcp_setup.has_migratable_workspace_mcp_backends("cursor", scope) is True
    assert cyt_mcp_setup.has_migratable_mcp_backends("cursor", scope) is True


def test_restore_codex_mcp_from_toml_defs(tmp_path: Path) -> None:
    defs_path = tmp_path / "codex-backends.json"
    defs_path.write_text(
        json.dumps({"mcpServers": {"my-server": {"command": "npx", "args": ["-y", "server"]}}}),
        encoding="utf-8",
    )
    codex_config = tmp_path / "config.toml"
    codex_config.write_text(
        '\n[mcp_servers.cyt-mcp]\ncommand = "cyt-mcp"\nargs = ["--agent", "codex"]\n',
        encoding="utf-8",
    )

    changed = cyt_mcp_setup.restore_agent_mcp_backends_from(
        defs_path,
        codex_config,
        agent="codex",
    )
    assert changed is True
    text = codex_config.read_text(encoding="utf-8")
    assert "[mcp_servers.cyt-mcp]" not in text
    assert "[mcp_servers.my-server]" in text
    assert 'command = "npx"' in text


def test_setup_cyt_mcp_for_agent_skips_when_no_backends(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt.hook.install_scope import CytInstallScope

    home = tmp_path / "home"
    home.mkdir()
    user_mcp = home / ".cursor" / "mcp.json"
    user_mcp.parent.mkdir(parents=True)
    user_mcp.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    aggregator_path = home / "cyt" / "mcp-config.yaml"

    import cyt.hook.install_scope as install_scope

    monkeypatch.setattr(install_scope, "GLOBAL_MCP_DIR", home / "cyt" / "mcp")
    monkeypatch.setitem(
        install_scope.GLOBAL_AGENT_MCP_PATHS,
        "cursor",
        Path(str(user_mcp)),
    )
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_CONFIG_PATH", aggregator_path)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_AGGREGATOR_PATH", aggregator_path)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_DIR", home / "cyt" / "mcp")
    monkeypatch.setattr(
        install_scope.CytInstallScope,
        "from_cwd",
        classmethod(lambda cls, *, cwd=None: CytInstallScope(workspace_root=None)),
    )

    cyt_mcp_setup.setup_cyt_mcp_for_agent("cursor", transport="stdio")

    assert not aggregator_path.is_file()
    assert json.loads(user_mcp.read_text(encoding="utf-8"))["mcpServers"] == {}
