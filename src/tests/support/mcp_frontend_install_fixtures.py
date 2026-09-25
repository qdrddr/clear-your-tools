"""Fixtures and helpers for cyt-mcp frontend install gate matrix tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pytest

import cyt.hook.install_scope as install_scope
from cyt.hook.install_scope import (
    GLOBAL_AGENT_MCP_PATHS,
    WORKSPACE_AGENT_MCP_PATHS,
    CytInstallScope,
)
from cyt.tools import cyt_mcp_setup
from cyt_client.mcp_entry import (
    CYT_MCP_FRONTEND_SERVER_KEYS,
    CYT_MCP_USER_SERVER_KEY,
    CYT_MCP_WORKSPACE_SERVER_KEY,
    build_cyt_mcp_mcp_server_entry,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_migration"
FRONTEND_INSTALL_MATRIX_PATH = FIXTURES_DIR / "frontend_install_matrix.json"

AgentMcpState = Literal["missing", "empty", "frontend_only", "has_backend"]
DefsState = Literal["missing", "empty", "has_backend"]
InstallScopeKind = Literal["user", "workspace"]


@dataclass(frozen=True)
class FrontendInstallMatrixCase:
    id: str
    scope: InstallScopeKind
    agent: str
    agent_mcp_state: AgentMcpState
    defs_state: DefsState
    expect_should_install: bool
    expect_backend_in_defs: bool
    expect_agent_frontend_only_after: bool
    expect_frontend_unchanged: bool
    backend_server: str
    backend_spec: dict[str, Any]


def load_frontend_install_matrix() -> list[FrontendInstallMatrixCase]:
    payload = json.loads(FRONTEND_INSTALL_MATRIX_PATH.read_text(encoding="utf-8"))
    agents = payload["agents"]
    scopes = payload["scopes"]
    backend_server = str(payload["backend_server"])
    backend_spec = payload["backend_spec"]
    templates = payload["case_templates"]
    cases: list[FrontendInstallMatrixCase] = []
    for scope in scopes:
        for agent in agents:
            for template in templates:
                cases.append(
                    FrontendInstallMatrixCase(
                        id=f"{scope}_{agent}_{template['id_suffix']}",
                        scope=scope,
                        agent=str(agent),
                        agent_mcp_state=template["agent_mcp_state"],
                        defs_state=template["defs_state"],
                        expect_should_install=bool(template["expect_should_install"]),
                        expect_backend_in_defs=bool(template["expect_backend_in_defs"]),
                        expect_agent_frontend_only_after=bool(
                            template.get("expect_agent_frontend_only_after", False),
                        ),
                        expect_frontend_unchanged=bool(
                            template.get("expect_frontend_unchanged", False),
                        ),
                        backend_server=backend_server,
                        backend_spec=dict(backend_spec),
                    ),
                )
    return cases


def frontend_server_key(scope: InstallScopeKind) -> str:
    return CYT_MCP_USER_SERVER_KEY if scope == "user" else CYT_MCP_WORKSPACE_SERVER_KEY


def _user_home_agent_mcp_path(home: Path, agent: str) -> Path:
    rel = GLOBAL_AGENT_MCP_PATHS.get(agent, GLOBAL_AGENT_MCP_PATHS["cursor"])
    return home / str(rel).replace("~", "").lstrip("/")


def _workspace_agent_mcp_path(workspace_root: Path, agent: str) -> Path:
    rel = WORKSPACE_AGENT_MCP_PATHS.get(agent, WORKSPACE_AGENT_MCP_PATHS["cursor"])
    return workspace_root / rel


def _user_defs_path(home: Path, agent: str) -> Path:
    return home / "cyt" / "mcp" / f"{agent}.json"


def _workspace_defs_path(workspace_root: Path, agent: str) -> Path:
    return workspace_root / ".agents" / "cyt" / "config" / "mcp" / f"{agent}.json"


def _write_json_mcp(path: Path, servers: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}, indent=2) + "\n", encoding="utf-8")


def _write_codex_backend(path: Path, server_key: str, spec: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    command = str(spec.get("command", ""))
    args = spec.get("args", [])
    block = f'\n[mcp_servers.{server_key}]\ncommand = "{command}"\nargs = {json.dumps(args)}\n'
    path.write_text(block, encoding="utf-8")


def _write_agent_mcp_state(
    path: Path,
    *,
    agent: str,
    scope: InstallScopeKind,
    state: AgentMcpState,
    backend_server: str,
    backend_spec: dict[str, Any],
) -> None:
    if state == "missing":
        return
    frontend_key = frontend_server_key(scope)
    if agent == "codex":
        if state == "empty":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
            return
        if state == "frontend_only":
            entry = build_cyt_mcp_mcp_server_entry(agent)
            _write_codex_backend(path, frontend_key, entry)
            return
        _write_codex_backend(path, backend_server, backend_spec)
        return

    servers: dict[str, Any] = {}
    if state == "has_backend":
        servers[backend_server] = dict(backend_spec)
    elif state == "frontend_only":
        servers[frontend_key] = build_cyt_mcp_mcp_server_entry(agent)
    _write_json_mcp(path, servers)


def _write_defs_state(
    path: Path,
    *,
    agent: str,
    state: DefsState,
    backend_server: str,
    backend_spec: dict[str, Any],
) -> None:
    if state == "missing":
        return
    if state == "empty":
        _write_json_mcp(path, {})
        return
    _write_json_mcp(path, {backend_server: dict(backend_spec)})


@dataclass
class FrontendInstallTestbed:
    home: Path
    workspace_root: Path
    agent: str
    scope: InstallScopeKind
    agent_mcp_path: Path
    defs_path: Path
    aggregator_path: Path
    cyt_scope: CytInstallScope

    @classmethod
    def create(
        cls,
        tmp_path: Path,
        *,
        agent: str,
        scope: InstallScopeKind,
        monkeypatch: pytest.MonkeyPatch,
    ) -> FrontendInstallTestbed:
        home = tmp_path / "home"
        workspace_root = tmp_path / "workspace"
        home.mkdir()
        workspace_root.mkdir()
        (workspace_root / ".git").mkdir()

        user_agent_mcp = _user_home_agent_mcp_path(home, agent)
        workspace_agent_mcp = _workspace_agent_mcp_path(workspace_root, agent)
        user_defs = _user_defs_path(home, agent)
        workspace_defs = _workspace_defs_path(workspace_root, agent)

        if scope == "user":
            agent_mcp_path = user_agent_mcp
            defs_path = user_defs
            aggregator_path = home / "cyt" / "mcp-config.yaml"
            cyt_scope = CytInstallScope(workspace_root=None)
        else:
            agent_mcp_path = workspace_agent_mcp
            defs_path = workspace_defs
            aggregator_path = workspace_root / ".agents" / "cyt" / "config" / "mcp-config.yaml"
            cyt_scope = CytInstallScope(workspace_root=workspace_root.resolve())

        monkeypatch.setattr(install_scope, "GLOBAL_MCP_DIR", home / "cyt" / "mcp")
        monkeypatch.setattr(
            install_scope,
            "GLOBAL_MCP_CONFIG_PATH",
            home / "cyt" / "mcp-config.yaml",
        )
        monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_DIR", home / "cyt" / "mcp")
        monkeypatch.setattr(
            cyt_mcp_setup,
            "DEFAULT_MCP_CONFIG_PATH",
            home / "cyt" / "mcp-config.yaml",
        )
        monkeypatch.setattr(
            cyt_mcp_setup,
            "DEFAULT_AGGREGATOR_PATH",
            home / "cyt" / "mcp-config.yaml",
        )
        monkeypatch.setitem(install_scope.GLOBAL_AGENT_MCP_PATHS, agent, user_agent_mcp)
        monkeypatch.setattr(
            install_scope.CytInstallScope,
            "from_cwd",
            classmethod(lambda cls, *, cwd=None: cyt_scope),
        )

        return cls(
            home=home,
            workspace_root=workspace_root,
            agent=agent,
            scope=scope,
            agent_mcp_path=agent_mcp_path,
            defs_path=defs_path,
            aggregator_path=aggregator_path,
            cyt_scope=cyt_scope,
        )

    def apply_case(self, case: FrontendInstallMatrixCase) -> None:
        _write_agent_mcp_state(
            self.agent_mcp_path,
            agent=self.agent,
            scope=self.scope,
            state=case.agent_mcp_state,
            backend_server=case.backend_server,
            backend_spec=case.backend_spec,
        )
        _write_defs_state(
            self.defs_path,
            agent=self.agent,
            state=case.defs_state,
            backend_server=case.backend_server,
            backend_spec=case.backend_spec,
        )

    def run_setup(self) -> None:
        if self.scope == "user":
            cyt_mcp_setup.setup_cyt_mcp_user_for_agent(
                self.agent,
                transport="stdio",
                scope=self.cyt_scope,
            )
            return
        cyt_mcp_setup.setup_cyt_mcp_workspace_for_agent(
            self.agent,
            self.cyt_scope,
            transport="stdio",
        )


def agent_mcp_has_frontend(path: Path, *, agent: str, scope: InstallScopeKind) -> bool:
    key = frontend_server_key(scope)
    if not path.is_file():
        return False
    if agent == "codex":
        return f"[mcp_servers.{key}]" in path.read_text(encoding="utf-8")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    servers = payload.get("mcpServers")
    return isinstance(servers, dict) and key in servers


def agent_mcp_backend_keys(path: Path, *, agent: str) -> set[str]:
    if not path.is_file():
        return set()
    if agent == "codex":
        import re

        text = path.read_text(encoding="utf-8")
        keys = set(re.findall(r"^\[mcp_servers\.([^\]]+)\]", text, flags=re.MULTILINE))
        return {key for key in keys if key not in CYT_MCP_FRONTEND_SERVER_KEYS}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        return set()
    return {key for key in servers if key not in CYT_MCP_FRONTEND_SERVER_KEYS}


def defs_has_backend(path: Path, backend_server: str) -> bool:
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    servers = payload.get("mcpServers")
    return isinstance(servers, dict) and backend_server in servers
