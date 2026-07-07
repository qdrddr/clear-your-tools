"""Interactive wizard for installing cyt-client and hook daemon agent hooks."""

from __future__ import annotations

import copy
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal, cast

from cyt.agents._types import AgentName
from cyt.config import (
    USER_ENV_PATH,
    inject_via,
    load_config,
    load_user_config_overlay,
    required_proxy_env_var_names,
    resolve_setup_config_path,
    save_user_config,
    skills_enabled,
)
from cyt.proxy.setup_wizard import _prompt, _prompt_choice, _prompt_yes_no, parse_path_list
from cyt.tools.hook_setup import prompt_tools_hook_config

CLAUDE_SETTINGS_PATH = Path("~/.claude/settings.json")
CODEX_HOOKS_PATH = Path("~/.codex/hooks.json")
CURSOR_HOOKS_PATH = Path("~/.cursor/hooks.json")
CLAUDE_SKILLS_DIR = Path("~/.claude/skills")
CODEX_SKILLS_DIR = Path("~/.codex/skills")
CURSOR_SKILLS_DIR = Path("~/.cursor/skills")
HOOK_EVENT_NAME = "UserPromptSubmit"
SESSION_START_EVENT = "SessionStart"
CURSOR_BEFORE_SUBMIT_EVENT = "beforeSubmitPrompt"
CURSOR_SESSION_START_EVENT = "sessionStart"
CURSOR_SESSION_END_EVENT = "sessionEnd"
HookAgentName = Literal["claude", "codex", "cursor"]
HOOK_TIMEOUT_SECONDS = 60
SESSION_START_TIMEOUT_SECONDS = 60
USER_PROMPT_TIMEOUT_SECONDS = 60
CYT_HOOK_COMMAND_PREFIX = "cyt hook"
CYT_CLIENT_COMMAND = "cyt-client"
CYT_DAEMON_START_COMMAND = "cyt hook daemon start --unattended"
CYT_DAEMON_START_COMMAND_BASE = "cyt hook daemon start"
HOOK_STDIN_TEST_PAYLOAD: dict[str, Any] = {
    "session_id": "sess-00000000-0000-4000-8000-000000000001",
    "turn_id": "turn-00000000-0000-4000-8000-000000000001",
    "transcript_path": "/Users/you/.codex/sessions/2026/06/12/rollout-example.jsonl",
    "cwd": "/path/to/your/project",
    "hook_event_name": "UserPromptSubmit",
    "model": "example-model",
    "permission_mode": "default",
    "prompt": "say hi",
}


def format_hook_stdin_test_command(*, debug: bool = False) -> str:
    """Return a copy-paste shell snippet that pipes anonymized hook JSON to ``cyt-client``."""
    command = "cyt-client"
    if debug:
        command = f"CYT_HOOK_DEBUG=1 {command}"
    payload_json = json.dumps(HOOK_STDIN_TEST_PAYLOAD, indent=2)
    return "\n".join(
        (
            f"cat <<'EOF' | {command}",
            payload_json,
            "EOF",
        ),
    )


def _print_hook_stdin_test_example(*, debug: bool) -> None:
    print("\nTest the hook locally (UserPromptSubmit payload on stdin) like so:")
    print()
    print(format_hook_stdin_test_command(debug=debug))
    print()
    print("Hook JSON output is written to stdout.")
    if debug:
        print("Set CYT_HOOK_DEBUG=1 to enable extra diagnostics on the hook server.")
    print("\nTo remove installed agent hooks later, run:")
    print("  cyt hook --uninstall")


def cursor_before_submit_entry(*, agent: AgentName = "cursor") -> dict[str, Any]:
    return cyt_client_entry(agent=agent)


def cursor_session_start_cleanup_entry(*, agent: AgentName = "cursor") -> dict[str, Any]:
    return cyt_client_entry(agent=agent)


def cursor_session_start_entry(*, agent: AgentName = "cursor") -> dict[str, Any]:
    return cyt_daemon_start_entry(agent=agent)


def cursor_session_end_entry(*, agent: AgentName = "cursor") -> dict[str, Any]:
    return cyt_client_entry(agent=agent)


def cursor_hook_entries(*, agent: AgentName = "cursor") -> dict[str, dict[str, Any]]:
    return {
        "before_submit": cursor_before_submit_entry(agent=agent),
        "session_start_cleanup": cursor_session_start_cleanup_entry(agent=agent),
        "session_start": cursor_session_start_entry(agent=agent),
        "session_end": cursor_session_end_entry(agent=agent),
    }


def cursor_desired_hook_commands(*, agent: AgentName = "cursor") -> list[str]:
    entries = cursor_hook_entries(agent=agent)
    return [
        str(entries["before_submit"].get("command")),
        str(entries["session_start_cleanup"].get("command")),
        str(entries["session_start"].get("command")),
        str(entries["session_end"].get("command")),
    ]


