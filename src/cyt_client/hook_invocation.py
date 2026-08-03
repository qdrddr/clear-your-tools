"""Dev/prod cyt hook command helpers (stdlib only)."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from cyt_client.mcp_entry import (
    CYT_MCP_SCRIPT_REL,
    _strip_env_prefix,
    dev_invocation_from_hooks_file,
    dev_invocation_from_mcp_file,
    is_cyt_dev_hook_command,
)

INSTALLED_CYT_CLIENT_COMMAND = "cyt-client"
INSTALLED_CYT_DAEMON_START_COMMAND = "cyt hook daemon start --unattended"
INSTALLED_CYT_DAEMON_START_COMMAND_BASE = "cyt hook daemon start"
CYT_CLIENT_SCRIPT_REL = "src/cyt_client/cli.py"
CYT_PROXY_SCRIPT_REL = "src/cyt/proxy/cli.py"
CYT_LAUNCH_AGENT_ENV = "CYT_LAUNCH_AGENT"
HOOK_TIMEOUT_SECONDS = 60
CURSOR_POST_TOOL_MATCHER = (
    r"cyt-mcp_search|mcp__cyt-mcp__search|MCP:cyt-mcp_search|MCP:mcp__cyt-mcp__search"
)


def repo_root_from_package_script(script: Path) -> Path | None:
    candidate = script.resolve().parents[2]
    if (candidate / "pyproject.toml").is_file():
        return candidate
    return None


def runtime_dev_repo_from_client() -> Path | None:
    from cyt_client import cli as cli_mod

    repo = repo_root_from_package_script(Path(cli_mod.__file__))
    if repo is not None and (repo / CYT_CLIENT_SCRIPT_REL).is_file():
        return repo
    return None


def runtime_dev_repo_from_mcp() -> Path | None:
    from cyt_mcp import cli as cli_mod

    repo = repo_root_from_package_script(Path(cli_mod.__file__))
    if repo is not None and (repo / CYT_MCP_SCRIPT_REL).is_file():
        return repo
    return None


def build_uv_run_dev_command(repo_root: Path, script_rel: str, *args: str) -> str:
    return shlex.join(["uv", "run", "--directory", str(repo_root), script_rel, *args])


def is_cyt_hook_command(command: object) -> bool:
    if not isinstance(command, str):
        return False
    normalized = _strip_env_prefix(command.strip())
    if normalized == INSTALLED_CYT_CLIENT_COMMAND or normalized.endswith(
        f" {INSTALLED_CYT_CLIENT_COMMAND}",
    ):
        return True
    if is_cyt_dev_hook_command(normalized):
        return True
    if (
        INSTALLED_CYT_DAEMON_START_COMMAND in normalized
        or INSTALLED_CYT_DAEMON_START_COMMAND_BASE in normalized
    ):
        return True
    return False


def hooks_use_launch_agent_prefix(hooks_path: Path) -> bool:
    if not hooks_path.is_file():
        return False
    import json

    try:
        payload = json.loads(hooks_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    hooks = payload.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for event_entries in hooks.values():
        if not isinstance(event_entries, list):
            continue
        for entry in event_entries:
            if not isinstance(entry, dict):
                continue
            command = entry.get("command")
            if isinstance(command, str) and command.strip().startswith(f"{CYT_LAUNCH_AGENT_ENV}="):
                return True
    return False


def resolve_pairing_dev_context(
    agent: str,
    *,
    hooks_path: Path | None,
    mcp_path: Path | None,
    runtime_repo: Path | None = None,
) -> tuple[bool, Path | None]:
    _ = agent
    if runtime_repo is not None and (runtime_repo / CYT_MCP_SCRIPT_REL).is_file():
        return True, runtime_repo
    if mcp_path is not None:
        dev = dev_invocation_from_mcp_file(mcp_path.expanduser())
        if dev is not None:
            return True, dev[0]
    if hooks_path is not None:
        dev = dev_invocation_from_hooks_file(hooks_path.expanduser())
        if dev is not None:
            return True, dev[0]
    return False, None


def cyt_client_hook_command(
    agent: str,
    *,
    use_dev: bool,
    dev_repo_root: Path | None,
    set_launch_agent: bool,
) -> str:
    if use_dev and dev_repo_root is not None:
        command = build_uv_run_dev_command(dev_repo_root, CYT_CLIENT_SCRIPT_REL)
    else:
        command = INSTALLED_CYT_CLIENT_COMMAND
    if set_launch_agent:
        command = f"{CYT_LAUNCH_AGENT_ENV}={agent} {command}"
    return command


def cyt_daemon_start_hook_command(
    agent: str,
    *,
    use_dev: bool,
    dev_repo_root: Path | None,
    set_launch_agent: bool,
) -> str:
    if use_dev and dev_repo_root is not None:
        command = build_uv_run_dev_command(
            dev_repo_root,
            CYT_PROXY_SCRIPT_REL,
            "hook",
            "daemon",
            "start",
            "--unattended",
        )
    else:
        command = INSTALLED_CYT_DAEMON_START_COMMAND
    if set_launch_agent:
        command = f"{CYT_LAUNCH_AGENT_ENV}={agent} {command}"
    return command


def cursor_pairing_hooks(
    agent: str,
    *,
    use_dev: bool,
    dev_repo_root: Path | None,
    set_launch_agent: bool,
) -> dict[str, list[dict[str, Any]]]:
    client_entry = {
        "command": cyt_client_hook_command(
            agent,
            use_dev=use_dev,
            dev_repo_root=dev_repo_root,
            set_launch_agent=set_launch_agent,
        ),
        "timeout": HOOK_TIMEOUT_SECONDS,
    }
    daemon_entry = {
        "command": cyt_daemon_start_hook_command(
            agent,
            use_dev=use_dev,
            dev_repo_root=dev_repo_root,
            set_launch_agent=set_launch_agent,
        ),
        "timeout": HOOK_TIMEOUT_SECONDS,
    }
    return {
        "sessionStart": [daemon_entry, client_entry],
        "sessionEnd": [client_entry],
        "beforeSubmitPrompt": [client_entry],
        "preToolUse": [client_entry],
        "postToolUse": [
            {
                **client_entry,
                "matcher": CURSOR_POST_TOOL_MATCHER,
            },
        ],
    }


def strip_cyt_hook_entries(entries: list[Any]) -> list[Any]:
    kept: list[Any] = []
    for entry in entries:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        command = entry.get("command")
        if is_cyt_hook_command(command):
            continue
        kept.append(entry)
    return kept
