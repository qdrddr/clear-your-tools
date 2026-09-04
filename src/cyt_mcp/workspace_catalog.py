"""Resolve workspace-scoped MCP server definitions for catalog merge."""

from __future__ import annotations

from pathlib import Path

_AGENT_CYT_DIRS: dict[str, str] = {
    "cursor": ".cursor",
    "claude": ".claude",
    "codex": ".codex",
}


def workspace_server_defs_path(workspace_root: Path, agent: str) -> Path | None:
    """Return ``.agents/cyt/config/mcp/<agent>.json`` when present, else legacy paths."""
    name = (agent or "cursor").strip() or "cursor"
    canonical = (
        workspace_root / ".agents" / "cyt" / "config" / "mcp" / f"{name}.json"
    )
    if canonical.is_file():
        return canonical
    rel_dir = _AGENT_CYT_DIRS.get(name, ".cursor")
    cyt_dir = workspace_root / rel_dir / "cyt"
    for path in (cyt_dir / "mcp" / f"{name}.json", cyt_dir / f"{name}.json"):
        if path.is_file():
            return path
    return None


def workspace_aggregator_path(workspace_root: Path, agent: str) -> Path:
    canonical = (
        workspace_root / ".agents" / "cyt" / "config" / "mcp-aggregator.yaml"
    )
    if canonical.is_file():
        return canonical
    rel_dir = _AGENT_CYT_DIRS.get((agent or "cursor").strip() or "cursor", ".cursor")
    cyt_dir = workspace_root / rel_dir / "cyt"
    for path in (
        cyt_dir / "config" / "mcp-aggregator.yaml",
        cyt_dir / "mcp-aggregator.yaml",
    ):
        if path.is_file():
            return path
    return canonical


def parse_workspace_root(raw: str | None) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    return path if path.is_dir() else None
