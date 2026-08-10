"""Agent skill read interceptor helpers for cyt-client (stdlib only)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from cyt_client.agent import infer_harness_agent
from cyt_client.config import skills_hook_agent_interceptor_enabled
from cyt_client.port import resolve_hook_url
from cyt_client.sessions import (
    append_session_log,
    entries_after_latest_compaction,
    read_session_log_file,
    session_log_path,
)
from cyt_client.skills import _payload_cwd, infer_launch_agent, skill_directories_for_payload

FULL_PROMOTION_THRESHOLD = 3
_READ_TOOL_NAMES = frozenset({"Read"})
_SYSTEM_SKILLS_DIR = ".system"
_HOME_PREFIX_RE = re.compile(r"^/Users/[^/]+")


InjectionMode = Literal["skip", "skinny", "full", "deny_full_reread"]
PostHookInject = Callable[[str, bytes], tuple[int, bytes]]
_USER_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL | re.IGNORECASE)
_TIMESTAMP_RE = re.compile(r"<timestamp>.*?</timestamp>\s*", re.DOTALL | re.IGNORECASE)


def extract_read_tool_call(payload: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Extract native Read tool name and args from preToolUse payload layers."""
    from cyt_client.tool_gate import _extract_tool_call

    tool_name, tool_args = _extract_tool_call(payload)
    if tool_name != "Read" or tool_args is None:
        return None, None
    return tool_name, tool_args


def read_path_from_payload(payload: dict[str, Any]) -> str | None:
    """Resolve read target path from preToolUse or beforeReadFile payloads."""
    from cyt_client.tool_gate import _payload_layers

    for layer in _payload_layers(payload):
        for field in ("file_path", "filePath", "path"):
            raw = layer.get(field)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    _tool_name, tool_input = extract_read_tool_call(payload)
    if tool_input is not None:
        return read_path_from_tool_input(tool_input)
    return None


def shorten_home_path(path: str | Path) -> str:
    text = str(path).strip()
    home = Path.home()
    try:
        resolved = Path(text).expanduser().resolve()
    except OSError:
        return text
    try:
        rel = resolved.relative_to(home)
    except ValueError:
        return text
    return f"~/{rel.as_posix()}"


def skill_item_key_for_path(path: str | Path) -> str:
    return f"skill:{shorten_home_path(path)}"


def content_sha256_for_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def format_search_query(user_query: str, assistant_message: str | None = None) -> str:
    base = f"User_Asks: {user_query}"
    if assistant_message:
        return f"{base}; Assistant_Says: {assistant_message}"
    return base


@dataclass(frozen=True)
class SessionLogIndex:
    entries: tuple[dict[str, Any], ...]

    @classmethod
    def from_entries(cls, entries: list[dict[str, Any]]) -> SessionLogIndex:
        sliced = entries_after_latest_compaction(entries)
        return cls(entries=tuple(sliced))

    def count_key(self, key: str) -> int:
        return sum(1 for entry in self.entries if str(entry.get("key") or "") == key)

    def latest_hash(self, key: str) -> str | None:
        for entry in reversed(self.entries):
            if str(entry.get("key") or "") != key:
                continue
            raw = entry.get("hash")
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        return None

    def has_satisfied_full(self, key: str, current_hash: str) -> bool:
        for entry in reversed(self.entries):
            if str(entry.get("key") or "") != key:
                continue
            if not entry.get("full"):
                continue
            logged_hash = entry.get("hash")
            if isinstance(logged_hash, str) and logged_hash.strip() == current_hash:
                return True
        return False

    def has_skill_entry(self, key: str) -> bool:
        return any(
            entry.get("kind") == "skill" and str(entry.get("key") or "") == key
            for entry in self.entries
        )

    def has_prompt_injected_skill(self, key: str) -> bool:
        """True when a turn exists and a skill entry follows prompt-time injection."""
        saw_turn = False
        for entry in self.entries:
            if entry.get("kind") == "turn":
                saw_turn = True
                continue
            if entry.get("kind") == "skill" and str(entry.get("key") or "") == key:
                return saw_turn
        return False


