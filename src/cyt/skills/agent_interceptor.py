"""Hook daemon handler for agent skill read interception."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from cyt.config import (
    skills_directories,
    skills_enabled,
    skills_hook_agent_interceptor_min_items,
)
from cyt.injection.session_log_build import build_skill_log_entry
from cyt.pruners.remote import PrunerSettingsCache
from cyt.skills.agents import is_excluded_agent_system_skill, resolve_skills_agent
from cyt.skills.catalog import (
    SkillEntryRef,
    build_registry_from_inline_sources,
    content_sha256_for_file,
)
from cyt.skills.diagnostics import SearchItemRow
from cyt.skills.reconstruct import reconstruct_matches_from_items
from cyt.skills.search import MatchedSkill, search_skills_with_trace


def _append_resolved_directory(
    directories: list[Path],
    seen: set[Path],
    candidate: Path,
) -> None:
    try:
        resolved = candidate.expanduser().resolve()
    except OSError:
        return
    if resolved in seen:
        return
    seen.add(resolved)
    directories.append(resolved)


def _allow_response(
    *,
    updated_input: dict[str, Any] | None = None,
    skill_log_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"agent_interceptor": True, "permission": "allow"}
    if updated_input is not None:
        payload["updated_input"] = updated_input
    if skill_log_entry is not None:
        payload["skill_log_entry"] = skill_log_entry
    return payload


def _resolve_skill_directories(config: dict[str, Any], payload: dict[str, Any]) -> list[Path]:
    cwd_raw = payload.get("cwd")
    workspace_roots = payload.get("workspace_roots")
    cwd = Path(str(cwd_raw)).expanduser() if isinstance(cwd_raw, str) and cwd_raw.strip() else None
    if cwd is None and isinstance(workspace_roots, list) and workspace_roots:
        first = workspace_roots[0]
        if isinstance(first, str) and first.strip():
            cwd = Path(first.strip()).expanduser()

    directories: list[Path] = []
    seen: set[Path] = set()
    agent = resolve_skills_agent(payload=payload)

    agent_pairs = {
        "cursor": (".cursor/skills", "~/.cursor/skills"),
        "claude": (".claude/skills", "~/.claude/skills"),
        "codex": (".codex/skills", "~/.codex/skills"),
    }
    pairs = [agent_pairs[agent]] if agent in agent_pairs else list(agent_pairs.values())
    if cwd is not None:
        for project_rel, home_rel in pairs:
            for candidate in (cwd / project_rel, Path(home_rel).expanduser()):
                _append_resolved_directory(directories, seen, candidate)

    for raw in skills_directories(config):
        _append_resolved_directory(directories, seen, Path(raw).expanduser())
    return directories


def _path_under_directories(path: Path, directories: list[Path]) -> bool:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return False
    for skill_dir in directories:
        try:
            base = skill_dir.expanduser().resolve()
        except OSError:
            continue
        if resolved == base or base in resolved.parents:
            return True
    return False


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _select_rows_with_min_items(
    rows: list[SearchItemRow],
    *,
    min_items: int,
) -> list[SearchItemRow]:
    passed = [row for row in rows if row.passed]
    passed.sort(key=lambda row: row.score, reverse=True)
    if len(passed) >= min_items:
        return passed
    ranked = sorted(rows, key=lambda row: row.score, reverse=True)
    selected = ranked[: max(min_items, len(passed))]
    if not selected:
        return passed
    return selected


def _ensure_skill_entry(config: dict[str, Any], skill_path: Path) -> SkillEntryRef | None:
    content = skill_path.read_text(encoding="utf-8")
    content_hash = content_sha256_for_file(skill_path)
    inline = [
        {
            "path": str(skill_path.resolve()),
            "content": content,
            "content_sha256": content_hash,
        },
    ]
    entries = build_registry_from_inline_sources(
        config,
        inline,
        original_by_hash={content_hash: skill_path},
    )
    return entries[0] if entries else None


def _prune_single_skill(
    query: str,
    entry: SkillEntryRef,
    *,
    config: dict[str, Any],
    pruner_settings: PrunerSettingsCache | None,
    min_items: int,
) -> MatchedSkill | None:
    _matches, trace = search_skills_with_trace(
        query,
        [entry],
        config=config,
        pruner_settings=pruner_settings,
        skip_frontmatter_gate=True,
        max_tokens=None,
    )
    item_kind = trace.search_item_kind or "node"
    selected_rows = _select_rows_with_min_items(trace.search_rows, min_items=min_items)
    if not selected_rows:
        return None
    matches = reconstruct_matches_from_items(
        selected_rows,
        [entry],
        item_kind=item_kind,
    )
    return matches[0] if matches else None


def _skinny_path_for_payload(payload: dict[str, Any], content_hash: str) -> Path:
    session_id = str(
        payload.get("conversation_id") or payload.get("session_id") or "session",
    ).strip()
    hash_part = content_hash[:12]
    workspace_roots = payload.get("workspace_roots")
    if isinstance(workspace_roots, list) and workspace_roots:
        first = workspace_roots[0]
        if isinstance(first, str) and first.strip():
            workspace = Path(first.strip()).expanduser()
            if workspace.is_dir():
                return workspace / ".cyt" / "skinny" / session_id / f"{hash_part}.md"
    return Path("~/.config/cyt/skinny").expanduser() / session_id / f"{hash_part}.md"


def run_skill_read_intercept(
    payload: dict[str, Any],
    config: dict[str, Any],
    *,
    pruner_settings: PrunerSettingsCache | None = None,
) -> dict[str, Any]:
    if not skills_enabled(config):
        return _allow_response()

    read_raw = payload.get("cyt_intercept_read_path") or payload.get("tool_input", {}).get("path")
    if not isinstance(read_raw, str) or not read_raw.strip():
        return _allow_response()

    skill_path = Path(read_raw).expanduser()
    if not skill_path.is_file() or not str(skill_path).lower().endswith(".md"):
        return _allow_response()

    agent = resolve_skills_agent(payload=payload)
    if is_excluded_agent_system_skill(skill_path, active_agent=agent):
        return _allow_response()

    directories = _resolve_skill_directories(config, payload)
    if not _path_under_directories(skill_path, directories):
        return _allow_response()

    query = str(payload.get("cyt_intercept_query") or "").strip()
    if not query:
        return _allow_response()

    entry = _ensure_skill_entry(config, skill_path)
    if entry is None:
        return _allow_response()

    min_items = skills_hook_agent_interceptor_min_items(config)
    match = _prune_single_skill(
        query,
        entry,
        config=config,
        pruner_settings=pruner_settings,
        min_items=min_items,
    )
    if match is None or not match.markdown.strip():
        return _allow_response()

    content_hash = content_sha256_for_file(skill_path)
    skinny_path = _skinny_path_for_payload(payload, content_hash)
    _atomic_write(skinny_path, match.markdown)

    skill_log_entry = build_skill_log_entry(match, full=False)
    return _allow_response(
        updated_input={"path": str(skinny_path.resolve())},
        skill_log_entry=skill_log_entry,
    )


def format_intercept_stdout(result: dict[str, Any]) -> str:
    return json.dumps(result, separators=(",", ":"))
