"""Session injection log I/O for cyt-client (stdlib only)."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from cyt_client.agent import infer_harness_agent
from cyt_client.rules_file import is_valid_workspace_root, workspace_root_from_payload
from cyt_client.skills import _payload_cwd


def _agent_from_payload(payload: dict[str, Any]) -> str | None:
    raw = payload.get("cyt_agent")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return infer_harness_agent(payload)


_AGENT_SESSION_DIRS: dict[str, tuple[str, str]] = {
    "claude": (".claude/cyt/sessions", "~/.claude/cyt/sessions"),
    "codex": (".codex/cyt/sessions", "~/.codex/cyt/sessions"),
    "cursor": (".cursor/cyt/sessions", "~/.cursor/cyt/sessions"),
}

_CONFIG_SESSIONS_DIR = Path("~/.config/cyt/sessions").expanduser()
_META_TYPE = "meta"
_SESSION_ID_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_DEFAULT_MAX_AGE_SECONDS = 86400


def session_id_from_payload(payload: dict[str, Any]) -> str | None:
    """Extract session id from hook payload fields."""
    for layer in (
        payload,
        payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
    ):
        if not isinstance(layer, dict):
            continue
        for key in ("session_id", "sessionId"):
            raw = layer.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        conversation_id = layer.get("conversation_id")
        if isinstance(conversation_id, str) and conversation_id.strip():
            return conversation_id.strip()
    return None


def _safe_session_filename(session_id: str) -> str:
    cleaned = _SESSION_ID_SAFE_RE.sub("_", session_id.strip())
    return cleaned or "session"


def sessions_dir_for_agent(agent: str) -> Path:
    """Return agent-home session directory ``~/.<agent>/cyt/sessions``."""
    if agent not in _AGENT_SESSION_DIRS:
        return _CONFIG_SESSIONS_DIR
    _project_rel, home_rel = _AGENT_SESSION_DIRS[agent]
    return Path(home_rel).expanduser()


def index_of_latest_compaction(entries: list[dict[str, Any]]) -> int | None:
    for index in range(len(entries) - 1, -1, -1):
        if entries[index].get("kind") == "compaction":
            return index
    return None


def entries_after_latest_compaction(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    boundary = index_of_latest_compaction(entries)
    if boundary is None:
        return list(entries)
    start = boundary + 1
    return list(entries[start:])


def read_session_log_post_compaction(path: Path) -> list[dict[str, Any]]:
    _agent, entries = read_session_log_file(path)
    return entries_after_latest_compaction(entries)


def _post_compaction_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return entries_after_latest_compaction(entries)


def sessions_dir_for_payload(payload: dict[str, Any]) -> Path:
    """Resolve sessions directory: workspace → agent home → ~/.config/cyt/sessions."""
    agent = _agent_from_payload(payload)
    if agent is not None:
        from cyt_client.config import inject_via_for_agent

        if inject_via_for_agent(agent) == "proxy":
            return sessions_dir_for_agent(agent)
    pairs = (
        [_AGENT_SESSION_DIRS[agent]] if agent is not None else list(_AGENT_SESSION_DIRS.values())
    )

    workspace = workspace_root_from_payload(payload)
    if workspace is not None and is_valid_workspace_root(workspace):
        for project_rel, _home_rel in pairs:
            candidate = workspace / project_rel
            return candidate

    cwd = _payload_cwd(payload)
    if cwd.is_dir():
        for project_rel, _home_rel in pairs:
            candidate = cwd / project_rel
            return candidate

    if agent is not None:
        _project_rel, home_rel = _AGENT_SESSION_DIRS[agent]
        return Path(home_rel).expanduser()

    return _CONFIG_SESSIONS_DIR


def session_log_path(payload: dict[str, Any]) -> Path | None:
    session_id = session_id_from_payload(payload)
    if session_id is None:
        return None
    return sessions_dir_for_payload(payload) / f"{_safe_session_filename(session_id)}.jsonl"


def _parse_jsonl_line(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def read_session_log_file(path: Path) -> tuple[str | None, list[dict[str, Any]]]:
    """Return (agent from meta line, item entries)."""
    if not path.is_file():
        return None, []

    agent: str | None = None
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = _parse_jsonl_line(line)
        if record is None:
            continue
        if record.get("type") == _META_TYPE:
            raw_agent = record.get("agent")
            if isinstance(raw_agent, str) and raw_agent.strip():
                agent = raw_agent.strip()
            continue
        items.append(record)
    return agent, items


def agent_from_session_file(path: Path) -> str | None:
    agent, _items = read_session_log_file(path)
    return agent


def read_session_log(path: Path, *, post_compaction_only: bool = True) -> list[dict[str, Any]]:
    """Parse JSONL item lines (meta excluded).

    When *post_compaction_only* is true (default), return entries after the latest
    ``kind: compaction`` marker.
    """
    _agent, items = read_session_log_file(path)
    if post_compaction_only:
        return _post_compaction_entries(items)
    return items


def append_session_log(
    path: Path,
    entries: list[dict[str, Any]],
    *,
    agent: str | None = None,
) -> None:
    """Append item records; write meta line on first create."""
    if not entries:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    new_lines: list[str] = []
    if not path.is_file() and agent:
        new_lines.append(json.dumps({"type": _META_TYPE, "agent": agent}, separators=(",", ":")))
    for entry in entries:
        new_lines.append(json.dumps(entry, separators=(",", ":"), ensure_ascii=False))

    with path.open("a", encoding="utf-8") as handle:
        for line in new_lines:
            if handle.tell() > 0:
                handle.write("\n")
            handle.write(line)


def cleanup_stale_session_logs(
    directory: Path,
    current_session_id: str | None,
    *,
    max_age_seconds: float = _DEFAULT_MAX_AGE_SECONDS,
) -> list[Path]:
    """Delete session files older than max_age by mtime, excluding current session."""
    if not directory.is_dir():
        return []

    current_name = (
        f"{_safe_session_filename(current_session_id)}.jsonl" if current_session_id else None
    )
    cutoff = time.time() - max_age_seconds
    removed: list[Path] = []

    for path in directory.glob("*.jsonl"):
        if not path.is_file():
            continue
        if current_name is not None and path.name == current_name:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            continue
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            continue
    return removed


def gitignore_entries_for_sessions() -> tuple[str, ...]:
    return tuple(
        f"{project_rel.split('/')[0]}/cyt/sessions/"
        for project_rel, _ in _AGENT_SESSION_DIRS.values()
    )


def read_latest_tool_catalogs(path: Path) -> dict[str, dict[str, Any]]:
    """Return latest full tool_catalog entry per catalog key (last non-empty tools wins)."""
    if not path.is_file():
        return {}
    catalogs: dict[str, dict[str, Any]] = {}
    _agent, entries = read_session_log_file(path)
    entries = _post_compaction_entries(entries)
    for entry in entries:
        if entry.get("kind") != "tool_catalog":
            continue
        tools = entry.get("tools")
        if not isinstance(tools, list) or not tools:
            continue
        catalog = str(entry.get("catalog") or "").strip()
        key = str(entry.get("key") or f"tool_catalog:{catalog}").strip()
        if key:
            catalogs[key] = entry
        if catalog:
            catalogs[f"tool_catalog:{catalog}"] = entry
    return catalogs


def read_tool_catalog_hashes(path: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    if not path.is_file():
        return hashes
    _agent, entries = read_session_log_file(path)
    entries = _post_compaction_entries(entries)
    for entry in entries:
        if entry.get("kind") != "tool_catalog":
            continue
        raw_hash = entry.get("hash")
        if not isinstance(raw_hash, str) or not raw_hash.strip():
            continue
        catalog = str(entry.get("catalog") or "").strip()
        key = str(entry.get("key") or f"tool_catalog:{catalog}").strip()
        if key:
            hashes[key] = raw_hash.strip()
        if catalog:
            hashes[f"tool_catalog:{catalog}"] = raw_hash.strip()
    return hashes


def read_tools_inject_enabled(path: Path) -> bool | None:
    if not path.is_file():
        return None
    _agent, entries = read_session_log_file(path)
    for entry in reversed(entries):
        if entry.get("kind") != "session_state":
            continue
        if str(entry.get("key") or "") != "session_state:inject":
            continue
        flag = entry.get("tools_inject_enabled")
        if isinstance(flag, bool):
            return flag
    return None


def read_hallucination_gate_enabled(path: Path) -> bool | None:
    if not path.is_file():
        return None
    _agent, entries = read_session_log_file(path)
    for entry in reversed(entries):
        if entry.get("kind") != "session_state":
            continue
        if str(entry.get("key") or "") != "session_state:inject":
            continue
        flag = entry.get("hallucination_gate_enabled")
        if isinstance(flag, bool):
            return flag
    return None


def _existing_keys_and_hashes(path: Path, kind: str) -> set[tuple[str, str]]:
    return {
        (str(entry.get("key") or ""), str(entry.get("hash") or ""))
        for entry in read_session_log(path)
        if entry.get("kind") == kind
    }


def _append_deduped_kind_entries(
    path: Path,
    entries: list[dict[str, Any]],
    *,
    kind: str,
    agent: str | None = None,
) -> None:
    """Append session log lines for *kind* with client-side (key, hash) dedup."""
    to_append: list[dict[str, Any]] = []
    existing = _existing_keys_and_hashes(path, kind)
    for entry in entries:
        if entry.get("kind") != kind:
            continue
        key = str(entry.get("key") or "").strip()
        content_hash = str(entry.get("hash") or "").strip()
        if key and content_hash and (key, content_hash) in existing:
            continue
        to_append.append(entry)
        if key and content_hash:
            existing.add((key, content_hash))
    append_session_log(path, to_append, agent=agent)


def append_tool_entries(
    path: Path,
    entries: list[dict[str, Any]],
    *,
    agent: str | None = None,
) -> None:
    """Append Type-1 tool lines with client-side (key, hash) dedup."""
    _append_deduped_kind_entries(path, entries, kind="tool", agent=agent)


def append_skill_entries(
    path: Path,
    entries: list[dict[str, Any]],
    *,
    agent: str | None = None,
) -> None:
    """Append skill lines with client-side (key, hash) dedup."""
    _append_deduped_kind_entries(path, entries, kind="skill", agent=agent)


def append_resource_entries(
    path: Path,
    entries: list[dict[str, Any]],
    *,
    agent: str | None = None,
) -> None:
    """Append resource lines with client-side (key, hash) dedup."""
    _append_deduped_kind_entries(path, entries, kind="resource", agent=agent)


def append_tool_catalog_entries(
    path: Path,
    entries: list[dict[str, Any]],
    *,
    agent: str | None = None,
) -> None:
    """Append tool_catalog lines with client-side partition hash dedup."""
    to_append: list[dict[str, Any]] = []
    latest = read_latest_tool_catalogs(path)
    for entry in entries:
        if entry.get("kind") != "tool_catalog":
            continue
        tools = entry.get("tools")
        if not isinstance(tools, list) or not tools:
            continue
        catalog = str(entry.get("catalog") or "").strip()
        key = f"tool_catalog:{catalog}" if catalog else str(entry.get("key") or "")
        existing = latest.get(key)
        if existing is not None and str(existing.get("hash") or "") == str(entry.get("hash") or ""):
            continue
        to_append.append(entry)
    append_session_log(path, to_append, agent=agent)