def resolve_read_intercept_mode(
    *,
    key: str,
    current_hash: str,
    index: SessionLogIndex,
) -> InjectionMode:
    if index.has_satisfied_full(key, current_hash):
        return "deny_full_reread"
    latest = index.latest_hash(key)
    if latest is not None and latest != current_hash:
        return "full"
    if index.count_key(key) >= FULL_PROMOTION_THRESHOLD:
        return "full"
    return "skinny"


def normalize_turn_prompt(text: str) -> str:
    """Normalize Cursor transcript/session prompts for same-turn comparison."""
    stripped = text.strip()
    if not stripped:
        return ""
    match = _USER_QUERY_RE.search(stripped)
    if match:
        return match.group(1).strip()
    return _TIMESTAMP_RE.sub("", stripped).strip()


def read_path_from_tool_input(tool_input: dict[str, Any]) -> str | None:
    for field in ("path", "file_path", "filePath"):
        raw = tool_input.get(field)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    nested = tool_input.get("input")
    if isinstance(nested, dict):
        for field in ("path", "file_path", "filePath"):
            raw = nested.get(field)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    return None


def is_native_read_tool(tool_name: str) -> bool:
    return tool_name.strip() in _READ_TOOL_NAMES


def has_partial_read_params(tool_input: dict[str, Any]) -> bool:
    for field in ("offset", "limit"):
        if field not in tool_input:
            continue
        value = tool_input.get(field)
        if value is not None:
            return True
    return False


def is_excluded_system_skill(path: Path, *, agent: str | None) -> bool:
    if agent is None:
        return False
    resolved = path.expanduser().resolve()
    parts = resolved.parts
    for index, part in enumerate(parts):
        if part != _SYSTEM_SKILLS_DIR:
            continue
        if index < 2 or parts[index - 1] != "skills":
            continue
        owner = parts[index - 2].removeprefix(".")
        if owner in {"claude", "codex", "cursor"} and owner != agent:
            return True
    return False


def _resolve_directory(path: Path) -> Path | None:
    try:
        return path.expanduser().resolve()
    except OSError:
        return None


def _skill_directories_from_session_log(log_path: Path) -> list[Path] | None:
    if not log_path.is_file():
        return None
    _agent, entries = read_session_log_file(log_path)
    for entry in reversed(entries_after_latest_compaction(entries)):
        if entry.get("kind") != "skill_directories":
            continue
        raw = entry.get("directories")
        if not isinstance(raw, list):
            continue
        dirs: list[Path] = []
        for item in raw:
            if not isinstance(item, str) or not item.strip():
                continue
            resolved = _resolve_directory(Path(item))
            if resolved is not None:
                dirs.append(resolved)
        if dirs:
            return dirs
    return None


def _skill_directories_from_payload(payload: dict[str, Any]) -> list[Path]:
    directories: list[Path] = []
    seen: set[Path] = set()
    for candidate in skill_directories_for_payload(payload):
        resolved = _resolve_directory(candidate)
        if resolved is None or resolved in seen:
            continue
        seen.add(resolved)
        directories.append(resolved)
    return directories


def skill_directories_from_session_or_payload(payload: dict[str, Any]) -> list[Path]:
    path = session_log_path(payload)
    if path is not None:
        session_dirs = _skill_directories_from_session_log(path)
        if session_dirs:
            return session_dirs
    return _skill_directories_from_payload(payload)


def is_skill_md_under_directories(path_str: str, directories: list[Path]) -> bool:
    if not path_str.lower().endswith(".md"):
        return False
    try:
        resolved = Path(path_str).expanduser().resolve()
    except OSError:
        return False
    for skill_dir in directories:
        base = _resolve_directory(skill_dir)
        if base is None:
            continue
        if resolved == base or base in resolved.parents:
            return True
    return False


def latest_turn_prompt_from_session(entries: list[dict[str, Any]]) -> str:
    sliced = entries_after_latest_compaction(entries)
    for entry in reversed(sliced):
        if entry.get("kind") != "turn":
            continue
        prompt = str(entry.get("prompt") or "").strip()
        if prompt:
            return prompt
    return ""


