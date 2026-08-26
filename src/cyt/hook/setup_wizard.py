"""Interactive wizard for installing cyt-client and hook daemon agent hooks."""

from __future__ import annotations

import copy
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from cyt.agents._types import CYT_LAUNCH_AGENT_ENV, AgentName
from cyt.cloudflare.readiness import report_cloudflare_hook_readiness
from cyt.config import (
    DEFAULT_INJECT_VIA_BY_AGENT,
    inject_via_for_agent,
    load_config,
    load_user_config_overlay,
    resolve_setup_config_path,
    save_user_config,
    skills_enabled,
    skills_hook_agent_interceptor_enabled,
    tools_enabled,
    tools_hook_sources,
)
from cyt.cyt_mcp.readiness import report_cyt_mcp_hook_readiness
from cyt.hook.cli_invocation import (
    INSTALLED_CYT_CLIENT_COMMAND,
    INSTALLED_CYT_DAEMON_START_COMMAND,
    INSTALLED_CYT_DAEMON_START_COMMAND_BASE,
    HookCliInvocation,
    cursor_hook_client_command,
    cursor_hook_daemon_start_command,
    cyt_client_command,
    cyt_daemon_restart_command,
    cyt_daemon_start_command,
    detect_hook_cli_invocation,
    is_dev_cyt_hook_command,
    is_windows_hook_wrapper_command,
    prefix_command_env,
    remove_windows_hook_wrappers,
)
from cyt.launch.inject_via_prompt import ensure_hook_inject_via
from cyt.mcpc.readiness import report_mcpc_hook_readiness
from cyt.proxy.setup_wizard import _prompt, _prompt_choice, _prompt_yes_no, parse_path_list
from cyt.tools.hook_setup import prompt_tools_hook_config
from cyt_client.hook_invocation import CURSOR_POST_TOOL_MATCHER

CLAUDE_SETTINGS_PATH = Path("~/.claude/settings.json")
CODEX_HOOKS_PATH = Path("~/.codex/hooks.json")
CURSOR_HOOKS_PATH = Path("~/.cursor/hooks.json")
CLAUDE_SKILLS_DIR = Path("~/.claude/skills")
CODEX_SKILLS_DIR = Path("~/.codex/skills")
CURSOR_SKILLS_DIR = Path("~/.cursor/skills")
HOOK_EVENT_NAME = "UserPromptSubmit"
SESSION_START_EVENT = "SessionStart"
SESSION_END_EVENT = "SessionEnd"
CURSOR_BEFORE_SUBMIT_EVENT = "beforeSubmitPrompt"
CURSOR_SESSION_START_EVENT = "sessionStart"
CURSOR_SESSION_END_EVENT = "sessionEnd"
CURSOR_PRE_TOOL_EVENT = "preToolUse"
CURSOR_BEFORE_READ_FILE_EVENT = "beforeReadFile"
CURSOR_POST_TOOL_EVENT = "postToolUse"
PRE_COMPACT_EVENT = "PreCompact"
CURSOR_PRE_COMPACT_EVENT = "preCompact"
# Legacy Cursor MCP-only hook events removed in favor of preToolUse/postToolUse.
_LEGACY_CURSOR_TOOL_HOOK_EVENTS = ("beforeMCPExecution", "afterMCPExecution")
POST_TOOL_USE_EVENT = "PostToolUse"
POST_TOOL_USE_MATCHER = "mcp__cyt-mcp__get-tool-definitions"
PRE_TOOL_USE_EVENT = "PreToolUse"
PRE_TOOL_USE_MATCHER = "mcp__*"
PRE_TOOL_USE_READ_MATCHER = "Read"
CURSOR_PRE_TOOL_READ_MATCHER = PRE_TOOL_USE_READ_MATCHER
HookAgentName = Literal["claude", "codex", "cursor"]
HOOK_TIMEOUT_SECONDS = 60
SESSION_START_TIMEOUT_SECONDS = 60
USER_PROMPT_TIMEOUT_SECONDS = 60
CYT_HOOK_COMMAND_PREFIX = "cyt hook"
CYT_CLIENT_COMMAND = INSTALLED_CYT_CLIENT_COMMAND
CYT_DAEMON_START_COMMAND = INSTALLED_CYT_DAEMON_START_COMMAND
CYT_DAEMON_START_COMMAND_BASE = INSTALLED_CYT_DAEMON_START_COMMAND_BASE
HOOK_STDIN_TEST_PAYLOAD: dict[str, Any] = {
    "conversation_id": "sess-00000000-0000-4000-8000-000000000001",
    "session_id": "sess-00000000-0000-4000-8000-000000000001",
    "turn_id": "turn-00000000-0000-4000-8000-000000000001",
    "transcript_path": str(
        Path("~/.cursor/projects/example/agent-transcripts/example.jsonl").expanduser(),
    ),
    "workspace_roots": [str(Path.cwd())],
    "cwd": str(Path.cwd()),
    "hook_event_name": "beforeSubmitPrompt",
    "model": "example-model",
    "permission_mode": "default",
    "prompt": "say hi",
}
HOOK_STDIN_VERIFY_ONLY_TEST_PAYLOAD: dict[str, Any] = {
    "session_id": "sess-00000000-0000-4000-8000-000000000001",
    "cwd": str(Path.cwd()),
    "hook_event_name": "preToolUse",
    "tool_name": "Shell",
    "tool_input": {"command": "echo hi"},
}


def format_hook_stdin_test_command(
    *,
    debug: bool = False,
    invocation: HookCliInvocation | None = None,
    selected_agents: list[HookAgentName] | None = None,
    verify_only: bool = False,
) -> str:
    """Return a copy-paste shell snippet that pipes anonymized hook JSON to ``cyt-client``."""
    command = cyt_client_command(invocation=invocation)
    if debug:
        command = prefix_command_env({"CYT_HOOK_DEBUG": "1"}, command)
    payload = _hook_stdin_test_payload(
        verify_only=verify_only,
        selected_agents=selected_agents or [],
    )
    payload_json = json.dumps(payload, indent=2)
    if sys.platform == "win32":
        return "\n".join(
            (
                "@'",
                payload_json,
                f"'@ | {command}",
            ),
        )
    return "\n".join(
        (
            f"cat <<'EOF' | {command}",
            payload_json,
            "EOF",
        ),
    )


def _hook_stdin_test_payload(
    *,
    verify_only: bool,
    selected_agents: list[HookAgentName],
) -> dict[str, Any]:
    if not verify_only:
        return HOOK_STDIN_TEST_PAYLOAD
    if selected_agents == ["cursor"]:
        return HOOK_STDIN_TEST_PAYLOAD
    return HOOK_STDIN_VERIFY_ONLY_TEST_PAYLOAD


def _hook_stdin_test_event_label(
    *,
    verify_only: bool,
    selected_agents: list[HookAgentName],
) -> str:
    payload = _hook_stdin_test_payload(
        verify_only=verify_only,
        selected_agents=selected_agents,
    )
    event = payload.get("hook_event_name")
    return str(event) if isinstance(event, str) else HOOK_EVENT_NAME


def _print_hook_stdin_test_example(
    *,
    debug: bool,
    invocation: HookCliInvocation | None = None,
    selected_agents: list[HookAgentName] | None = None,
    verify_only: bool = False,
) -> None:
    agents = selected_agents or []
    event_label = _hook_stdin_test_event_label(verify_only=verify_only, selected_agents=agents)
    print("\n--- Test manually ---")
    print(f"\nTest the hook locally ({event_label} payload on stdin) like so:")
    print()
    print(
        format_hook_stdin_test_command(
            debug=debug,
            invocation=invocation,
            selected_agents=agents,
            verify_only=verify_only,
        ),
    )
    print()
    print("Hook JSON output is written to stdout.")
    if debug:
        print("Set CYT_HOOK_DEBUG=1 to enable extra diagnostics on the hook server.")


def _print_hook_uninstall_instructions() -> None:
    print("\n--- Remove Hooks ---")
    print("\nTo remove installed agent hooks later, run:")
    print("  cyt hook --uninstall")


