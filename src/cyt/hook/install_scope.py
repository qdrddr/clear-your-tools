"""Path helpers for Global User vs workspace-scoped CYT install."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

HookAgentName = Literal["claude", "codex", "cursor"]

GLOBAL_CONFIG_PATH = Path("~/.config/cyt/config.yaml")
GLOBAL_AGGREGATOR_PATH = Path("~/.config/cyt/mcp-aggregator.yaml")
GLOBAL_MCP_DIR = Path("~/.config/cyt/mcp")

GLOBAL_HOOKS_PATHS: dict[HookAgentName, Path] = {
    "cursor": Path("~/.cursor/hooks.json"),
    "claude": Path("~/.claude/settings.json"),
    "codex": Path("~/.codex/hooks.json"),
}

GLOBAL_AGENT_MCP_PATHS: dict[str, Path] = {
    "cursor": Path("~/.cursor/mcp.json"),
    "claude": Path("~/.claude.json"),
    "codex": Path("~/.codex/config.toml"),
}

# Shared workspace CYT directory for cross-agent policy (permissions, etc.).
WORKSPACE_ALL_AGENTS_CYT_DIR = ".agents/cyt"

# Agent-native directory name under workspace root for CYT artifacts.
_AGENT_CYT_DIRS: dict[str, str] = {
    "cursor": ".cursor",
    "claude": ".claude",
    "codex": ".codex",
}

# Subdirectories under ``.<agent>/cyt/`` for workspace-scoped artifacts.
WORKSPACE_CYT_CONFIG_SUBDIR = "config"
WORKSPACE_CYT_MCP_SUBDIR = "mcp"
WORKSPACE_AGENT_MCP_PATHS: dict[str, str] = {
    "cursor": ".cursor/mcp.json",
    "claude": ".mcp.json",
    "codex": ".codex/config.toml",
}


def _expand(path: Path) -> Path:
    return path.expanduser()


def detect_workspace_root(*, cwd: Path | None = None) -> Path | None:
    """Return workspace root when cwd looks like a project repo."""
    root = (cwd or Path.cwd()).resolve()
    markers = (
        root / ".git",
        root / ".cursor",
        root / ".claude",
        root / ".codex",
        root / ".agents",
    )
    if any(marker.exists() for marker in markers):
        return root
    return None


@dataclass(frozen=True)
class CytInstallScope:
    workspace_root: Path | None

    @classmethod
    def from_cwd(cls, *, cwd: Path | None = None) -> CytInstallScope:
        return cls(workspace_root=detect_workspace_root(cwd=cwd))

    @property
    def has_workspace(self) -> bool:
        return self.workspace_root is not None

    def global_hooks_path(self, agent: HookAgentName) -> Path:
        return _expand(GLOBAL_HOOKS_PATHS[agent])

    def global_agent_mcp_path(self, agent: str) -> Path:
        return _expand(GLOBAL_AGENT_MCP_PATHS.get(agent, GLOBAL_AGENT_MCP_PATHS["cursor"]))

    def global_server_defs_path(self, agent: str) -> Path:
        name = agent.strip() or "cursor"
        return _expand(GLOBAL_MCP_DIR / f"{name}.json")

    def global_cyt_config_path(self) -> Path:
        return _expand(GLOBAL_CONFIG_PATH)

    def global_aggregator_path(self) -> Path:
        return _expand(GLOBAL_AGGREGATOR_PATH)

    def workspace_all_agents_cyt_dir(self) -> Path | None:
        if self.workspace_root is None:
            return None
        return self.workspace_root / WORKSPACE_ALL_AGENTS_CYT_DIR

    def workspace_all_agents_cyt_config_path(self) -> Path | None:
        cyt_dir = self.workspace_all_agents_cyt_dir()
        if cyt_dir is None:
            return None
        return cyt_dir / WORKSPACE_CYT_CONFIG_SUBDIR / "config.yaml"

    def resolve_workspace_all_agents_cyt_config_path(self) -> Path | None:
        path = self.workspace_all_agents_cyt_config_path()
        if path is None or not path.is_file():
            return None
        return path

    def _legacy_workspace_cyt_config_paths(self, agent: str) -> tuple[Path, ...]:
        cyt_dir = self._agent_cyt_dir(agent)
        if cyt_dir is None:
            return ()
        return (
            cyt_dir / WORKSPACE_CYT_CONFIG_SUBDIR / "config.yaml",
            cyt_dir / "config.yaml",
        )

    def legacy_workspace_cyt_config_path(self, agent: str) -> Path | None:
        """Return a legacy per-agent workspace config file when present."""
        for path in self._legacy_workspace_cyt_config_paths(agent):
            if path.is_file():
                return path
        return None

    def _agent_cyt_dir(self, agent: str) -> Path | None:
        if self.workspace_root is None:
            return None
        rel = _AGENT_CYT_DIRS.get(agent.strip() or "cursor", ".cursor")
        return self.workspace_root / rel / "cyt"

    def workspace_agent_mcp_path(self, agent: str) -> Path | None:
        if self.workspace_root is None:
            return None
        rel = WORKSPACE_AGENT_MCP_PATHS.get(agent.strip() or "cursor", ".cursor/mcp.json")
        return self.workspace_root / rel

    def workspace_server_defs_path(self, agent: str) -> Path | None:
        cyt_dir = self._agent_cyt_dir(agent)
        if cyt_dir is None:
            return None
        name = agent.strip() or "cursor"
        return cyt_dir / WORKSPACE_CYT_MCP_SUBDIR / f"{name}.json"

    def workspace_cyt_config_path(self, agent: str) -> Path | None:
        """Canonical workspace CYT config path (shared across agents)."""
        return self.workspace_all_agents_cyt_config_path()

    def resolve_workspace_cyt_config_path(self, agent: str) -> Path | None:
        """Resolve workspace config, preferring ``.agents/cyt/config/config.yaml``."""
        shared = self.resolve_workspace_all_agents_cyt_config_path()
        if shared is not None:
            return shared
        return self.legacy_workspace_cyt_config_path(agent)

    def workspace_aggregator_path(self, agent: str) -> Path | None:
        cyt_dir = self._agent_cyt_dir(agent)
        if cyt_dir is None:
            return None
        return cyt_dir / WORKSPACE_CYT_CONFIG_SUBDIR / "mcp-aggregator.yaml"

    def resolve_workspace_aggregator_path(self, agent: str) -> Path | None:
        cyt_dir = self._agent_cyt_dir(agent)
        if cyt_dir is None:
            return None
        for path in (
            cyt_dir / WORKSPACE_CYT_CONFIG_SUBDIR / "mcp-aggregator.yaml",
            cyt_dir / "mcp-aggregator.yaml",
        ):
            if path.is_file():
                return path
        return None

    def workspace_cyt_dir(self, agent: str) -> Path | None:
        return self._agent_cyt_dir(agent)
