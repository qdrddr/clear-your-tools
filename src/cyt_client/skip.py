"""Troubleshooting skip switch for cyt-client hooks (stdlib only)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from cyt_client.agent import infer_harness_agent
from cyt_client.rules_file import is_valid_workspace_root, workspace_root_from_payload
from cyt_client.skills import _payload_cwd

SKIP_FILENAME = "skip.txt"
GLOBAL_SKIP_PATH = Path("~/.config/cyt/skip.txt").expanduser()
_AGENT_CYT_DIRS: dict[str, tuple[str, str]] = {
    "claude": (".claude/cyt", "~/.claude/cyt"),
    "codex": (".codex/cyt", "~/.codex/cyt"),
    "cursor": (".cursor/cyt", "~/.cursor/cyt"),
}


def _agent_for_skip(payload: dict[str, Any] | None) -> str | None:
    if payload is not None:
        for key in ("cyt_agent", "cyt_session_agent", "cytAgent"):
            raw = payload.get(key)
            if isinstance(raw, str):
                agent = raw.strip().lower()
                if agent in _AGENT_CYT_DIRS:
                    return agent
        inferred = infer_harness_agent(payload)
        if inferred is not None and inferred in _AGENT_CYT_DIRS:
            return inferred
    launch_agent = os.environ.get("CYT_LAUNCH_AGENT", "").strip().lower()
    if launch_agent in _AGENT_CYT_DIRS:
        return launch_agent
    return None


def skip_hook_paths_for_payload(payload: dict[str, Any] | None) -> list[Path]:
    """Candidate ``skip.txt`` paths: global, workspace/cwd agent dirs, agent home."""
    paths: list[Path] = [GLOBAL_SKIP_PATH]
    agent = _agent_for_skip(payload)
    pairs = [_AGENT_CYT_DIRS[agent]] if agent is not None else list(_AGENT_CYT_DIRS.values())

    roots: list[Path] = []
    if payload is not None:
        workspace = workspace_root_from_payload(payload)
        if workspace is not None and is_valid_workspace_root(workspace):
            roots.append(workspace)
        cwd = _payload_cwd(payload)
        if cwd.is_dir() and cwd not in roots:
            roots.append(cwd)
        process_cwd = Path.cwd()
        if process_cwd.is_dir() and process_cwd not in roots:
            roots.append(process_cwd)
    else:
        roots.append(Path.cwd())

    for root in roots:
        for project_rel, _home_rel in pairs:
            paths.append(root / project_rel / SKIP_FILENAME)

    if agent is not None:
        _project_rel, home_rel = _AGENT_CYT_DIRS[agent]
        paths.append(Path(home_rel).expanduser() / SKIP_FILENAME)

    return paths


def hook_skip_enabled(payload: dict[str, Any] | None = None) -> bool:
    """True when a troubleshooting ``skip.txt`` disables cyt-client hook work."""
    return any(path.is_file() for path in skip_hook_paths_for_payload(payload))