def intercept_query_for_payload(payload: dict[str, Any], entries: list[dict[str, Any]]) -> str:
    from cyt_client.transcript import last_assistant_from_payload, last_user_from_payload

    user = last_user_from_payload(payload)
    if user:
        normalized_user = normalize_turn_prompt(user)
        assistant = last_assistant_from_payload(payload)
        normalized_assistant = normalize_turn_prompt(assistant) if assistant else None
        return format_search_query(normalized_user, normalized_assistant or None)
    return latest_turn_query_from_session(entries)


def should_deny_same_turn_preinjected_skill(
    key: str,
    index: SessionLogIndex,
    *,
    session_prompt: str,
    transcript_prompt: str,
) -> bool:
    if not index.has_skill_entry(key):
        return False
    normalized_session = normalize_turn_prompt(session_prompt)
    normalized_transcript = normalize_turn_prompt(transcript_prompt)
    if not normalized_session or not normalized_transcript:
        return False
    return normalized_session == normalized_transcript


def _append_full_skill_log_entry(
    payload: dict[str, Any],
    *,
    skill_file: Path,
    content_hash: str,
    key: str,
    agent: str,
) -> None:
    log_path = session_log_path(payload)
    if log_path is None:
        return
    try:
        body = skill_file.read_text(encoding="utf-8")
    except OSError:
        body = ""
    skill_name = (
        skill_file.parent.name if skill_file.name.upper() == "SKILL.MD" else skill_file.stem
    )
    append_session_log(
        log_path,
        [
            {
                "kind": "skill",
                "key": key,
                "hash": content_hash,
                "full": True,
                "source": "file",
                "body": body,
                "name": skill_name,
                "path": shorten_home_path(skill_file),
            },
        ],
        agent=agent,
    )


def latest_turn_query_from_session(entries: list[dict[str, Any]]) -> str:
    sliced = entries_after_latest_compaction(entries)
    for entry in reversed(sliced):
        if entry.get("kind") != "turn":
            continue
        prompt = str(entry.get("prompt") or "").strip()
        assistant = str(entry.get("assistant") or "").strip()
        if prompt:
            return format_search_query(prompt, assistant or None)
    return ""


def load_session_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    path = session_log_path(payload)
    if path is None or not path.is_file():
        return []
    _agent, entries = read_session_log_file(path)
    return entries


def build_intercept_request_payload(
    payload: dict[str, Any],
    *,
    read_path: str,
    query: str,
) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["cyt_intercept_read_path"] = read_path
    enriched["cyt_intercept_query"] = query
    enriched["cyt_agent_interceptor"] = True
    return enriched


def parse_agent_interceptor_response(body: bytes) -> dict[str, Any] | None:
    if not body.strip():
        return None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("agent_interceptor") or parsed.get("cyt_agent_interceptor"):
        return parsed
    hook_output = parsed.get("hookSpecificOutput")
    if isinstance(hook_output, dict) and hook_output.get("agent_interceptor"):
        return hook_output
    return None


def format_pre_tool_allow() -> str:
    return json.dumps({"permission": "allow"})


def format_pre_tool_allow_updated_input(updated_input: dict[str, Any]) -> str:
    return json.dumps({"permission": "allow", "updated_input": updated_input})


def format_pre_tool_deny(user_message: str) -> str:
    return json.dumps({"permission": "deny", "user_message": user_message})


def format_claude_pre_tool_allow_updated_input(updated_input: dict[str, Any]) -> str:
    return json.dumps(
        {
            "hookSpecificOutput": {
                "permissionDecision": "allow",
                "updatedInput": updated_input,
            },
        },
    )


def format_codex_pre_tool_allow_updated_input(updated_input: dict[str, Any]) -> str:
    return json.dumps(
        {
            "permissionDecision": "allow",
            "updatedInput": updated_input,
        },
    )


def format_codex_pre_tool_allow() -> str:
    return json.dumps({"permissionDecision": "allow"})