def normalize_cursor_hooks_section(hooks_section: object) -> dict[str, Any]:
    """Return a Cursor-native hooks map (event name -> list of flat hook entries)."""
    if not isinstance(hooks_section, dict):
        return {}
    section = cast(dict[str, Any], hooks_section)
    normalized: dict[str, Any] = {}
    for event_name, event_entries in section.items():
        if not isinstance(event_name, str) or not isinstance(event_entries, list):
            continue
        kept: list[Any] = []
        for entry in event_entries:
            if not isinstance(entry, dict):
                continue
            command = entry.get("command")
            if isinstance(command, str):
                kept.append(copy.deepcopy(entry))
        if kept:
            normalized[event_name] = kept
    return normalized


def merge_skills_directory_lists(
    existing: list[str],
    new_dirs: list[str],
) -> tuple[list[str], bool]:
    """Append skill directory paths from *new_dirs* when not already present."""
    merged = [str(path) for path in existing if str(path).strip()]
    seen = {str(Path(path).expanduser()) for path in merged}
    changed = False
    for raw in new_dirs:
        text = str(raw).strip()
        if not text:
            continue
        expanded = str(Path(text).expanduser())
        if expanded in seen:
            continue
        merged.append(text)
        seen.add(expanded)
        changed = True
    return merged, changed


def default_hook_skills_directories(
    skills_cfg: dict[str, Any],
    *,
    include_claude: bool,
    include_codex: bool,
    include_cursor: bool = False,
) -> list[str]:
    raw = skills_cfg.get("directories")
    if isinstance(raw, list) and raw:
        return [str(path) for path in raw if str(path).strip()]

    defaults: list[str] = []
    if include_claude:
        defaults.append(str(CLAUDE_SKILLS_DIR))
    if include_codex:
        defaults.append(str(CODEX_SKILLS_DIR))
    if include_cursor:
        defaults.append(str(CURSOR_SKILLS_DIR))
    return defaults


def _prompt_hook_skills_directories(
    skills_cfg: dict[str, Any],
    *,
    include_claude: bool,
    include_codex: bool,
    include_cursor: bool = False,
) -> list[str]:
    default_dirs = default_hook_skills_directories(
        skills_cfg,
        include_claude=include_claude,
        include_codex=include_codex,
        include_cursor=include_cursor,
    )
    default_str = ", ".join(default_dirs)
    while True:
        raw = _prompt("Skills directories (comma-separated paths)", default_str)
        parsed = parse_path_list(raw)
        if parsed is not None:
            return parsed
        if default_str:
            return default_dirs
        print("Enter at least one directory path.", file=sys.stderr)


def _ensure_skill_directories_exist(directories: list[str]) -> None:
    for raw in directories:
        path = Path(raw).expanduser()
        path.mkdir(parents=True, exist_ok=True)


