"""Default skills permission path rules shipped with CYT."""

from __future__ import annotations

from cyt.permissions.match import format_skill_path_permission_entry

# Codex built-in system skills are indexed for discovery but denied by default.
DEFAULT_CODEX_SYSTEM_SKILL_PATHS: tuple[str, ...] = (
    ".codex/skills/.system",
    "~/.codex/skills/.system",
)

DEFAULT_CODEX_SYSTEM_SKILL_PATH_DENIES: tuple[str, ...] = tuple(
    format_skill_path_permission_entry(path) for path in DEFAULT_CODEX_SYSTEM_SKILL_PATHS
)

DEFAULT_CLAUDE_SYSTEM_SKILL_PATHS: tuple[str, ...] = (
    ".claude/skills/.system",
    "~/.claude/skills/.system",
)

DEFAULT_CURSOR_SYSTEM_SKILL_PATHS: tuple[str, ...] = (
    ".cursor/skills/.system",
    "~/.cursor/skills/.system",
)

DEFAULT_AGENT_SYSTEM_SKILL_PATH_DENIES: dict[str, tuple[str, ...]] = {
    "cursor": tuple(
        format_skill_path_permission_entry(path)
        for path in DEFAULT_CODEX_SYSTEM_SKILL_PATHS + DEFAULT_CLAUDE_SYSTEM_SKILL_PATHS
    ),
    "claude": tuple(
        format_skill_path_permission_entry(path)
        for path in DEFAULT_CODEX_SYSTEM_SKILL_PATHS + DEFAULT_CURSOR_SYSTEM_SKILL_PATHS
    ),
    "codex": tuple(
        format_skill_path_permission_entry(path)
        for path in DEFAULT_CLAUDE_SYSTEM_SKILL_PATHS + DEFAULT_CURSOR_SYSTEM_SKILL_PATHS
    ),
}