def format_pre_tool_response(
    *,
    agent: str,
    permission: Literal["allow", "deny"],
    updated_input: dict[str, Any] | None = None,
    user_message: str | None = None,
) -> str:
    if permission == "deny":
        return format_pre_tool_deny(user_message or "Read denied by CYT agent interceptor")
    if updated_input:
        if agent == "claude":
            return format_claude_pre_tool_allow_updated_input(updated_input)
        if agent == "codex":
            return format_codex_pre_tool_allow_updated_input(updated_input)
        return format_pre_tool_allow_updated_input(updated_input)
    if agent == "claude":
        return json.dumps({"hookSpecificOutput": {"permissionDecision": "allow"}})
    if agent == "codex":
        return format_codex_pre_tool_allow()
    return format_pre_tool_allow()


def skinny_output_path(
    payload: dict[str, Any],
    *,
    session_id: str,
    content_hash: str,
) -> Path:
    hash_part = content_hash[:12]
    workspace = _payload_cwd(payload)
    if workspace.is_dir():
        return workspace / ".cyt" / "skinny" / session_id / f"{hash_part}.md"
    return Path("~/.config/cyt/skinny").expanduser() / session_id / f"{hash_part}.md"


def ensure_gitignore_skinny(workspace: Path) -> None:
    gitignore = workspace / ".gitignore"
    entry = ".cyt/skinny/"
    try:
        if gitignore.is_file():
            text = gitignore.read_text(encoding="utf-8")
            if entry in text or ".cyt/skinny" in text:
                return
            suffix = "" if text.endswith("\n") or not text else "\n"
            gitignore.write_text(f"{text}{suffix}{entry}\n", encoding="utf-8")
        else:
            gitignore.write_text(f"{entry}\n", encoding="utf-8")
    except OSError:
        return


def effective_intercept_agent(payload: dict[str, Any]) -> str:
    return (
        infer_harness_agent(payload) or os.environ.get("CYT_LAUNCH_AGENT", "").strip() or "cursor"
    )


def should_attempt_read_intercept(payload: dict[str, Any], tool_name: str) -> bool:
    return skills_hook_agent_interceptor_enabled() and is_native_read_tool(tool_name)


def persist_skill_directories_to_session_log(payload: dict[str, Any]) -> bool:
    path = session_log_path(payload)
    if path is None:
        return False
    _agent, entries = read_session_log_file(path)
    if any(str(entry.get("key") or "") == "skill_directories" for entry in entries):
        return False
    directories: list[str] = []
    seen: set[str] = set()
    for candidate in skill_directories_for_payload(payload):
        try:
            resolved = str(candidate.expanduser().resolve())
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        directories.append(resolved)
    if not directories:
        return False
    agent = effective_intercept_agent(payload)
    append_session_log(
        path,
        [
            {
                "kind": "skill_directories",
                "key": "skill_directories",
                "directories": directories,
            },
        ],
        agent=agent,
    )
    return True


def _read_intercept_allow(agent: str) -> str:
    return format_pre_tool_response(agent=agent, permission="allow")


def _read_intercept_outside_skill_dirs(
    payload: dict[str, Any],
    read_path: str,
    agent: str,
) -> str | None:
    directories = skill_directories_from_session_or_payload(payload)
    if is_skill_md_under_directories(read_path, directories):
        return None
    return _read_intercept_allow(agent)


def _read_intercept_local_gates(
    payload: dict[str, Any],
    *,
    read_path: str,
    agent: str,
) -> str | None:
    from cyt_client.transcript import last_user_from_payload

    skill_file = Path(read_path).expanduser()
    if is_excluded_system_skill(skill_file, agent=infer_launch_agent(payload)):
        return _read_intercept_allow(agent)
    if not skill_file.is_file():
        return _read_intercept_allow(agent)

    content_hash = content_sha256_for_file(skill_file)
    key = skill_item_key_for_path(skill_file)
    entries = load_session_entries(payload)
    index = SessionLogIndex.from_entries(entries)
    session_prompt = latest_turn_prompt_from_session(entries)
    transcript_prompt = last_user_from_payload(payload) or ""

    if should_deny_same_turn_preinjected_skill(
        key,
        index,
        session_prompt=session_prompt,
        transcript_prompt=transcript_prompt,
    ):
        return format_pre_tool_response(
            agent=agent,
            permission="deny",
            user_message="Skill already injected for this turn; Read is redundant.",
        )

    mode = resolve_read_intercept_mode(key=key, current_hash=content_hash, index=index)
    if mode == "deny_full_reread":
        return format_pre_tool_response(
            agent=agent,
            permission="deny",
            user_message="Full skill file was already read in this session.",
        )
    if mode == "full":
        _append_full_skill_log_entry(
            payload,
            skill_file=skill_file,
            content_hash=content_hash,
            key=key,
            agent=agent,
        )
        return _read_intercept_allow(agent)
    return None