def build_hook_skills_config_overlay(
    existing_skills: dict[str, Any],
    directories: list[str],
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build a skills overlay for hook mode, or ``None`` when no config write is needed."""
    existing_dirs = existing_skills.get("directories")
    if not isinstance(existing_dirs, list):
        existing_dirs = []

    merged_dirs, dirs_changed = merge_skills_directory_lists(existing_dirs, directories)
    enabled_ok = existing_skills.get("enabled") is True
    inject_ok = inject_via(config or {}) == "hook"

    if enabled_ok and inject_ok and not dirs_changed:
        return None

    return {
        "pruning": {"inject_via": "hook"},
        "skills": {
            "enabled": True,
            "directories": merged_dirs,
        },
    }


def _save_hook_skills_directories(
    config_path: Path,
    directories: list[str],
    *,
    user_overlay: dict[str, Any],
) -> bool:
    skills_cfg = user_overlay.get("skills")
    existing_skills = skills_cfg if isinstance(skills_cfg, dict) else {}
    overlay = build_hook_skills_config_overlay(
        existing_skills,
        directories,
        config=load_config(config_path),
    )
    if overlay is None:
        return False
    return save_user_config(config_path, overlay, apply_bundled_sections=False)


def _configure_hook_skills_directories(
    *,
    config_path: Path,
    include_claude: bool,
    include_codex: bool,
    include_cursor: bool = False,
) -> None:
    user_overlay = load_user_config_overlay(config_path)
    skills_cfg = user_overlay.get("skills")
    skills_cfg = skills_cfg if isinstance(skills_cfg, dict) else {}

    print("\n--- Skills directories ---")
    directories = _prompt_hook_skills_directories(
        skills_cfg,
        include_claude=include_claude,
        include_codex=include_codex,
        include_cursor=include_cursor,
    )
    _ensure_skill_directories_exist(directories)
    if _save_hook_skills_directories(config_path, directories, user_overlay=user_overlay):
        print(f"Updated skills config in {config_path} (enabled, inject_via: hook, directories)")
    else:
        print(f"Skills config already set for hook mode in {config_path}")


def cyt_client_entry(*, agent: AgentName | None = None) -> dict[str, Any]:
    command = CYT_CLIENT_COMMAND
    if agent is not None:
        from cyt.skills.agents import CYT_LAUNCH_AGENT_ENV

        command = f"{CYT_LAUNCH_AGENT_ENV}={agent} {command}"
    return {"type": "command", "command": command, "timeout": USER_PROMPT_TIMEOUT_SECONDS}


def cyt_daemon_start_entry(*, agent: AgentName | None = None) -> dict[str, Any]:
    command = CYT_DAEMON_START_COMMAND
    if agent is not None:
        from cyt.skills.agents import CYT_LAUNCH_AGENT_ENV

        command = f"{CYT_LAUNCH_AGENT_ENV}={agent} {command}"
    return {"type": "command", "command": command, "timeout": SESSION_START_TIMEOUT_SECONDS}


def cyt_hook_entry(*, debug: bool = False, agent: AgentName | None = None) -> dict[str, Any]:
    """Back-compat alias; prefer :func:`cyt_client_entry`."""
    del debug
    return cyt_client_entry(agent=agent)


def _is_legacy_cyt_hook_stdin_command(command: str) -> bool:
    normalized = f" {command.strip()} "
    return (
        f" {CYT_HOOK_COMMAND_PREFIX} ".casefold() in normalized.casefold()
        and " --stdin" in normalized
    )


def _is_cyt_hook_command(command: object) -> bool:
    if not isinstance(command, str):
        return False
    normalized = command.strip()
    if normalized == CYT_CLIENT_COMMAND or normalized.endswith(f" {CYT_CLIENT_COMMAND}"):
        return True
    if CYT_DAEMON_START_COMMAND in normalized or CYT_DAEMON_START_COMMAND_BASE in normalized:
        return True
    if normalized.startswith("cyt skills"):
        return True
    lowered = normalized.casefold()
    # Legacy Cursor wrapper scripts from older cyt hook cursor installs.
    if "cursor-hook-bridge" in lowered or "cyt-skills.sh" in lowered:
        return True
    if "daemon-start.sh" in lowered:
        return True
    # Agent hooks prefix the command with CYT_LAUNCH_AGENT=...
    return f" {normalized} ".casefold().find(f" {CYT_HOOK_COMMAND_PREFIX} ") >= 0


def _collect_cyt_hook_commands(hooks_section: object) -> list[str]:
    """Return all configured CYT hook command strings."""
    if not isinstance(hooks_section, dict):
        return []
    section = cast(dict[str, Any], hooks_section)
    return [
        command
        for command in _iter_hook_commands(section)
        if isinstance(command, str) and _is_cyt_hook_command(command)
    ]


def _cyt_hook_has_debug_flag(command: str) -> bool:
    normalized = f" {command.strip()} "
    return " --debug " in normalized or normalized.rstrip().endswith(" --debug")


def _format_existing_hook_status(commands: list[str]) -> str:
    if len(commands) > 1:
        debug_bits = {_cyt_hook_has_debug_flag(command) for command in commands}
        if len(debug_bits) == 1:
            debug_label = "with --debug" if debug_bits.pop() else "without --debug"
            return f"found {len(commands)} duplicate CYT hooks ({debug_label})"
        return f"found {len(commands)} duplicate CYT hooks (mixed debug settings)"
    command = commands[0]
    debug_label = "with --debug" if _cyt_hook_has_debug_flag(command) else "without --debug"
    return f"CYT hook already configured ({debug_label})"


def _cyt_hook_needs_update(existing_commands: list[str], entry: dict[str, Any]) -> bool:
    desired = entry.get("command")
    if not isinstance(desired, str):
        return True
    if len(existing_commands) != 1:
        return True
    existing = existing_commands[0]
    if existing.strip().startswith("cyt skills"):
        return False
    if _is_legacy_cyt_hook_stdin_command(existing):
        return True
    return existing != desired


def _iter_hook_commands(hooks_section: dict[str, Any]) -> Iterator[object]:
    for event_entries in hooks_section.values():
        if not isinstance(event_entries, list):
            continue
        for wrapper in event_entries:
            if not isinstance(wrapper, dict):
                continue
            inner = wrapper.get("hooks")
            if isinstance(inner, list):
                for hook in inner:
                    if isinstance(hook, dict):
                        yield hook.get("command")
                continue
            command = wrapper.get("command")
            if command is not None:
                yield command


def _append_flat_hook_entry(
    hooks_section: dict[str, Any],
    entry: dict[str, Any],
    *,
    event_name: str,
) -> dict[str, Any]:
    merged = copy.deepcopy(hooks_section) if hooks_section else {}
    entries = merged.setdefault(event_name, [])
    if not isinstance(entries, list):
        entries = []
        merged[event_name] = entries
    entries.append(copy.deepcopy(entry))
    return merged


def _collect_cyt_hook_commands_for_flat_event(
    hooks_section: dict[str, Any],
    event_name: str,
) -> list[str]:
    event_entries = hooks_section.get(event_name)
    if not isinstance(event_entries, list):
        return []
    commands: list[str] = []
    for entry in event_entries:
        if not isinstance(entry, dict):
            continue
        command = entry.get("command")
        if isinstance(command, str) and _is_cyt_hook_command(command):
            commands.append(command)
    return commands


def _flat_event_cyt_commands_match(
    existing_commands: list[str],
    desired_commands: list[str],
) -> bool:
    return existing_commands == desired_commands


def _upsert_cursor_flat_event(
    hooks_section: dict[str, Any],
    event_name: str,
    entries: list[dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    merged = copy.deepcopy(hooks_section) if hooks_section else {}
    existing = _collect_cyt_hook_commands_for_flat_event(merged, event_name)
    desired = [command for entry in entries if isinstance((command := entry.get("command")), str)]
    if _flat_event_cyt_commands_match(existing, desired):
        return merged, False

    merged, _ = _remove_cyt_hooks_for_event(merged, event_name)
    for entry in entries:
        merged = _append_flat_hook_entry(merged, entry, event_name=event_name)
    return merged, True


def upsert_cursor_hooks(
    hooks_section: dict[str, Any],
    *,
    before_submit_entry: dict[str, Any],
    session_start_cleanup_entry: dict[str, Any],
    session_start_entry: dict[str, Any],
    session_end_entry: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    merged = copy.deepcopy(hooks_section) if hooks_section else {}
    changed = False

    for event_name, entries in (
        (CURSOR_BEFORE_SUBMIT_EVENT, [before_submit_entry]),
        (CURSOR_SESSION_START_EVENT, [session_start_cleanup_entry, session_start_entry]),
        (CURSOR_SESSION_END_EVENT, [session_end_entry]),
    ):
        merged, event_changed = _upsert_cursor_flat_event(merged, event_name, entries)
        changed = changed or event_changed

    return merged, changed


def upsert_cursor_hooks_into_file(
    path: Path,
    *,
    before_submit_entry: dict[str, Any],
    session_start_cleanup_entry: dict[str, Any],
    session_start_entry: dict[str, Any],
    session_end_entry: dict[str, Any],
) -> bool:
    existing = _load_json_object(path)
    hooks_section = normalize_cursor_hooks_section(existing.get("hooks"))
    merged_hooks, changed = upsert_cursor_hooks(
        hooks_section,
        before_submit_entry=before_submit_entry,
        session_start_cleanup_entry=session_start_cleanup_entry,
        session_start_entry=session_start_entry,
        session_end_entry=session_end_entry,
    )
    if not changed:
        return False

    if merged_hooks:
        existing["hooks"] = merged_hooks
    else:
        existing.pop("hooks", None)
    if "version" not in existing:
        existing["version"] = 1
    _write_json_object(path, existing)
    return True


def cyt_hook_command_exists(hooks_section: object) -> bool:
    """Return True when a CYT hook command is already configured."""
    if not isinstance(hooks_section, dict):
        return False
    section = cast(dict[str, Any], hooks_section)
    return any(_is_cyt_hook_command(command) for command in _iter_hook_commands(section))


def _append_hook_entry(
    hooks_section: dict[str, Any],
    entry: dict[str, Any],
    *,
    event_name: str = HOOK_EVENT_NAME,
) -> dict[str, Any]:
    merged = copy.deepcopy(hooks_section) if hooks_section else {}

    wrappers = merged.setdefault(event_name, [])
    if not isinstance(wrappers, list):
        wrappers = []
        merged[event_name] = wrappers

    target: dict[str, Any] | None = None
    for wrapper in wrappers:
        if isinstance(wrapper, dict) and isinstance(wrapper.get("hooks"), list):
            target = wrapper
            break
    if target is None:
        target = {"hooks": []}
        wrappers.append(target)

    inner = target["hooks"]
    assert isinstance(inner, list)
    inner.append(copy.deepcopy(entry))
    return merged


def _collect_cyt_hook_commands_for_event(
    hooks_section: dict[str, Any],
    event_name: str,
) -> list[str]:
    event_entries = hooks_section.get(event_name)
    if not isinstance(event_entries, list):
        return []
    return [
        command
        for command in _iter_hook_commands({event_name: event_entries})
        if isinstance(command, str) and _is_cyt_hook_command(command)
    ]


def _event_has_cyt_hook(hooks_section: dict[str, Any], event_name: str) -> bool:
    return bool(_collect_cyt_hook_commands_for_event(hooks_section, event_name))


def merge_cyt_hook(
    hooks_section: dict[str, Any],
    entry: dict[str, Any],
    *,
    event_name: str = HOOK_EVENT_NAME,
) -> tuple[dict[str, Any], bool]:
    """Append *entry* to *event_name* unless a current CYT hook already exists there."""
    merged = copy.deepcopy(hooks_section) if hooks_section else {}
    existing = _collect_cyt_hook_commands_for_event(merged, event_name)
    if not _cyt_hook_needs_update(existing, entry):
        return merged, False
    if existing:
        merged, _ = _remove_cyt_hooks_for_event(merged, event_name)
    return _append_hook_entry(merged, entry, event_name=event_name), True


def upsert_cyt_hook(
    hooks_section: dict[str, Any],
    entry: dict[str, Any],
    *,
    event_name: str = HOOK_EVENT_NAME,
) -> tuple[dict[str, Any], bool]:
    """Ensure exactly one CYT hook exists and matches *entry* for *event_name*."""
    merged = copy.deepcopy(hooks_section) if hooks_section else {}
    existing = _collect_cyt_hook_commands_for_event(merged, event_name)
    if not _cyt_hook_needs_update(existing, entry):
        return merged, False

    merged, _ = _remove_cyt_hooks_for_event(merged, event_name)
    merged = _append_hook_entry(merged, entry, event_name=event_name)
    return merged, True


def merge_cyt_hooks(
    hooks_section: dict[str, Any],
    *,
    user_prompt_entry: dict[str, Any],
    session_start_entry: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    merged, changed_user = merge_cyt_hook(
        hooks_section,
        user_prompt_entry,
        event_name=HOOK_EVENT_NAME,
    )
    merged, changed_session = merge_cyt_hook(
        merged,
        session_start_entry,
        event_name=SESSION_START_EVENT,
    )
    return merged, changed_user or changed_session


def upsert_cyt_hooks(
    hooks_section: dict[str, Any],
    *,
    user_prompt_entry: dict[str, Any],
    session_start_entry: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    merged, changed_user = upsert_cyt_hook(
        hooks_section,
        user_prompt_entry,
        event_name=HOOK_EVENT_NAME,
    )
    merged, changed_session = upsert_cyt_hook(
        merged,
        session_start_entry,
        event_name=SESSION_START_EVENT,
    )
    return merged, changed_user or changed_session


def remove_cyt_hooks(hooks_section: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Remove CYT hook commands from all events in a hooks section."""
    merged = copy.deepcopy(hooks_section)
    changed = False
    for event_name in list(merged):
        updated, event_changed = _remove_cyt_hooks_for_event(merged, event_name)
        merged = updated
        changed = changed or event_changed
    return merged, changed


def _filter_wrapper_inner_hooks(
    wrapper: dict[str, Any],
    inner: list[Any],
) -> tuple[dict[str, Any] | None, bool]:
    filtered = [
        hook
        for hook in inner
        if not (isinstance(hook, dict) and _is_cyt_hook_command(hook.get("command")))
    ]
    if not filtered:
        return None, len(filtered) != len(inner)
    if len(filtered) == len(inner):
        return copy.deepcopy(wrapper), False
    kept_wrapper = copy.deepcopy(wrapper)
    kept_wrapper["hooks"] = filtered
    return kept_wrapper, True


def _remove_cyt_hooks_for_event(
    hooks_section: dict[str, Any],
    event_name: str,
) -> tuple[dict[str, Any], bool]:
    merged = copy.deepcopy(hooks_section)
    changed = False
    event_entries = merged.get(event_name)
    if not isinstance(event_entries, list):
        return merged, False

    kept_wrappers: list[Any] = []
    for wrapper in event_entries:
        if not isinstance(wrapper, dict):
            kept_wrappers.append(wrapper)
            continue

        inner = wrapper.get("hooks")
        if isinstance(inner, list):
            kept_wrapper, wrapper_changed = _filter_wrapper_inner_hooks(wrapper, inner)
            changed = changed or wrapper_changed
            if kept_wrapper is not None:
                kept_wrappers.append(kept_wrapper)
            continue

        if _is_cyt_hook_command(wrapper.get("command")):
            changed = True
            continue

        kept_wrappers.append(copy.deepcopy(wrapper))

    if kept_wrappers != event_entries:
        changed = True
    if kept_wrappers:
        merged[event_name] = kept_wrappers
    elif event_name in merged:
        del merged[event_name]
        changed = True

    return merged, changed


def uninstall_hooks_from_file(
    path: Path,
    *,
    hooks_key: str = "hooks",
) -> bool:
    """Remove CYT hooks from *path*; return True when the file changed."""
    if not path.is_file():
        return False

    existing = _load_json_object(path)
    hooks_section = existing.get(hooks_key)
    if not isinstance(hooks_section, dict) or not cyt_hook_command_exists(hooks_section):
        return False

    merged_hooks, changed = remove_cyt_hooks(hooks_section)
    if not changed:
        return False

    if merged_hooks:
        existing[hooks_key] = merged_hooks
    else:
        existing.pop(hooks_key, None)

    _write_json_object(path, existing)
    return True


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _write_json_object(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _save_hooks_section_to_file(
    path: Path,
    hooks_section: dict[str, Any],
    *,
    hooks_key: str = "hooks",
) -> None:
    existing = _load_json_object(path)
    if hooks_section:
        existing[hooks_key] = hooks_section
    else:
        existing.pop(hooks_key, None)
    _write_json_object(path, existing)


def merge_hooks_into_file(
    path: Path,
    entry: dict[str, Any],
    *,
    hooks_key: str = "hooks",
) -> bool:
    """Merge CYT hook into *path*; return True when the file changed."""
    existing = _load_json_object(path)
    hooks_section = existing.get(hooks_key)
    if not isinstance(hooks_section, dict):
        hooks_section = {}
    merged_hooks, changed = merge_cyt_hook(hooks_section, entry)
    if not changed:
        return False
    _save_hooks_section_to_file(path, merged_hooks, hooks_key=hooks_key)
    return True


def upsert_hooks_into_file(
    path: Path,
    entry: dict[str, Any],
    *,
    hooks_key: str = "hooks",
    event_name: str = HOOK_EVENT_NAME,
) -> bool:
    """Replace CYT hooks in *path* with a single *entry*; return True when changed."""
    existing = _load_json_object(path)
    hooks_section = existing.get(hooks_key)
    if not isinstance(hooks_section, dict):
        hooks_section = {}
    merged_hooks, changed = upsert_cyt_hook(hooks_section, entry, event_name=event_name)
    if not changed:
        return False
    _save_hooks_section_to_file(path, merged_hooks, hooks_key=hooks_key)
    return True


def upsert_all_hooks_into_file(
    path: Path,
    *,
    user_prompt_entry: dict[str, Any],
    session_start_entry: dict[str, Any],
    hooks_key: str = "hooks",
) -> bool:
    existing = _load_json_object(path)
    hooks_section = existing.get(hooks_key)
    if not isinstance(hooks_section, dict):
        hooks_section = {}
    merged_hooks, changed = upsert_cyt_hooks(
        hooks_section,
        user_prompt_entry=user_prompt_entry,
        session_start_entry=session_start_entry,
    )
    if not changed:
        return False
    _save_hooks_section_to_file(path, merged_hooks, hooks_key=hooks_key)
    return True


def _agent_config_path(path: Path) -> Path:
    return path.expanduser()


def _hook_agent_ready(agent: HookAgentName, path: Path) -> bool:
    if agent == "cursor":
        return True
    resolved = path.expanduser()
    if resolved.is_file():
        return True
    return resolved.parent.is_dir()


def _agent_config_ready(path: Path) -> bool:
    """Back-compat helper for Claude/Codex paths."""
    resolved = path.expanduser()
    if resolved.is_file():
        return True
    return resolved.parent.is_dir()


def _agent_hook_label(agent: HookAgentName) -> str:
    return {
        "claude": "Claude Code",
        "codex": "Codex",
        "cursor": "Cursor",
    }[agent]


def _agent_hook_path(agent: HookAgentName) -> Path:
    return {
        "claude": _agent_config_path(CLAUDE_SETTINGS_PATH),
        "codex": _agent_config_path(CODEX_HOOKS_PATH),
        "cursor": _agent_config_path(CURSOR_HOOKS_PATH),
    }[agent]


def _ensure_hook_credentials(config: dict[str, Any]) -> None:
    from cyt.launch.secrets import (
        ensure_wizard_credentials,
        inspect_named_credentials,
        preload_keyring_credentials,
    )

    names = required_proxy_env_var_names(config)
    if not names:
        print("Hook credentials: none required for the current pipeline.")
        return

    preload_keyring_credentials(names)

    print("Checking required API keys:")
    before_sources = dict(inspect_named_credentials(names, allow_prompt=False))
    for name in names:
        source = before_sources.get(name)
        if source:
            print(f"  {name}: {source}")
        else:
            print(f"  {name}: missing")

    missing_before = [name for name in names if not before_sources.get(name)]
    if not missing_before and all(
        source == "keyring" for source in before_sources.values() if source
    ):
        print("All required keys are already available.")
        return

    if missing_before and not sys.stdin.isatty():
        vars_block = "\n".join(f"\t{name}" for name in missing_before)
        raise SystemExit(
            f"Required environment variable(s) not set:\n{vars_block}\n"
            f"Run `cyt hook all` interactively or define them in {USER_ENV_PATH}.",
        )

    sources = ensure_wizard_credentials(names, env_fallback_path=USER_ENV_PATH)
    persisted = [
        name for name in names if sources.get(name) and sources[name] != before_sources.get(name)
    ]
    if persisted:
        print("Updated credentials:")
        for name in persisted:
            print(f"  {name}: {sources[name]}")


def _save_tools_hook_wizard_config(
    resolved_config_path: Path,
    config: dict[str, Any],
    *,
    config_path: Path | None,
) -> dict[str, Any]:
    tools_overlay = prompt_tools_hook_config(config, context="hook")
    if save_user_config(
        resolved_config_path,
        {"pruning": {"inject_via": "hook", "tools": tools_overlay}},
        apply_bundled_sections=False,
    ):
        print(f"Saved tools hook settings to {resolved_config_path}")
        return load_config(config_path)
    return config


def _install_nested_hooks_for_targets(
    targets: list[tuple[str, Path, AgentName]],
    *,
    debug: bool,
) -> bool:
    any_changed = False
    action_choices = ("update", "remove", "skip")
    for label, path, agent in targets:
        user_prompt_entry = cyt_client_entry(agent=agent)
        session_start_entry = cyt_daemon_start_entry(agent=agent)
        hooks_data = _load_json_object(path)
        hooks_section = hooks_data.get("hooks")
        if not isinstance(hooks_section, dict):
            hooks_section = {}

        existing_commands = _collect_cyt_hook_commands(hooks_section)
        desired_commands = {
            str(user_prompt_entry.get("command")),
            str(session_start_entry.get("command")),
        }
        needs_update = set(existing_commands) != desired_commands or len(existing_commands) != 2
        if existing_commands:
            print(f"{label}: {_format_existing_hook_status(existing_commands)} in {path}")
            default_action = "update" if needs_update else "skip"
            action = _prompt_choice(
                f"{label}: existing CYT hook — choose action (update | remove | skip)",
                list(action_choices),
                default_index=action_choices.index(default_action),
            )
            if action == "skip":
                print(f"{label}: kept existing hook")
                continue
            if action == "remove":
                merged_hooks, changed = remove_cyt_hooks(hooks_section)
                if changed:
                    _save_hooks_section_to_file(path, merged_hooks)
                    print(f"{label}: removed CYT hook from {path}")
                    any_changed = True
                else:
                    print(f"{label}: no CYT hook to remove in {path}")
                continue

            if upsert_all_hooks_into_file(
                path,
                user_prompt_entry=user_prompt_entry,
                session_start_entry=session_start_entry,
            ):
                print(f"{label}: updated CYT hooks in {path}")
                any_changed = True
            else:
                print(f"{label}: CYT hooks already match selected settings")
            continue

        if not _prompt_yes_no(f"Install CYT hooks for {label}?", default_yes=True):
            print(f"{label}: skipped")
            continue
        if upsert_all_hooks_into_file(
            path,
            user_prompt_entry=user_prompt_entry,
            session_start_entry=session_start_entry,
        ):
            print(f"{label}: added CYT hooks to {path}")
            any_changed = True
        else:
            print(f"{label}: CYT hooks already configured in {path}")
    return any_changed


def _handle_existing_cursor_hooks(
    label: str,
    path: Path,
    hooks_section: dict[str, Any],
    *,
    needs_update: bool,
    before_submit_entry: dict[str, Any],
    session_start_cleanup_entry: dict[str, Any],
    session_start_entry: dict[str, Any],
    session_end_entry: dict[str, Any],
) -> bool:
    """Prompt for update/remove/skip when CYT hooks already exist; return whether file changed."""
    action_choices = ("update", "remove", "skip")
    default_action = "update" if needs_update else "skip"
    action = _prompt_choice(
        f"{label}: existing CYT hook — choose action (update | remove | skip)",
        list(action_choices),
        default_index=action_choices.index(default_action),
    )
    if action == "skip":
        print(f"{label}: kept existing hook")
        return False
    if action == "remove":
        merged_hooks, changed = remove_cyt_hooks(hooks_section)
        if changed:
            existing = _load_json_object(path)
            if merged_hooks:
                existing["hooks"] = merged_hooks
            else:
                existing.pop("hooks", None)
            if "version" not in existing and (merged_hooks or existing):
                existing["version"] = 1
            _write_json_object(path, existing)
            print(f"{label}: removed CYT hook from {path}")
            return True
        print(f"{label}: no CYT hook to remove in {path}")
        return False

    if upsert_cursor_hooks_into_file(
        path,
        before_submit_entry=before_submit_entry,
        session_start_cleanup_entry=session_start_cleanup_entry,
        session_start_entry=session_start_entry,
        session_end_entry=session_end_entry,
    ):
        print(f"{label}: updated CYT hooks in {path}")
        return True
    print(f"{label}: CYT hooks already match selected settings")
    return False


def _install_cursor_hooks_for_target(
    label: str,
    path: Path,
    *,
    debug: bool,
) -> bool:
    del debug
    entries = cursor_hook_entries(agent="cursor")
    before_submit_entry = entries["before_submit"]
    session_start_cleanup_entry = entries["session_start_cleanup"]
    session_start_entry = entries["session_start"]
    session_end_entry = entries["session_end"]
    hooks_data = _load_json_object(path)
    hooks_section = hooks_data.get("hooks")
    if not isinstance(hooks_section, dict):
        hooks_section = {}

    existing_commands = _collect_cyt_hook_commands(hooks_section)
    desired_commands = cursor_desired_hook_commands(agent="cursor")
    needs_update = set(existing_commands) != set(desired_commands) or len(existing_commands) != len(
        desired_commands,
    )

    if existing_commands:
        print(f"{label}: {_format_existing_hook_status(existing_commands)} in {path}")
        return _handle_existing_cursor_hooks(
            label,
            path,
            hooks_section,
            needs_update=needs_update,
            before_submit_entry=before_submit_entry,
            session_start_cleanup_entry=session_start_cleanup_entry,
            session_start_entry=session_start_entry,
            session_end_entry=session_end_entry,
        )

    if not _prompt_yes_no(f"Install CYT hooks for {label}?", default_yes=True):
        print(f"{label}: skipped")
        return False
    if upsert_cursor_hooks_into_file(
        path,
        before_submit_entry=before_submit_entry,
        session_start_cleanup_entry=session_start_cleanup_entry,
        session_start_entry=session_start_entry,
        session_end_entry=session_end_entry,
    ):
        print(f"{label}: added CYT hooks to {path}")
        return True
    print(f"{label}: CYT hooks already configured in {path}")
    return False


def _resolve_hook_setup_agents(
    agents: list[HookAgentName] | None,
) -> list[HookAgentName]:
    if agents is None:
        return ["claude", "codex", "cursor"]
    return list(dict.fromkeys(agents))


def _collect_hook_setup_targets(
    selected_agents: list[HookAgentName],
) -> tuple[
    list[tuple[str, Path, AgentName]],
    list[tuple[str, Path]],
    bool,
    bool,
    bool,
]:
    nested_targets: list[tuple[str, Path, AgentName]] = []
    cursor_targets: list[tuple[str, Path]] = []
    include_claude = False
    include_codex = False
    include_cursor = False

    for agent in selected_agents:
        path = _agent_hook_path(agent)
        label = _agent_hook_label(agent)
        if not _hook_agent_ready(agent, path):
            message = f"Skipping {label} ({path}): config file not found."
            if len(selected_agents) == 1:
                raise SystemExit(
                    f"No agent config found for {label}; create {path} first.",
                )
            print(message)
            continue

        if agent == "cursor":
            cursor_targets.append((label, path))
            include_cursor = True
        elif agent == "claude":
            nested_targets.append((label, path, "claude"))
            include_claude = True
        else:
            nested_targets.append((label, path, "codex"))
            include_codex = True

    return nested_targets, cursor_targets, include_claude, include_codex, include_cursor


def run_hook_setup(
    *,
    config_path: Path | None = None,
    agents: list[HookAgentName] | None = None,
) -> None:
    """Install CYT agent hooks and ensure runtime credentials."""
    selected_agents = _resolve_hook_setup_agents(agents)
    resolved_config_path = resolve_setup_config_path(config_path)
    config = load_config(config_path)
    if len(selected_agents) == 1:
        print(f"CYT hook setup ({selected_agents[0]})\n")
    else:
        print("CYT hook setup\n")

    if not skills_enabled(config):
        print(
            "Note: skills.enabled is false in config; hooks will not inject skills until enabled.",
            file=sys.stderr,
        )

    _ensure_hook_credentials(config)

    config = _save_tools_hook_wizard_config(
        resolved_config_path,
        config,
        config_path=config_path,
    )

    nested_targets, cursor_targets, include_claude, include_codex, include_cursor = (
        _collect_hook_setup_targets(selected_agents)
    )

    if not nested_targets and not cursor_targets:
        raise SystemExit("No agent config files found for the selected hook targets.")

    _configure_hook_skills_directories(
        config_path=resolved_config_path,
        include_claude=include_claude,
        include_codex=include_codex,
        include_cursor=include_cursor,
    )

    debug = _prompt_yes_no("Enable hook debug logging (--debug)?", default_yes=False)

    any_changed = False
    if nested_targets:
        any_changed = _install_nested_hooks_for_targets(nested_targets, debug=debug) or any_changed
    for label, path in cursor_targets:
        any_changed = _install_cursor_hooks_for_target(label, path, debug=debug) or any_changed

    if any_changed:
        print("\nRestart your agent so hook changes take effect.")
    else:
        print("\nNo hook files were modified.")

    _print_hook_stdin_test_example(debug=debug)


def run_hook_uninstall() -> None:
    """Remove CYT agent hooks from Claude, Codex, and Cursor config files."""
    print("CYT hook uninstall\n")

    targets = [
        ("Claude Code", _agent_config_path(CLAUDE_SETTINGS_PATH)),
        ("Codex", _agent_config_path(CODEX_HOOKS_PATH)),
        ("Cursor", _agent_config_path(CURSOR_HOOKS_PATH)),
    ]

    any_changed = False
    for label, path in targets:
        if not path.is_file():
            print(f"{label}: skipped ({path} not found)")
            continue
        if uninstall_hooks_from_file(path):
            print(f"{label}: removed CYT hook from {path}")
            any_changed = True
        else:
            print(f"{label}: no CYT hook in {path}")

    if any_changed:
        print("\nRestart your agent so hook changes take effect.")
    else:
        print("\nNo hook files were modified.")