def _selected_agents_use_hook_injection(
    config: dict[str, Any],
    selected_agents: list[HookAgentName],
) -> bool:
    for agent in selected_agents:
        if agent == "cursor":
            return True
        if inject_via_for_agent(config, agent) == "hook":
            return True
    return False


def _should_propose_hook_daemon_restart(
    config: dict[str, Any],
    selected_agents: list[HookAgentName],
    *,
    prevent_hallucinations: bool,
) -> bool:
    del prevent_hallucinations
    return _selected_agents_use_hook_injection(config, selected_agents)


def _propose_hook_daemon_restart(
    *,
    config_path: Path | None = None,
    invocation: HookCliInvocation | None = None,
    selected_agents: list[HookAgentName],
    prevent_hallucinations: bool = False,
) -> None:
    from cyt.hook.daemon import daemon_restart

    resolved_invocation = invocation or detect_hook_cli_invocation()
    command = cyt_daemon_restart_command(invocation=resolved_invocation)
    print("\n--- Hook Daemon ---")
    if prevent_hallucinations:
        mode = "development CLI" if resolved_invocation.is_dev else "packaged cyt"
        print(f"\n\nRestarting hook daemon for verify-only mode via {mode}:\n  {command}")
        daemon_restart(config_path=config_path, unattended=True)
        return
    if "cursor" in selected_agents:
        prompt = "Restart the hook daemon so Cursor session hooks pick up the new configuration?"
    else:
        prompt = "Restart the hook daemon so session hooks pick up the new configuration?"
    print()
    if not _prompt_yes_no(prompt, default_yes=True):
        print(f"Skipped. Run manually when ready:\n  {command}")
        return
    mode = "development CLI" if resolved_invocation.is_dev else "packaged cyt"
    print(f"\nRestarting hook daemon via {mode}:\n  {command}")
    daemon_restart(config_path=config_path, unattended=False)


def cursor_before_submit_entry(
    *,
    agent: AgentName = "cursor",
    set_launch_agent: bool = False,
    invocation: HookCliInvocation | None = None,
) -> dict[str, Any]:
    return cyt_client_entry(
        agent=agent,
        set_launch_agent=set_launch_agent,
        invocation=invocation,
        use_cursor_wrappers=True,
    )


def cursor_session_start_entries(
    *,
    agent: AgentName = "cursor",
    set_launch_agent: bool = False,
    invocation: HookCliInvocation | None = None,
) -> list[dict[str, Any]]:
    """sessionStart: start hook daemon, then reset rules file to lifecycle placeholder."""
    return [
        cyt_daemon_start_entry(
            agent=agent,
            set_launch_agent=set_launch_agent,
            invocation=invocation,
            use_cursor_wrappers=True,
        ),
        cyt_session_end_entry(
            agent=agent,
            set_launch_agent=set_launch_agent,
            invocation=invocation,
            use_cursor_wrappers=True,
        ),
    ]


def cursor_session_start_entry(
    *,
    agent: AgentName = "cursor",
    set_launch_agent: bool = False,
    invocation: HookCliInvocation | None = None,
) -> dict[str, Any]:
    """Back-compat alias; prefer :func:`cursor_session_start_entries`."""
    return cursor_session_start_entries(
        agent=agent,
        set_launch_agent=set_launch_agent,
        invocation=invocation,
    )[0]


def cursor_session_end_entry(
    *,
    agent: AgentName = "cursor",
    set_launch_agent: bool = False,
    invocation: HookCliInvocation | None = None,
) -> dict[str, Any]:
    return cyt_client_entry(
        agent=agent,
        set_launch_agent=set_launch_agent,
        invocation=invocation,
        use_cursor_wrappers=True,
    )


class CursorHookEntries(TypedDict):
    before_submit: dict[str, Any]
    session_start: list[dict[str, Any]]
    session_end: dict[str, Any]
    pre_tool: dict[str, Any]
    pre_tool_read: dict[str, Any]
    before_read_file: dict[str, Any]
    post_tool: dict[str, Any]
    pre_compact: dict[str, Any]


def cursor_hook_entries(
    *,
    agent: AgentName = "cursor",
    set_launch_agent: bool = False,
    invocation: HookCliInvocation | None = None,
    include_post_tool_use: bool = True,
) -> CursorHookEntries:
    client_entry = cyt_client_entry(
        agent=agent,
        set_launch_agent=set_launch_agent,
        invocation=invocation,
        use_cursor_wrappers=True,
    )
    post_tool: dict[str, Any] = {
        **client_entry,
        "matcher": CURSOR_POST_TOOL_MATCHER,
    }
    pre_tool_read: dict[str, Any] = {
        **client_entry,
        "matcher": CURSOR_PRE_TOOL_READ_MATCHER,
    }
    return {
        "before_submit": client_entry,
        "session_start": cursor_session_start_entries(
            agent=agent,
            set_launch_agent=set_launch_agent,
            invocation=invocation,
        ),
        "session_end": cursor_session_end_entry(
            agent=agent,
            set_launch_agent=set_launch_agent,
            invocation=invocation,
        ),
        "pre_tool": client_entry,
        "pre_tool_read": pre_tool_read,
        "before_read_file": client_entry,
        "post_tool": post_tool if include_post_tool_use else {},
        "pre_compact": client_entry,
    }