def _read_intercept_from_daemon(
    payload: dict[str, Any],
    *,
    read_path: str,
    query: str,
    agent: str,
    post_hook_inject: PostHookInject,
) -> str:
    hook_url = resolve_hook_url()
    if hook_url is None:
        return _read_intercept_allow(agent)

    request_payload = build_intercept_request_payload(payload, read_path=read_path, query=query)
    try:
        status, body = post_hook_inject(hook_url, json.dumps(request_payload).encode())
    except OSError:
        return _read_intercept_allow(agent)

    if status >= 400:
        return _read_intercept_allow(agent)

    parsed = parse_agent_interceptor_response(body)
    if parsed is None:
        return _read_intercept_allow(agent)

    if parsed.get("permission") == "deny":
        message = str(parsed.get("user_message") or "Read denied by CYT.")
        return format_pre_tool_response(agent=agent, permission="deny", user_message=message)

    updated_input = parsed.get("updated_input")
    if isinstance(updated_input, dict) and isinstance(updated_input.get("path"), str):
        workspace = _payload_cwd(payload)
        if workspace.is_dir():
            ensure_gitignore_skinny(workspace)
        skill_log_entry = parsed.get("skill_log_entry")
        log_path = session_log_path(payload)
        if log_path is not None and isinstance(skill_log_entry, dict):
            append_session_log(log_path, [skill_log_entry], agent=agent)
        return format_pre_tool_response(
            agent=agent,
            permission="allow",
            updated_input={"path": updated_input["path"]},
        )

    return _read_intercept_allow(agent)


def handle_before_read_file_intercept(payload: dict[str, Any]) -> str:
    """Handle Cursor beforeReadFile: deny same-turn skill re-reads; allow otherwise."""
    if not skills_hook_agent_interceptor_enabled():
        return format_pre_tool_allow()

    read_path = read_path_from_payload(payload)
    if read_path is None:
        return format_pre_tool_allow()

    agent = effective_intercept_agent(payload)
    outside = _read_intercept_outside_skill_dirs(payload, read_path, agent)
    if outside is not None:
        return outside

    local = _read_intercept_local_gates(payload, read_path=read_path, agent=agent)
    if local is not None:
        return local

    return _read_intercept_allow(agent)


def handle_read_intercept(
    payload: dict[str, Any],
    *,
    post_hook_inject: PostHookInject,
) -> str | None:
    """Return preToolUse stdout when intercept handles the Read, else None."""
    if not skills_hook_agent_interceptor_enabled():
        return None

    tool_name, tool_input = extract_read_tool_call(payload)
    if tool_name is None or tool_input is None:
        return None

    agent = effective_intercept_agent(payload)
    if has_partial_read_params(tool_input):
        return _read_intercept_allow(agent)

    read_path = read_path_from_tool_input(tool_input)
    if read_path is None:
        return None

    outside = _read_intercept_outside_skill_dirs(payload, read_path, agent)
    if outside is not None:
        return outside

    local = _read_intercept_local_gates(payload, read_path=read_path, agent=agent)
    if local is not None:
        return local

    entries = load_session_entries(payload)
    query = intercept_query_for_payload(payload, entries)
    if not query.strip():
        return _read_intercept_allow(agent)

    return _read_intercept_from_daemon(
        payload,
        read_path=read_path,
        query=query,
        agent=agent,
        post_hook_inject=post_hook_inject,
    )
