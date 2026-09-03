"""Resolve workspace-scoped MCP server definitions for catalog merge."""

from __future__ import annotations

from pathlib import Path

_AGENT_CYT_DIRS: dict[str, str] = {
    "cursor": ".cursor",
    "claude": ".claude",
    "codex": ".codex",
}


def workspace_server_defs_path(workspace_root: Path, agent: str) -> Path | None:
    """Return ``.<agent>/cyt/mcp/<agent>.json`` under *workspace_root* when it exists."""
    rel_dir = _AGENT_CYT_DIRS.get((agent or "cursor").strip() or "cursor", ".cursor")
    name = (agent or "cursor").strip() or "cursor"
    cyt_dir = workspace_root / rel_dir / "cyt"
    for path in (cyt_dir / "mcp" / f"{name}.json", cyt_dir / f"{name}.json"):
        if path.is_file():
            return path
    return None


def workspace_aggregator_path(workspace_root: Path, agent: str) -> Path:
    rel_dir = _AGENT_CYT_DIRS.get((agent or "cursor").strip() or "cursor", ".cursor")
    cyt_dir = workspace_root / rel_dir / "cyt"
    for path in (
        cyt_dir / "config" / "mcp-aggregator.yaml",
        cyt_dir / "mcp-aggregator.yaml",
    ):
        if path.is_file():
            return path
    return cyt_dir / "config" / "mcp-aggregator.yaml"


def parse_workspace_root(raw: str | None) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    return path if path.is_dir() else None
