"""Write pruned hook injection to Cursor workspace rules (stdlib only)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

RULES_REL_PATH = Path(".cursor/rules/cyt-injection.mdc")
GITIGNORE_ENTRY = ".cursor/rules/cyt-injection.mdc"
_RULES_DESCRIPTION = "CYT pruned skills and tools for this prompt"
_custom_rules_rel_path: Path | None = None


def set_rules_file_rel_path(path: str | None) -> None:
    """Override the workspace-relative rules path (e.g. from ``--rule``)."""
    global _custom_rules_rel_path
    if path is None or not path.strip():
        _custom_rules_rel_path = None
        return
    _custom_rules_rel_path = Path(path.strip())


def reset_rules_file_rel_path() -> None:
    """Clear any custom rules path override (mainly for tests)."""
    set_rules_file_rel_path(None)


def cursor_rules_file_enabled() -> bool:
    raw = os.environ.get("CYT_CURSOR_RULES_FILE", "1").strip().casefold()
    return raw not in {"0", "false", "no", "off"}


def workspace_root_from_payload(payload: dict[str, Any]) -> Path | None:
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return Path(cwd.strip())

    roots = payload.get("workspace_roots")
    if isinstance(roots, list):
        for root in roots:
            if isinstance(root, str) and root.strip():
                return Path(root.strip())
    return None


def is_valid_workspace_root(workspace: Path) -> bool:
    """Return True when ``workspace`` exists and is a directory."""
    try:
        return workspace.is_dir()
    except OSError:
        return False


def rules_file_path(workspace: Path) -> Path:
    rel = _custom_rules_rel_path if _custom_rules_rel_path is not None else RULES_REL_PATH
    if rel.is_absolute():
        return rel
    return workspace / rel


def _gitignore_entry_for_rules_path(workspace: Path, path: Path) -> str | None:
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return None


def build_rules_mdc(injection: str) -> str:
    body = injection.rstrip()
    return f"---\ndescription: {_RULES_DESCRIPTION}\nalwaysApply: true\n---\n\n{body}\n"


def extract_additional_context(body: bytes) -> str:
    if not body.strip():
        return ""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""

    hook_output = data.get("hookSpecificOutput")
    if not isinstance(hook_output, dict):
        return ""

    context = hook_output.get("additionalContext") or hook_output.get("additional_context")
    if isinstance(context, str):
        return context.strip()
    return ""


def ensure_gitignore_entry(workspace: Path, rel_path: str = GITIGNORE_ENTRY) -> None:
    git_dir = workspace / ".git"
    if not git_dir.is_dir():
        return

    gitignore_path = workspace / ".gitignore"
    line = rel_path.strip()
    if not line:
        return

    if gitignore_path.is_file():
        existing = gitignore_path.read_text(encoding="utf-8")
        if any(entry.strip() == line for entry in existing.splitlines()):
            return
        suffix = "" if existing.endswith("\n") or not existing else "\n"
        gitignore_path.write_text(f"{existing}{suffix}{line}\n", encoding="utf-8")
        return

    gitignore_path.write_text(f"{line}\n", encoding="utf-8")


def delete_cursor_rules_file(workspace: Path) -> bool:
    """Delete the rules file if present. Return True when a file was removed."""
    if not cursor_rules_file_enabled() or not is_valid_workspace_root(workspace):
        return False

    path = rules_file_path(workspace)
    if not path.is_file():
        return False
    path.unlink()
    return True


def sync_cursor_rules_file(workspace: Path, injection: str) -> bool:
    """Write or delete the rules file. Return True when disk state changed."""
    if not cursor_rules_file_enabled() or not is_valid_workspace_root(workspace):
        return False

    path = rules_file_path(workspace)
    if not injection.strip():
        return delete_cursor_rules_file(workspace)

    new_content = build_rules_mdc(injection)
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
        if existing == new_content:
            return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_content, encoding="utf-8")
    if gitignore_entry := _gitignore_entry_for_rules_path(workspace, path):
        ensure_gitignore_entry(workspace, rel_path=gitignore_entry)
    return True