def cursor_read_intercept_hook_options(
    entries: CursorHookEntries,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return ``upsert_cursor_hooks_into_file`` kwargs for skill read interception."""
    if not skills_hook_agent_interceptor_enabled(config):
        return {
            "before_read_file_entry": None,
            "pre_tool_read_entry": None,
        }
    return {
        "before_read_file_entry": entries["before_read_file"],
        "pre_tool_read_entry": entries["pre_tool_read"],
    }


def cursor_upsert_hook_kwargs(
    entries: CursorHookEntries,
    *,
    config: dict[str, Any] | None = None,
    include_post_tool_use: bool = True,
) -> dict[str, Any]:
    """Build keyword arguments for ``upsert_cursor_hooks_into_file``."""
    return {
        "before_submit_entry": entries["before_submit"],
        "session_start_entries": entries["session_start"],
        "session_end_entry": entries["session_end"],
        "pre_tool_entry": entries["pre_tool"],
        "post_tool_entry": entries["post_tool"],
        "pre_compact_entry": entries["pre_compact"],
        "include_post_tool_use": include_post_tool_use,
        **cursor_read_intercept_hook_options(entries, config=config),
    }


def cursor_desired_hook_commands(
    *,
    agent: AgentName = "cursor",
    set_launch_agent: bool = False,
    invocation: HookCliInvocation | None = None,
    include_post_tool_use: bool = True,
    config: dict[str, Any] | None = None,
) -> list[str]:
    entries = cursor_hook_entries(
        agent=agent,
        set_launch_agent=set_launch_agent,
        invocation=invocation,
        include_post_tool_use=include_post_tool_use,
    )
    session_start_commands = [
        str(entry.get("command")) for entry in entries["session_start"] if isinstance(entry, dict)
    ]
    commands = [
        str(entries["before_submit"].get("command")),
        *session_start_commands,
        str(entries["session_end"].get("command")),
        str(entries["pre_tool"].get("command")),
        str(entries["pre_compact"].get("command")),
    ]
    if skills_hook_agent_interceptor_enabled(config):
        commands.append(str(entries["before_read_file"].get("command")))
        commands.append(str(entries["pre_tool_read"].get("command")))
    if include_post_tool_use and entries["post_tool"]:
        commands.append(str(entries["post_tool"].get("command")))
    return commands


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
    agent_defaults: list[str] = []
    if include_claude:
        agent_defaults.append(CLAUDE_SKILLS_DIR.as_posix())
    if include_codex:
        agent_defaults.append(CODEX_SKILLS_DIR.as_posix())
    if include_cursor:
        agent_defaults.append(CURSOR_SKILLS_DIR.as_posix())

    selected_agents = sum((include_claude, include_codex, include_cursor))
    if selected_agents == 1:
        return agent_defaults

    raw = skills_cfg.get("directories")
    if isinstance(raw, list) and raw:
        return [str(path) for path in raw if str(path).strip()]

    return agent_defaults


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
        raw = _prompt("\nSkills directories (comma-separated paths)", default_str)
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
    directories: list[str] | None = None,
    *,
    enabled: bool = True,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build a skills overlay for hook mode, or ``None`` when no config write is needed."""
    from cyt.config import (
        DEFAULT_INJECT_VIA_BY_AGENT,
        inject_via_for_agent,
        tools_hook_cyt_mcp_agent,
    )

    hook_agent = tools_hook_cyt_mcp_agent(config or {})
    inject_ok = inject_via_for_agent(config or {}, hook_agent) == "hook"
    inject_overlay = {"pruning": {"inject_via": dict.fromkeys(DEFAULT_INJECT_VIA_BY_AGENT, "hook")}}

    if not enabled:
        if existing_skills.get("enabled") is False and inject_ok:
            return None
        return {**inject_overlay, "skills": {"enabled": False}}

    if directories is None:
        return None

    existing_dirs = existing_skills.get("directories")
    if not isinstance(existing_dirs, list):
        existing_dirs = []

    merged_dirs, dirs_changed = merge_skills_directory_lists(existing_dirs, directories)
    enabled_ok = existing_skills.get("enabled") is True

    if enabled_ok and inject_ok and not dirs_changed:
        return None

    return {
        **inject_overlay,
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
        enabled=True,
        config=load_config(config_path),
    )
    if overlay is None:
        return False
    return save_user_config(config_path, overlay, apply_bundled_sections=False)


def _save_hook_skills_disabled(config_path: Path, *, user_overlay: dict[str, Any]) -> bool:
    skills_cfg = user_overlay.get("skills")
    existing_skills = skills_cfg if isinstance(skills_cfg, dict) else {}
    overlay = build_hook_skills_config_overlay(
        existing_skills,
        enabled=False,
        config=load_config(config_path),
    )
    if overlay is None:
        return False
    return save_user_config(config_path, overlay, apply_bundled_sections=False)


def _save_hook_agent_interceptor_enabled(config_path: Path, *, enabled: bool) -> bool:
    overlay: dict[str, Any] = {
        "skills": {"hook": {"agent_interceptor": {"enabled": enabled}}},
    }
    return save_user_config(config_path, overlay, apply_bundled_sections=False)


def _configure_hook_agent_interceptor(config_path: Path) -> None:
    enabled = _prompt_yes_no("Enable hook skill interceptor?", default_yes=True)
    if _save_hook_agent_interceptor_enabled(config_path, enabled=enabled):
        state = "enabled" if enabled else "disabled"
        print(f"Updated skills config in {config_path} (agent_interceptor: {state})")
    else:
        state = "enabled" if enabled else "disabled"
        print(f"\nSkills agent interceptor config already set in {config_path} ({state})")


def _configure_hook_skills(
    *,
    config_path: Path,
    include_claude: bool,
    include_codex: bool,
    include_cursor: bool = False,
) -> None:
    if not sys.stdin.isatty():
        return

    user_overlay = load_user_config_overlay(config_path)
    skills_cfg = user_overlay.get("skills")
    skills_cfg = skills_cfg if isinstance(skills_cfg, dict) else {}
    merged_config = load_config(config_path)
    default_enabled = skills_enabled(merged_config)

    print("\n--- Skills injection ---")
    enabled = _prompt_yes_no("Enable skills injection?", default_yes=default_enabled)
    if not enabled:
        if _save_hook_skills_disabled(config_path, user_overlay=user_overlay):
            print(f"Updated skills config in {config_path} (disabled)")
        else:
            print(f"\nSkills config already set for hook mode in {config_path}")
        return

    _configure_hook_agent_interceptor(config_path)

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
        print(f"\nSkills config already set for hook mode in {config_path}")


def cyt_client_entry(
    *,
    agent: AgentName | None = None,
    set_launch_agent: bool = False,
    invocation: HookCliInvocation | None = None,
    use_cursor_wrappers: bool = False,
) -> dict[str, Any]:
    if use_cursor_wrappers:
        command = cursor_hook_client_command(invocation=invocation)
    else:
        command = cyt_client_command(invocation=invocation)
    if set_launch_agent and agent is not None:
        command = prefix_command_env({CYT_LAUNCH_AGENT_ENV: agent}, command)
    return {"type": "command", "command": command, "timeout": USER_PROMPT_TIMEOUT_SECONDS}


def cyt_session_end_entry(
    *,
    agent: AgentName | None = None,
    set_launch_agent: bool = False,
    invocation: HookCliInvocation | None = None,
    use_cursor_wrappers: bool = False,
) -> dict[str, Any]:
    if use_cursor_wrappers:
        command = cursor_hook_client_command(invocation=invocation)
    else:
        command = cyt_client_command(invocation=invocation)
    if set_launch_agent and agent is not None:
        command = prefix_command_env({CYT_LAUNCH_AGENT_ENV: agent}, command)
    return {"type": "command", "command": command, "timeout": HOOK_TIMEOUT_SECONDS}


def cyt_daemon_start_entry(
    *,
    agent: AgentName | None = None,
    set_launch_agent: bool = False,
    invocation: HookCliInvocation | None = None,
    use_cursor_wrappers: bool = False,
) -> dict[str, Any]:
    if use_cursor_wrappers:
        command = cursor_hook_daemon_start_command(invocation=invocation)
    else:
        command = cyt_daemon_start_command(invocation=invocation)
    if set_launch_agent and agent is not None:
        command = prefix_command_env({CYT_LAUNCH_AGENT_ENV: agent}, command)
    return {"type": "command", "command": command, "timeout": SESSION_START_TIMEOUT_SECONDS}


def cyt_hook_entry(
    *,
    debug: bool = False,
    agent: AgentName | None = None,
    set_launch_agent: bool = False,
    invocation: HookCliInvocation | None = None,
) -> dict[str, Any]:
    """Back-compat alias; prefer :func:`cyt_client_entry`."""
    del debug
    return cyt_client_entry(
        agent=agent,
        set_launch_agent=set_launch_agent,
        invocation=invocation,
    )


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
    if is_windows_hook_wrapper_command(normalized):
        return True
    if normalized == CYT_CLIENT_COMMAND or normalized.endswith(f" {CYT_CLIENT_COMMAND}"):
        return True
    if is_dev_cyt_hook_command(normalized):
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
    unique_commands = list(dict.fromkeys(commands))
    if len(unique_commands) < len(commands):
        debug_bits = {_cyt_hook_has_debug_flag(command) for command in unique_commands}
        if len(debug_bits) == 1:
            debug_label = "with --debug" if debug_bits.pop() else "without --debug"
            duplicate_count = len(commands) - len(unique_commands)
            return (
                f"found {len(commands)} CYT hooks "
                f"({duplicate_count} duplicate command(s), {debug_label})"
            )
        return f"found {len(commands)} CYT hooks (duplicate commands, mixed debug settings)"
    if len(commands) > 1:
        debug_bits = {_cyt_hook_has_debug_flag(command) for command in commands}
        if len(debug_bits) == 1:
            debug_label = "with --debug" if debug_bits.pop() else "without --debug"
            return f"found {len(commands)} CYT hooks across events ({debug_label})"
        return f"found {len(commands)} CYT hooks across events (mixed debug settings)"
    command = commands[0]
    debug_label = "with --debug" if _cyt_hook_has_debug_flag(command) else "without --debug"
    return f"CYT hook already configured ({debug_label})"


def _format_hook_action_status(existing_commands: list[str]) -> str:
    if not existing_commands:
        return "no CYT hooks configured"
    return _format_existing_hook_status(existing_commands)


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


def _append_cursor_flat_hook_if_missing(
    hooks_section: dict[str, Any],
    event_name: str,
    entry: dict[str, Any],
    *,
    matcher: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Append a flat Cursor hook entry when no matching matcher/command exists."""
    merged = copy.deepcopy(hooks_section) if hooks_section else {}
    wrappers = merged.get(event_name)
    if not isinstance(wrappers, list):
        wrappers = []
        merged[event_name] = wrappers
    for wrapper in wrappers:
        if not isinstance(wrapper, dict):
            continue
        wrapper_matcher = str(wrapper.get("matcher") or "")
        if matcher is not None:
            if wrapper_matcher != matcher:
                continue
        elif wrapper_matcher:
            continue
        existing = _collect_cyt_hook_commands_for_flat_event({event_name: [wrapper]}, event_name)
        if existing and not _cyt_hook_needs_update(existing, entry):
            return merged, False
    merged = _append_flat_hook_entry(merged, copy.deepcopy(entry), event_name=event_name)
    return merged, True


def _remove_legacy_cursor_tool_hook_events(
    hooks_section: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    merged = copy.deepcopy(hooks_section) if hooks_section else {}
    changed = False
    for event_name in _LEGACY_CURSOR_TOOL_HOOK_EVENTS:
        merged, event_changed = _remove_cyt_hooks_for_event(merged, event_name)
        changed = changed or event_changed
    return merged, changed


def upsert_cursor_hooks(
    hooks_section: dict[str, Any],
    *,
    before_submit_entry: dict[str, Any],
    session_start_entries: list[dict[str, Any]],
    session_end_entry: dict[str, Any],
    pre_tool_entry: dict[str, Any],
    post_tool_entry: dict[str, Any],
    pre_compact_entry: dict[str, Any],
    before_read_file_entry: dict[str, Any] | None = None,
    pre_tool_read_entry: dict[str, Any] | None = None,
    include_post_tool_use: bool = True,
) -> tuple[dict[str, Any], bool]:
    merged = copy.deepcopy(hooks_section) if hooks_section else {}
    changed = False

    merged, deprecated_changed = _remove_legacy_cursor_tool_hook_events(merged)
    changed = changed or deprecated_changed

    event_specs: list[tuple[str, list[dict[str, Any]]]] = [
        (CURSOR_BEFORE_SUBMIT_EVENT, [before_submit_entry]),
        (CURSOR_SESSION_START_EVENT, session_start_entries),
        (CURSOR_SESSION_END_EVENT, [session_end_entry]),
        (CURSOR_PRE_TOOL_EVENT, [pre_tool_entry]),
        (CURSOR_PRE_COMPACT_EVENT, [pre_compact_entry]),
    ]
    if before_read_file_entry is not None:
        event_specs.append((CURSOR_BEFORE_READ_FILE_EVENT, [before_read_file_entry]))
    if include_post_tool_use and post_tool_entry:
        event_specs.append((CURSOR_POST_TOOL_EVENT, [post_tool_entry]))
    else:
        merged, removed_post = _remove_cyt_hooks_for_event(merged, CURSOR_POST_TOOL_EVENT)
        changed = changed or removed_post

    for event_name, entries in event_specs:
        merged, event_changed = _upsert_cursor_flat_event(merged, event_name, entries)
        changed = changed or event_changed

    if pre_tool_read_entry is not None:
        merged, changed_read = _append_cursor_flat_hook_if_missing(
            merged,
            CURSOR_PRE_TOOL_EVENT,
            pre_tool_read_entry,
            matcher=CURSOR_PRE_TOOL_READ_MATCHER,
        )
        changed = changed or changed_read

    return merged, changed


def upsert_cursor_hooks_into_file(
    path: Path,
    *,
    before_submit_entry: dict[str, Any],
    session_start_entries: list[dict[str, Any]],
    session_end_entry: dict[str, Any],
    pre_tool_entry: dict[str, Any],
    post_tool_entry: dict[str, Any],
    pre_compact_entry: dict[str, Any] | None = None,
    before_read_file_entry: dict[str, Any] | None = None,
    pre_tool_read_entry: dict[str, Any] | None = None,
    include_post_tool_use: bool = True,
) -> bool:
    resolved_pre_compact = pre_compact_entry or pre_tool_entry
    existing = _load_json_object(path)
    hooks_section = normalize_cursor_hooks_section(existing.get("hooks"))
    merged_hooks, changed = upsert_cursor_hooks(
        hooks_section,
        before_submit_entry=before_submit_entry,
        session_start_entries=session_start_entries,
        session_end_entry=session_end_entry,
        pre_tool_entry=pre_tool_entry,
        post_tool_entry=post_tool_entry,
        pre_compact_entry=resolved_pre_compact,
        before_read_file_entry=before_read_file_entry,
        pre_tool_read_entry=pre_tool_read_entry,
        include_post_tool_use=include_post_tool_use,
    )
    if not changed:
        return False

    _write_cursor_hooks_file(path, existing, merged_hooks)
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


def upsert_cyt_matcher_hook(
    hooks_section: dict[str, Any],
    entry: dict[str, Any],
    *,
    event_name: str,
    matcher: str,
) -> tuple[dict[str, Any], bool]:
    merged = copy.deepcopy(hooks_section) if hooks_section else {}
    existing = _collect_cyt_hook_commands_for_event(merged, event_name)
    if not _cyt_hook_needs_update(existing, entry):
        return merged, False

    merged, _ = _remove_cyt_hooks_for_event(merged, event_name)
    wrappers = merged.setdefault(event_name, [])
    if not isinstance(wrappers, list):
        wrappers = []
        merged[event_name] = wrappers
    wrappers.append(
        {
            "matcher": matcher,
            "hooks": [copy.deepcopy(entry)],
        },
    )
    return merged, True


def append_cyt_matcher_hook_if_missing(
    hooks_section: dict[str, Any],
    entry: dict[str, Any],
    *,
    event_name: str,
    matcher: str,
) -> tuple[dict[str, Any], bool]:
    """Append a matcher wrapper for *event_name* when *matcher* is not already present."""
    merged = copy.deepcopy(hooks_section) if hooks_section else {}
    wrappers = merged.get(event_name)
    if not isinstance(wrappers, list):
        wrappers = []
        merged[event_name] = wrappers
    for wrapper in wrappers:
        if isinstance(wrapper, dict) and str(wrapper.get("matcher") or "") == matcher:
            existing = _collect_cyt_hook_commands_for_event({event_name: [wrapper]}, event_name)
            if existing and not _cyt_hook_needs_update(existing, entry):
                return merged, False
    wrappers.append(
        {
            "matcher": matcher,
            "hooks": [copy.deepcopy(entry)],
        },
    )
    return merged, True


def merge_cyt_hooks(
    hooks_section: dict[str, Any],
    *,
    user_prompt_entry: dict[str, Any],
    session_start_entry: dict[str, Any],
    session_end_entry: dict[str, Any],
    pre_tool_use_entry: dict[str, Any],
    post_tool_use_entry: dict[str, Any] | None = None,
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
    merged, changed_end = merge_cyt_hook(
        merged,
        session_end_entry,
        event_name=SESSION_END_EVENT,
    )
    merged, changed_pre = upsert_cyt_matcher_hook(
        merged,
        pre_tool_use_entry,
        event_name=PRE_TOOL_USE_EVENT,
        matcher=PRE_TOOL_USE_MATCHER,
    )
    merged, changed_read = append_cyt_matcher_hook_if_missing(
        merged,
        pre_tool_use_entry,
        event_name=PRE_TOOL_USE_EVENT,
        matcher=PRE_TOOL_USE_READ_MATCHER,
    )
    changed_pre = changed_pre or changed_read
    changed_post = False
    if post_tool_use_entry is not None:
        merged, changed_post = upsert_cyt_matcher_hook(
            merged,
            post_tool_use_entry,
            event_name=POST_TOOL_USE_EVENT,
            matcher=POST_TOOL_USE_MATCHER,
        )
    else:
        merged, removed_post = _remove_cyt_hooks_for_event(merged, POST_TOOL_USE_EVENT)
        changed_post = removed_post
    return merged, changed_user or changed_session or changed_end or changed_pre or changed_post


def upsert_cyt_hooks(
    hooks_section: dict[str, Any],
    *,
    user_prompt_entry: dict[str, Any],
    session_start_entry: dict[str, Any],
    session_end_entry: dict[str, Any],
    pre_tool_use_entry: dict[str, Any],
    post_tool_use_entry: dict[str, Any] | None = None,
    pre_compact_entry: dict[str, Any] | None = None,
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
    merged, changed_end = upsert_cyt_hook(
        merged,
        session_end_entry,
        event_name=SESSION_END_EVENT,
    )
    merged, changed_pre = upsert_cyt_matcher_hook(
        merged,
        pre_tool_use_entry,
        event_name=PRE_TOOL_USE_EVENT,
        matcher=PRE_TOOL_USE_MATCHER,
    )
    merged, changed_read = append_cyt_matcher_hook_if_missing(
        merged,
        pre_tool_use_entry,
        event_name=PRE_TOOL_USE_EVENT,
        matcher=PRE_TOOL_USE_READ_MATCHER,
    )
    changed_pre = changed_pre or changed_read
    changed_post = False
    if post_tool_use_entry is not None:
        merged, changed_post = upsert_cyt_matcher_hook(
            merged,
            post_tool_use_entry,
            event_name=POST_TOOL_USE_EVENT,
            matcher=POST_TOOL_USE_MATCHER,
        )
    else:
        merged, removed_post = _remove_cyt_hooks_for_event(merged, POST_TOOL_USE_EVENT)
        changed_post = removed_post
    changed_compact = False
    if pre_compact_entry is not None:
        merged, changed_pre_compact = upsert_cyt_hook(
            merged,
            pre_compact_entry,
            event_name=PRE_COMPACT_EVENT,
        )
        changed_compact = changed_compact or changed_pre_compact
    else:
        merged, removed_pre_compact = _remove_cyt_hooks_for_event(merged, PRE_COMPACT_EVENT)
        changed_compact = changed_compact or removed_pre_compact
    return (
        merged,
        changed_user
        or changed_session
        or changed_end
        or changed_pre
        or changed_post
        or changed_compact,
    )


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
    preserve_empty_hooks_object: bool = False,
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

    if preserve_empty_hooks_object:
        _write_cursor_hooks_file(path, existing, merged_hooks)
    elif merged_hooks:
        existing[hooks_key] = merged_hooks
        _write_json_object(path, existing)
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


def _finalize_cursor_hooks_payload(
    existing: dict[str, Any],
    merged_hooks: dict[str, Any],
) -> dict[str, Any]:
    """Return Cursor hooks.json payload preserving the minimum ``version`` + ``hooks`` shape."""
    payload = copy.deepcopy(existing)
    payload["version"] = payload.get("version", 1)
    payload["hooks"] = merged_hooks if merged_hooks else {}
    return payload


def _write_cursor_hooks_file(
    path: Path,
    existing: dict[str, Any],
    merged_hooks: dict[str, Any],
) -> None:
    _write_json_object(path, _finalize_cursor_hooks_payload(existing, merged_hooks))


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
    session_end_entry: dict[str, Any],
    pre_tool_use_entry: dict[str, Any],
    post_tool_use_entry: dict[str, Any] | None = None,
    pre_compact_entry: dict[str, Any] | None = None,
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
        session_end_entry=session_end_entry,
        pre_tool_use_entry=pre_tool_use_entry,
        post_tool_use_entry=post_tool_use_entry,
        pre_compact_entry=pre_compact_entry,
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
    from cyt.hook.credentials import report_and_ensure_hook_credentials

    if not skills_enabled(config) and not tools_enabled(config):
        return
    report_and_ensure_hook_credentials(config, exit_on_missing_non_tty=True)


def _primary_hook_agent(selected_agents: list[HookAgentName]) -> HookAgentName | None:
    for agent in ("cursor", "claude", "codex"):
        if agent in selected_agents:
            return agent
    return None


def _report_hook_tools_readiness(config: dict[str, Any]) -> None:
    if not tools_enabled(config):
        return
    sources = set(tools_hook_sources(config))
    if "cyt_mcp" in sources:
        report_cyt_mcp_hook_readiness(config)
    if "mcpc" in sources:
        report_mcpc_hook_readiness(config)
    if "cloudflare" in sources:
        report_cloudflare_hook_readiness(config)


def _save_tools_hook_wizard_config(
    resolved_config_path: Path,
    config: dict[str, Any],
    *,
    config_path: Path | None,
    hook_agent: HookAgentName | None = None,
) -> dict[str, Any]:
    if not sys.stdin.isatty():
        return config

    print("\n--- Tools injection ---")
    enable_tools = _prompt_yes_no("Enable tools injection?", default_yes=True)
    if not enable_tools:
        if save_user_config(
            resolved_config_path,
            {
                "pruning": {
                    "inject_via": dict.fromkeys(DEFAULT_INJECT_VIA_BY_AGENT, "hook"),
                    "tools": {"enabled": False},
                },
            },
            apply_bundled_sections=False,
        ):
            print(f"Saved tools hook settings to {resolved_config_path} (disabled)")
            return load_config(config_path)
        return config

    tools_overlay = prompt_tools_hook_config(
        config,
        context="hook",
        agent=hook_agent,
    )
    if save_user_config(
        resolved_config_path,
        {
            "pruning": {
                "inject_via": dict.fromkeys(DEFAULT_INJECT_VIA_BY_AGENT, "hook"),
                "tools": {"enabled": True, **tools_overlay},
            },
        },
        apply_bundled_sections=False,
    ):
        print(f"Saved tools hook settings to {resolved_config_path}")
        return load_config(config_path)
    return config


def _build_nested_hook_entries(
    agent: AgentName,
    *,
    set_launch_agent: bool,
    invocation: HookCliInvocation,
    include_post_tool_use: bool,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any] | None,
    dict[str, Any],
]:
    user_prompt_entry = cyt_client_entry(
        agent=agent,
        set_launch_agent=set_launch_agent,
        invocation=invocation,
    )
    session_start_entry = cyt_daemon_start_entry(
        agent=agent,
        set_launch_agent=set_launch_agent,
        invocation=invocation,
    )
    session_end_entry = cyt_session_end_entry(
        agent=agent,
        set_launch_agent=set_launch_agent,
        invocation=invocation,
    )
    pre_tool_use_entry = cyt_client_entry(
        agent=agent,
        set_launch_agent=set_launch_agent,
        invocation=invocation,
    )
    post_tool_use_entry = (
        cyt_client_entry(
            agent=agent,
            set_launch_agent=set_launch_agent,
            invocation=invocation,
        )
        if include_post_tool_use
        else None
    )
    pre_compact_entry = cyt_client_entry(
        agent=agent,
        set_launch_agent=set_launch_agent,
        invocation=invocation,
    )
    return (
        user_prompt_entry,
        session_start_entry,
        session_end_entry,
        pre_tool_use_entry,
        post_tool_use_entry,
        pre_compact_entry,
    )


def _upsert_nested_hooks_file(
    path: Path,
    *,
    user_prompt_entry: dict[str, Any],
    session_start_entry: dict[str, Any],
    session_end_entry: dict[str, Any],
    pre_tool_use_entry: dict[str, Any],
    post_tool_use_entry: dict[str, Any] | None,
    pre_compact_entry: dict[str, Any],
) -> bool:
    return upsert_all_hooks_into_file(
        path,
        user_prompt_entry=user_prompt_entry,
        session_start_entry=session_start_entry,
        session_end_entry=session_end_entry,
        pre_tool_use_entry=pre_tool_use_entry,
        post_tool_use_entry=post_tool_use_entry,
        pre_compact_entry=pre_compact_entry,
    )


def _handle_existing_nested_hooks(
    label: str,
    path: Path,
    hooks_section: dict[str, Any],
    *,
    user_prompt_entry: dict[str, Any],
    session_start_entry: dict[str, Any],
    session_end_entry: dict[str, Any],
    pre_tool_use_entry: dict[str, Any],
    post_tool_use_entry: dict[str, Any] | None,
    pre_compact_entry: dict[str, Any],
    existing_commands: list[str],
    desired_commands: set[str],
    needs_update: bool,
    action_choices: tuple[str, ...],
) -> bool | None:
    """Return True/False when changed, or None when the caller should continue the loop."""
    print(f"{label}: {_format_hook_action_status(existing_commands)} in {path}")
    default_action = "update" if needs_update else "skip"
    action = _prompt_choice(
        f"{label}: existing CYT hook — choose action (update | remove | skip)",
        list(action_choices),
        default_index=action_choices.index(default_action),
    )
    if action == "skip":
        print(f"{label}: kept existing hook")
        return None
    if action == "remove":
        merged_hooks, changed = remove_cyt_hooks(hooks_section)
        if changed:
            _save_hooks_section_to_file(path, merged_hooks)
            print(f"{label}: removed CYT hook from {path}")
            return True
        print(f"{label}: no CYT hook to remove in {path}")
        return None

    if _upsert_nested_hooks_file(
        path,
        user_prompt_entry=user_prompt_entry,
        session_start_entry=session_start_entry,
        session_end_entry=session_end_entry,
        pre_tool_use_entry=pre_tool_use_entry,
        post_tool_use_entry=post_tool_use_entry,
        pre_compact_entry=pre_compact_entry,
    ):
        print(f"{label}: updated CYT hooks in {path}")
        return True
    print(f"{label}: CYT hooks already match selected settings")
    return None


def _nested_hook_desired_commands(
    *,
    user_prompt_entry: dict[str, Any],
    session_start_entry: dict[str, Any],
    session_end_entry: dict[str, Any],
    pre_tool_use_entry: dict[str, Any],
    post_tool_use_entry: dict[str, Any] | None,
    pre_compact_entry: dict[str, Any],
) -> set[str]:
    desired_commands = {
        str(user_prompt_entry.get("command")),
        str(session_start_entry.get("command")),
        str(session_end_entry.get("command")),
        str(pre_tool_use_entry.get("command")),
        str(pre_compact_entry.get("command")),
    }
    if post_tool_use_entry is not None:
        desired_commands.add(str(post_tool_use_entry.get("command")))
    return desired_commands


def _install_single_nested_hook_target(
    label: str,
    path: Path,
    *,
    user_prompt_entry: dict[str, Any],
    session_start_entry: dict[str, Any],
    session_end_entry: dict[str, Any],
    pre_tool_use_entry: dict[str, Any],
    post_tool_use_entry: dict[str, Any] | None,
    pre_compact_entry: dict[str, Any],
    hooks_section: dict[str, Any],
    existing_commands: list[str],
    desired_commands: set[str],
    needs_update: bool,
    action_choices: tuple[str, ...],
    apply_silently: bool,
    always_prompt_hook_action: bool,
) -> bool | None:
    if apply_silently:
        if not needs_update:
            return None
        if _upsert_nested_hooks_file(
            path,
            user_prompt_entry=user_prompt_entry,
            session_start_entry=session_start_entry,
            session_end_entry=session_end_entry,
            pre_tool_use_entry=pre_tool_use_entry,
            post_tool_use_entry=post_tool_use_entry,
            pre_compact_entry=pre_compact_entry,
        ):
            print(f"{label}: updated CYT hooks with selected command options")
            return True
        return None
    if existing_commands or always_prompt_hook_action:
        return _handle_existing_nested_hooks(
            label,
            path,
            hooks_section,
            user_prompt_entry=user_prompt_entry,
            session_start_entry=session_start_entry,
            session_end_entry=session_end_entry,
            pre_tool_use_entry=pre_tool_use_entry,
            post_tool_use_entry=post_tool_use_entry,
            pre_compact_entry=pre_compact_entry,
            existing_commands=existing_commands,
            desired_commands=desired_commands,
            needs_update=needs_update if existing_commands else True,
            action_choices=action_choices,
        )
    if not _prompt_yes_no(f"Install CYT hooks for {label}?", default_yes=True):
        print(f"{label}: skipped")
        return None
    if _upsert_nested_hooks_file(
        path,
        user_prompt_entry=user_prompt_entry,
        session_start_entry=session_start_entry,
        session_end_entry=session_end_entry,
        pre_tool_use_entry=pre_tool_use_entry,
        post_tool_use_entry=post_tool_use_entry,
        pre_compact_entry=pre_compact_entry,
    ):
        print(f"{label}: added CYT hooks to {path}")
        return True
    print(f"{label}: CYT hooks already configured in {path}")
    return None


def _install_nested_hooks_for_targets(
    targets: list[tuple[str, Path, AgentName]],
    *,
    debug: bool,
    set_launch_agent: bool,
    invocation: HookCliInvocation,
    include_post_tool_use: bool = True,
    always_prompt_hook_action: bool = False,
    apply_silently: bool = False,
) -> bool:
    any_changed = False
    action_choices = ("update", "remove", "skip")
    for label, path, agent in targets:
        (
            user_prompt_entry,
            session_start_entry,
            session_end_entry,
            pre_tool_use_entry,
            post_tool_use_entry,
            pre_compact_entry,
        ) = _build_nested_hook_entries(
            agent,
            set_launch_agent=set_launch_agent,
            invocation=invocation,
            include_post_tool_use=include_post_tool_use,
        )
        hooks_data = _load_json_object(path)
        hooks_section = hooks_data.get("hooks")
        if not isinstance(hooks_section, dict):
            hooks_section = {}

        existing_commands = _collect_cyt_hook_commands(hooks_section)
        desired_commands = _nested_hook_desired_commands(
            user_prompt_entry=user_prompt_entry,
            session_start_entry=session_start_entry,
            session_end_entry=session_end_entry,
            pre_tool_use_entry=pre_tool_use_entry,
            post_tool_use_entry=post_tool_use_entry,
            pre_compact_entry=pre_compact_entry,
        )
        needs_update = set(existing_commands) != desired_commands or len(existing_commands) != len(
            desired_commands,
        )
        changed = _install_single_nested_hook_target(
            label,
            path,
            user_prompt_entry=user_prompt_entry,
            session_start_entry=session_start_entry,
            session_end_entry=session_end_entry,
            pre_tool_use_entry=pre_tool_use_entry,
            post_tool_use_entry=post_tool_use_entry,
            pre_compact_entry=pre_compact_entry,
            hooks_section=hooks_section,
            existing_commands=existing_commands,
            desired_commands=desired_commands,
            needs_update=needs_update,
            action_choices=action_choices,
            apply_silently=apply_silently,
            always_prompt_hook_action=always_prompt_hook_action,
        )
        if changed is True:
            any_changed = True
    return any_changed


def _handle_existing_cursor_hooks(
    label: str,
    path: Path,
    hooks_section: dict[str, Any],
    *,
    needs_update: bool,
    upsert_kwargs: dict[str, Any],
    include_post_tool_use: bool = True,
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
            _write_cursor_hooks_file(path, existing, merged_hooks)
            print(f"{label}: removed CYT hook from {path}")
            return True
        print(f"{label}: no CYT hook to remove in {path}")
        return False

    if upsert_cursor_hooks_into_file(path, **upsert_kwargs):
        print(f"{label}: updated CYT hooks in {path}")
        return True
    print(f"{label}: CYT hooks already match selected settings")
    return False


def _install_cursor_hooks_for_target(
    label: str,
    path: Path,
    *,
    debug: bool,
    set_launch_agent: bool,
    invocation: HookCliInvocation,
    include_post_tool_use: bool = True,
    always_prompt_hook_action: bool = False,
    apply_silently: bool = False,
    config: dict[str, Any] | None = None,
) -> bool:
    del debug
    entries = cursor_hook_entries(
        agent="cursor",
        set_launch_agent=set_launch_agent,
        invocation=invocation,
        include_post_tool_use=include_post_tool_use,
    )
    upsert_kwargs = cursor_upsert_hook_kwargs(
        entries,
        config=config,
        include_post_tool_use=include_post_tool_use,
    )

    hooks_data = _load_json_object(path)
    hooks_section = hooks_data.get("hooks")
    if not isinstance(hooks_section, dict):
        hooks_section = {}

    existing_commands = _collect_cyt_hook_commands(hooks_section)
    desired_commands = cursor_desired_hook_commands(
        agent="cursor",
        set_launch_agent=set_launch_agent,
        invocation=invocation,
        include_post_tool_use=include_post_tool_use,
        config=config,
    )
    needs_update = set(existing_commands) != set(desired_commands) or len(existing_commands) != len(
        desired_commands,
    )

    if apply_silently:
        if not needs_update:
            return False
        if upsert_cursor_hooks_into_file(path, **upsert_kwargs):
            print(f"{label}: updated CYT hooks with selected command options")
            return True
        return False

    if existing_commands or always_prompt_hook_action:
        print(f"{label}: {_format_hook_action_status(existing_commands)} in {path}")
        return _handle_existing_cursor_hooks(
            label,
            path,
            hooks_section,
            needs_update=needs_update if existing_commands else True,
            upsert_kwargs=upsert_kwargs,
            include_post_tool_use=include_post_tool_use,
        )

    if not _prompt_yes_no(f"Install CYT hooks for {label}?", default_yes=True):
        print(f"{label}: skipped")
        return False
    if upsert_cursor_hooks_into_file(path, **upsert_kwargs):
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


def _prompt_prevent_hallucinations_inject_via(
    agent: HookAgentName,
    config: dict[str, Any],
) -> str:
    choices = ("hook", "proxy")
    current = inject_via_for_agent(config, agent)
    default = current if current in choices else "proxy"
    print(f"\n--- Tool detection ({agent}) ---")
    return _prompt_choice(
        f"Detect tools for {agent} via (hook | proxy)",
        list(choices),
        default_index=choices.index(default),
    )


def _prompt_prevent_hallucinations_mcp_migration(agent: HookAgentName) -> bool:
    if not sys.stdin.isatty():
        return True
    print(f"\n--- Migrate ({agent})'s MCP config ---")
    return _prompt_yes_no(
        "Migrate agent MCP config to cyt-mcp aggregator?",
        default_yes=True,
    )


def _apply_injection_hook_config(
    config_path: Path,
    config: dict[str, Any],
    *,
    agents: list[HookAgentName],
) -> dict[str, Any]:
    """Restore full hook injection settings after verify-only / prevent-hallucinations."""
    from cyt.config import save_user_config, sync_config_in_place

    del agents
    overlay: dict[str, Any] = {
        "hallucination_gate": {"enabled": False},
        "pruning": {
            "tools": {"enabled": True},
        },
    }
    if save_user_config(config_path, overlay, apply_bundled_sections=False):
        sync_config_in_place(config, config_path)
    return config


def _configure_cursor_rule_file(
    config_path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Prompt to enable Cursor rules file injection when hook injection is active."""
    from cyt.config import save_user_config, sync_config_in_place

    skills_injection_enabled = skills_enabled(config)
    tools_injection_enabled = tools_enabled(config)
    if not skills_injection_enabled and not tools_injection_enabled:
        enabled = False
    elif sys.stdin.isatty():
        print("\n--- Cursor rules file ---")
        enabled = _prompt_yes_no(
            "Enable Cursor rules file injection (.cursor/rules/cyt-injection.mdc)?",
            default_yes=True,
        )
    else:
        enabled = True

    overlay = {"skills": {"hook": {"cursor_rule_file": {"enabled": enabled}}}}
    if save_user_config(config_path, overlay, apply_bundled_sections=False):
        sync_config_in_place(config, config_path)
        print(
            f"Updated cursor rules file config in {config_path} "
            f"({'enabled' if enabled else 'disabled'})",
        )
    else:
        print(
            f"\nCursor rules file config already set in {config_path} "
            f"({'enabled' if enabled else 'disabled'})",
        )
    return config


def _apply_prevent_hallucinations_config(
    config_path: Path,
    config: dict[str, Any],
    *,
    agents: list[HookAgentName],
) -> dict[str, Any]:
    from cyt.config import save_user_config, sync_config_in_place
    from cyt.tools.cyt_mcp_setup import setup_cyt_mcp_for_agent, write_mcp_aggregator_yaml

    inject_map: dict[str, str] = {
        "cursor": "hook",
        "claude": "proxy",
        "codex": "proxy",
    }
    if sys.stdin.isatty():
        for name in ("claude", "codex"):
            if name not in agents:
                continue
            inject_map[name] = _prompt_prevent_hallucinations_inject_via(name, config)
    overlay = {
        "hallucination_gate": {"enabled": True},
        "skills": {
            "enabled": False,
            "hook": {"cursor_rule_file": {"enabled": False}},
        },
        "pruning": {
            "tools": {"enabled": False},
            "inject_via": inject_map,
        },
    }
    save_user_config(config_path, overlay, apply_bundled_sections=False)
    sync_config_in_place(config, config_path)

    invocation = detect_hook_cli_invocation()
    if invocation.is_dev and invocation.repo_root is not None:
        print(
            f"\nInstalling development cyt-mcp via uv run --directory {invocation.repo_root}",
            file=sys.stderr,
        )

    for agent in agents:
        if inject_map.get(agent) != "hook":
            write_mcp_aggregator_yaml(agent, transport="stdio", verify_only=True)
            continue
        migrate_backends = _prompt_prevent_hallucinations_mcp_migration(agent)
        setup_cyt_mcp_for_agent(
            agent,
            invocation=invocation,
            transport="stdio",
            migrate_backends=migrate_backends,
            verify_only=True,
        )
    return config


def _install_hook_setup_targets(
    nested_targets: list[tuple[str, Path, AgentName]],
    cursor_targets: list[tuple[str, Path]],
    *,
    debug: bool,
    set_launch_agent: bool,
    invocation: HookCliInvocation,
    include_post_tool_use: bool,
    config: dict[str, Any] | None = None,
    always_prompt_hook_action: bool = False,
    apply_silently: bool = False,
) -> bool:
    any_changed = False
    if nested_targets:
        any_changed = (
            _install_nested_hooks_for_targets(
                nested_targets,
                debug=debug,
                set_launch_agent=set_launch_agent,
                invocation=invocation,
                include_post_tool_use=include_post_tool_use,
                always_prompt_hook_action=always_prompt_hook_action,
                apply_silently=apply_silently,
            )
            or any_changed
        )
    for label, path in cursor_targets:
        any_changed = (
            _install_cursor_hooks_for_target(
                label,
                path,
                debug=debug,
                set_launch_agent=set_launch_agent,
                invocation=invocation,
                include_post_tool_use=include_post_tool_use,
                always_prompt_hook_action=always_prompt_hook_action,
                apply_silently=apply_silently,
                config=config,
            )
            or any_changed
        )
    return any_changed


def _hook_setup_install_options(
    *,
    prevent_hallucinations: bool,
    resolved_config_path: Path,
    include_claude: bool,
    include_codex: bool,
    include_cursor: bool,
) -> tuple[bool, bool]:
    if prevent_hallucinations:
        return False, False
    set_launch_agent = _prompt_yes_no(
        f"\nPrefix hook commands with {CYT_LAUNCH_AGENT_ENV}=<agent>?",
        default_yes=False,
    )
    debug = _prompt_yes_no("Enable hook debug logging (--debug)?", default_yes=False)
    return set_launch_agent, debug


def _load_hook_setup_config(
    *,
    config_path: Path | None,
    resolved_config_path: Path,
    selected_agents: list[HookAgentName],
    prevent_hallucinations: bool,
) -> dict[str, Any]:
    config = load_config(config_path)
    if prevent_hallucinations:
        return _apply_prevent_hallucinations_config(
            resolved_config_path,
            config,
            agents=selected_agents,
        )
    config = _apply_injection_hook_config(
        resolved_config_path,
        config,
        agents=selected_agents,
    )
    if sys.stdin.isatty() and _selected_agents_use_hook_injection(config, selected_agents):
        config = ensure_hook_inject_via(resolved_config_path, config)
    return config


def _run_standard_hook_setup_steps(
    *,
    resolved_config_path: Path,
    config_path: Path | None,
    config: dict[str, Any],
    selected_agents: list[HookAgentName],
    include_claude: bool,
    include_codex: bool,
    include_cursor: bool,
) -> dict[str, Any]:
    _configure_hook_skills(
        config_path=resolved_config_path,
        include_claude=include_claude,
        include_codex=include_codex,
        include_cursor=include_cursor,
    )
    config = _save_tools_hook_wizard_config(
        resolved_config_path,
        config,
        config_path=config_path,
        hook_agent=_primary_hook_agent(selected_agents),
    )
    config = load_config(config_path)
    _ensure_hook_credentials(config)
    _report_hook_tools_readiness(config)
    if "cursor" in selected_agents:
        config = _configure_cursor_rule_file(resolved_config_path, config)
    return config


def _finish_hook_setup(
    *,
    config_path: Path | None,
    config: dict[str, Any],
    selected_agents: list[HookAgentName],
    prevent_hallucinations: bool,
    nested_targets: list[tuple[str, Path, AgentName]],
    cursor_targets: list[tuple[str, Path]],
    invocation: HookCliInvocation,
    include_post_tool_use: bool,
) -> None:
    set_launch_agent, debug = _hook_setup_install_options(
        prevent_hallucinations=prevent_hallucinations,
        resolved_config_path=resolve_setup_config_path(config_path),
        include_claude="claude" in selected_agents,
        include_codex="codex" in selected_agents,
        include_cursor="cursor" in selected_agents,
    )
    if set_launch_agent:
        if _install_hook_setup_targets(
            nested_targets,
            cursor_targets,
            debug=debug,
            set_launch_agent=True,
            invocation=invocation,
            include_post_tool_use=include_post_tool_use,
            config=config,
            apply_silently=True,
        ):
            print("\nRestart your agent so hook changes take effect.")

    if _should_propose_hook_daemon_restart(
        config,
        selected_agents,
        prevent_hallucinations=prevent_hallucinations,
    ):
        _propose_hook_daemon_restart(
            config_path=config_path,
            invocation=invocation,
            selected_agents=selected_agents,
            prevent_hallucinations=prevent_hallucinations,
        )

    _print_hook_stdin_test_example(
        debug=debug,
        invocation=invocation,
        selected_agents=selected_agents,
        verify_only=prevent_hallucinations,
    )
    _print_hook_uninstall_instructions()


def run_hook_setup(
    *,
    config_path: Path | None = None,
    agents: list[HookAgentName] | None = None,
    prevent_hallucinations: bool = False,
) -> None:
    """Install CYT agent hooks and ensure runtime credentials."""
    selected_agents = _resolve_hook_setup_agents(agents)
    resolved_config_path = resolve_setup_config_path(config_path)
    config = _load_hook_setup_config(
        config_path=config_path,
        resolved_config_path=resolved_config_path,
        selected_agents=selected_agents,
        prevent_hallucinations=prevent_hallucinations,
    )
    if len(selected_agents) == 1:
        print(f"CYT hook setup ({selected_agents[0]})\n")
    else:
        print("CYT hook setup\n")

    nested_targets, cursor_targets, include_claude, include_codex, include_cursor = (
        _collect_hook_setup_targets(selected_agents)
    )

    if not nested_targets and not cursor_targets:
        raise SystemExit("No agent config files found for the selected hook targets.")

    if prevent_hallucinations:
        print("Verify-only hallucination prevention enabled (no prompt injection).")
    else:
        config = _run_standard_hook_setup_steps(
            resolved_config_path=resolved_config_path,
            config_path=config_path,
            config=config,
            selected_agents=selected_agents,
            include_claude=include_claude,
            include_codex=include_codex,
            include_cursor=include_cursor,
        )

    if sys.stdin.isatty():
        print("\n--- Install Hooks ---")

    invocation = detect_hook_cli_invocation()
    if invocation.is_dev and invocation.repo_root is not None:
        print(
            f"\nInstalling development hook commands via uv run --directory {invocation.repo_root}",
        )

    include_post_tool_use = not prevent_hallucinations
    any_changed = _install_hook_setup_targets(
        nested_targets,
        cursor_targets,
        debug=False,
        set_launch_agent=False,
        invocation=invocation,
        include_post_tool_use=include_post_tool_use,
        config=config,
        always_prompt_hook_action=prevent_hallucinations,
    )

    if prevent_hallucinations or any_changed:
        print("\nRestart your agent so hook changes take effect.")
    else:
        print("\nNo hook files were modified.")

    _finish_hook_setup(
        config_path=config_path,
        config=config,
        selected_agents=selected_agents,
        prevent_hallucinations=prevent_hallucinations,
        nested_targets=nested_targets,
        cursor_targets=cursor_targets,
        invocation=invocation,
        include_post_tool_use=include_post_tool_use,
    )


def cyt_hooks_installed() -> bool:
    """Return True when any agent config file contains CYT hooks."""
    for path in (
        _agent_config_path(CLAUDE_SETTINGS_PATH),
        _agent_config_path(CODEX_HOOKS_PATH),
        _agent_config_path(CURSOR_HOOKS_PATH),
    ):
        if not path.is_file():
            continue
        hooks_section = _load_json_object(path).get("hooks")
        if isinstance(hooks_section, dict) and cyt_hook_command_exists(hooks_section):
            return True
    return False


def _agent_hooks_config_path(agent: str) -> Path | None:
    if agent == "claude":
        return _agent_config_path(CLAUDE_SETTINGS_PATH)
    if agent == "codex":
        return _agent_config_path(CODEX_HOOKS_PATH)
    if agent == "cursor":
        return _agent_config_path(CURSOR_HOOKS_PATH)
    return None


def pre_tool_use_hooks_installed(agent: str) -> bool:
    """Return True when the agent config has a CYT cyt-client PreToolUse hook."""
    path = _agent_hooks_config_path(agent)
    if path is None or not path.is_file():
        return False
    hooks_section = _load_json_object(path).get("hooks")
    if not isinstance(hooks_section, dict):
        return False
    event_name = CURSOR_PRE_TOOL_EVENT if agent == "cursor" else PRE_TOOL_USE_EVENT
    commands = _collect_cyt_hook_commands_for_event(hooks_section, event_name)
    return bool(commands)


def ensure_pre_tool_hooks_for_launch(agent: AgentName, *, quiet: bool = False) -> bool:
    """Install or refresh PreToolUse hooks before launching Claude/Codex/Cursor."""
    if agent == "cursor":
        from cyt.agents.cursor.launch import ensure_cursor_hooks_for_launch

        return ensure_cursor_hooks_for_launch(quiet=quiet)

    path = _agent_hooks_config_path(agent)
    if path is None:
        return False
    invocation = detect_hook_cli_invocation()
    user_prompt_entry = cyt_client_entry(agent=agent, invocation=invocation)
    session_start_entry = cyt_daemon_start_entry(agent=agent, invocation=invocation)
    session_end_entry = cyt_session_end_entry(agent=agent, invocation=invocation)
    pre_tool_use_entry = cyt_client_entry(agent=agent, invocation=invocation)
    pre_compact_entry = cyt_client_entry(agent=agent, invocation=invocation)
    changed = upsert_all_hooks_into_file(
        path,
        user_prompt_entry=user_prompt_entry,
        session_start_entry=session_start_entry,
        session_end_entry=session_end_entry,
        pre_tool_use_entry=pre_tool_use_entry,
        post_tool_use_entry=cyt_client_entry(agent=agent, invocation=invocation),
        pre_compact_entry=pre_compact_entry,
    )
    if changed and not quiet:
        print(f"Updated CYT hooks (including PreToolUse) in {path}")
    return changed


def run_hook_uninstall(*, agents: list[HookAgentName] | None = None) -> None:
    """Remove CYT agent hooks from Claude, Codex, and/or Cursor config files."""
    selected_agents = _resolve_hook_setup_agents(agents)

    if len(selected_agents) == 1:
        print(f"CYT hook uninstall ({selected_agents[0]})\n")
    else:
        print("CYT hook uninstall\n")

    any_changed = False
    for agent in selected_agents:
        label = _agent_hook_label(agent)
        path = _agent_hook_path(agent)
        if not path.is_file():
            print(f"{label}: skipped ({path} not found)")
            continue
        preserve_empty_hooks_object = agent == "cursor"
        if uninstall_hooks_from_file(
            path,
            preserve_empty_hooks_object=preserve_empty_hooks_object,
        ):
            print(f"{label}: removed CYT hook from {path}")
            any_changed = True
        else:
            print(f"{label}: no CYT hook in {path}")

    if "cursor" in selected_agents:
        removed = remove_windows_hook_wrappers()
        if removed:
            print(f"Cursor: removed {len(removed)} Windows hook wrapper(s)")

    if any_changed:
        print("\nRestart your agent so hook changes take effect.")
    else:
        print("\nNo hook files were modified.")
