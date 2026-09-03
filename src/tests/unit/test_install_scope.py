"""Tests for dual-scope CYT install path helpers and wizard behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cyt.hook.install_scope import CytInstallScope, detect_workspace_root
from cyt.tools import cyt_mcp_setup
from cyt_client.mcp_entry import CYT_MCP_SERVER_KEY, CYT_MCP_WORKSPACE_SERVER_KEY


def test_detect_workspace_root_from_git(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    assert detect_workspace_root(cwd=tmp_path) == tmp_path.resolve()


def test_cyt_install_scope_workspace_paths(tmp_path: Path) -> None:
    scope = CytInstallScope(workspace_root=tmp_path)
    assert scope.workspace_server_defs_path("cursor") == tmp_path / ".cursor/cyt/mcp/cursor.json"
    assert scope.workspace_cyt_config_path("cursor") == tmp_path / ".cursor/cyt/config/config.yaml"
    assert (
        scope.workspace_aggregator_path("cursor")
        == tmp_path / ".cursor/cyt/config/mcp-aggregator.yaml"
    )
    assert scope.global_hooks_path("cursor") == Path("~/.cursor/hooks.json").expanduser()


def test_setup_cyt_mcp_global_only_outside_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    global_mcp = home / ".cursor" / "mcp.json"
    global_mcp.parent.mkdir(parents=True)
    global_mcp.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    monkeypatch.setattr(
        cyt_mcp_setup,
        "DEFAULT_AGGREGATOR_PATH",
        home / "cyt" / "mcp-aggregator.yaml",
    )
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_DIR", home / "cyt" / "mcp")
    import cyt.hook.install_scope as install_scope

    monkeypatch.setattr(
        install_scope,
        "GLOBAL_AGGREGATOR_PATH",
        home / "cyt" / "mcp-aggregator.yaml",
    )
    monkeypatch.setattr(install_scope, "GLOBAL_MCP_DIR", home / "cyt" / "mcp")
    monkeypatch.setitem(
        install_scope.GLOBAL_AGENT_MCP_PATHS,
        "cursor",
        Path(str(home / ".cursor" / "mcp.json")),
    )
    monkeypatch.setattr(
        install_scope.CytInstallScope,
        "from_cwd",
        classmethod(lambda cls, *, cwd=None: CytInstallScope(workspace_root=None)),
    )

    cyt_mcp_setup.setup_cyt_mcp_for_agent("cursor", transport="stdio", migrate_backends=False)

    assert not (tmp_path / ".cursor" / "cyt").exists()
    user_mcp = json.loads(global_mcp.read_text(encoding="utf-8"))
    assert CYT_MCP_SERVER_KEY in user_mcp["mcpServers"]
    assert "--config" in user_mcp["mcpServers"][CYT_MCP_SERVER_KEY]["args"]


def test_setup_cyt_mcp_writes_global_and_workspace_layers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".git").mkdir()
    home = tmp_path / "home"
    home.mkdir()
    global_mcp = home / ".cursor" / "mcp.json"
    global_mcp.parent.mkdir(parents=True)
    global_mcp.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "backend-a": {"command": "echo", "args": ["a"]},
                    CYT_MCP_SERVER_KEY: {"command": "old"},
                },
            },
        ),
        encoding="utf-8",
    )
    project_mcp = tmp_path / ".cursor" / "mcp.json"
    project_mcp.parent.mkdir(parents=True)
    project_mcp.write_text(
        json.dumps({"mcpServers": {"backend-b": {"command": "echo", "args": ["b"]}}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        cyt_mcp_setup,
        "DEFAULT_AGGREGATOR_PATH",
        home / "cyt" / "mcp-aggregator.yaml",
    )
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_DIR", home / "cyt" / "mcp")
    import cyt.hook.install_scope as install_scope

    monkeypatch.setattr(
        install_scope,
        "GLOBAL_AGGREGATOR_PATH",
        home / "cyt" / "mcp-aggregator.yaml",
    )
    monkeypatch.setattr(install_scope, "GLOBAL_MCP_DIR", home / "cyt" / "mcp")
    monkeypatch.setitem(
        install_scope.GLOBAL_AGENT_MCP_PATHS,
        "cursor",
        Path(str(home / ".cursor" / "mcp.json")),
    )
    monkeypatch.setattr(
        install_scope.CytInstallScope,
        "from_cwd",
        classmethod(lambda cls, *, cwd=None: CytInstallScope(workspace_root=tmp_path.resolve())),
    )
    monkeypatch.setattr(cyt_mcp_setup, "_prompt_yes_no", lambda *a, **k: True)

    cyt_mcp_setup.setup_cyt_mcp_for_agent(
        "cursor",
        transport="http",
        migrate_backends=True,
        configure_workspace=True,
    )

    global_defs = home / "cyt" / "mcp" / "cursor.json"
    assert global_defs.is_file()
    global_payload = json.loads(global_defs.read_text(encoding="utf-8"))
    assert "backend-a" in global_payload["mcpServers"]
    assert CYT_MCP_SERVER_KEY not in global_payload["mcpServers"]

    workspace_defs = tmp_path / ".cursor" / "cyt" / "mcp" / "cursor.json"
    assert workspace_defs.is_file()
    workspace_payload = json.loads(workspace_defs.read_text(encoding="utf-8"))
    assert "backend-b" in workspace_payload["mcpServers"]

    user_mcp = json.loads(global_mcp.read_text(encoding="utf-8"))
    project_mcp_data = json.loads(project_mcp.read_text(encoding="utf-8"))
    assert CYT_MCP_SERVER_KEY in user_mcp["mcpServers"]
    assert "url" in user_mcp["mcpServers"][CYT_MCP_SERVER_KEY]
    assert CYT_MCP_WORKSPACE_SERVER_KEY in project_mcp_data["mcpServers"]
    assert "url" in project_mcp_data["mcpServers"][CYT_MCP_WORKSPACE_SERVER_KEY]


def test_setup_cyt_mcp_configures_workspace_with_stdio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".git").mkdir()
    home = tmp_path / "home"
    home.mkdir()
    global_mcp = home / ".cursor" / "mcp.json"
    global_mcp.parent.mkdir(parents=True)
    global_mcp.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    project_mcp = tmp_path / ".cursor" / "mcp.json"
    project_mcp.parent.mkdir(parents=True)
    project_mcp.write_text(
        json.dumps({"mcpServers": {"backend-b": {"command": "echo", "args": ["b"]}}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        cyt_mcp_setup,
        "DEFAULT_AGGREGATOR_PATH",
        home / "cyt" / "mcp-aggregator.yaml",
    )
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_DIR", home / "cyt" / "mcp")
    import cyt.hook.install_scope as install_scope

    monkeypatch.setattr(
        install_scope,
        "GLOBAL_AGGREGATOR_PATH",
        home / "cyt" / "mcp-aggregator.yaml",
    )
    monkeypatch.setattr(install_scope, "GLOBAL_MCP_DIR", home / "cyt" / "mcp")
    monkeypatch.setitem(
        install_scope.GLOBAL_AGENT_MCP_PATHS,
        "cursor",
        Path(str(home / ".cursor" / "mcp.json")),
    )
    monkeypatch.setattr(
        install_scope.CytInstallScope,
        "from_cwd",
        classmethod(lambda cls, *, cwd=None: CytInstallScope(workspace_root=tmp_path.resolve())),
    )

    cyt_mcp_setup.setup_cyt_mcp_for_agent(
        "cursor",
        transport="stdio",
        migrate_backends=True,
        configure_workspace=True,
    )

    assert (tmp_path / ".cursor" / "cyt" / "mcp" / "cursor.json").is_file()
    project_mcp_data = json.loads(project_mcp.read_text(encoding="utf-8"))
    assert CYT_MCP_WORKSPACE_SERVER_KEY in project_mcp_data.get("mcpServers", {})


def test_setup_cyt_mcp_verify_only_writes_workspace_layer_with_stdio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".git").mkdir()
    home = tmp_path / "home"
    home.mkdir()
    global_mcp = home / ".cursor" / "mcp.json"
    global_mcp.parent.mkdir(parents=True)
    global_mcp.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    project_mcp = tmp_path / ".cursor" / "mcp.json"
    project_mcp.parent.mkdir(parents=True)
    project_mcp.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    monkeypatch.setattr(
        cyt_mcp_setup,
        "DEFAULT_AGGREGATOR_PATH",
        home / "cyt" / "mcp-aggregator.yaml",
    )
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_DIR", home / "cyt" / "mcp")
    import cyt.hook.install_scope as install_scope

    monkeypatch.setattr(
        install_scope,
        "GLOBAL_AGGREGATOR_PATH",
        home / "cyt" / "mcp-aggregator.yaml",
    )
    monkeypatch.setattr(install_scope, "GLOBAL_MCP_DIR", home / "cyt" / "mcp")
    monkeypatch.setitem(
        install_scope.GLOBAL_AGENT_MCP_PATHS,
        "cursor",
        Path(str(home / ".cursor" / "mcp.json")),
    )
    monkeypatch.setattr(
        install_scope.CytInstallScope,
        "from_cwd",
        classmethod(lambda cls, *, cwd=None: CytInstallScope(workspace_root=tmp_path.resolve())),
    )

    cyt_mcp_setup.setup_cyt_mcp_for_agent(
        "cursor",
        transport="stdio",
        migrate_backends=False,
        configure_workspace=True,
        verify_only=True,
    )

    workspace_agg = tmp_path / ".cursor" / "cyt" / "config" / "mcp-aggregator.yaml"
    assert workspace_agg.is_file()
    workspace_text = workspace_agg.read_text(encoding="utf-8")
    assert "verify_only: true" in workspace_text
    assert "catalog_scope: workspace" in workspace_text
    global_agg = home / "cyt" / "mcp-aggregator.yaml"
    assert "verify_only: true" in global_agg.read_text(encoding="utf-8")
    project_mcp_data = json.loads(project_mcp.read_text(encoding="utf-8"))
    assert CYT_MCP_WORKSPACE_SERVER_KEY in project_mcp_data.get("mcpServers", {})


def test_remove_workspace_cyt_mcp_for_agent(tmp_path: Path) -> None:
    scope = CytInstallScope(workspace_root=tmp_path)
    cyt_root = tmp_path / ".cursor" / "cyt"
    mcp_dir = cyt_root / "mcp"
    mcp_dir.mkdir(parents=True)
    (mcp_dir / "cursor.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    project_mcp = tmp_path / ".cursor" / "mcp.json"
    project_mcp.parent.mkdir(parents=True, exist_ok=True)
    project_mcp.write_text(
        json.dumps({"mcpServers": {CYT_MCP_WORKSPACE_SERVER_KEY: {"command": "cyt-mcp"}}}),
        encoding="utf-8",
    )

    changed = cyt_mcp_setup.remove_workspace_cyt_mcp_for_agent("cursor", scope)
    assert changed is True
    assert not cyt_root.exists()
    payload = json.loads(project_mcp.read_text(encoding="utf-8"))
    assert CYT_MCP_WORKSPACE_SERVER_KEY not in payload["mcpServers"]
