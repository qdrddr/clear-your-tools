"""Dev/prod cyt hook command helpers (stdlib only)."""

from __future__ import annotations

import shlex
import subprocess
import sys
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
    r"get-tool-definitions|cyt-mcp_get-tool-definitions|"
    r"mcp__cyt-mcp__get-tool-definitions|"
    r"MCP:get-tool-definitions|MCP:cyt-mcp_get-tool-definitions"
)
WINDOWS_CLIENT_WRAPPER = "cyt-client.cmd"
WINDOWS_CLIENT_DEV_WRAPPER = "cyt-client-dev.cmd"
WINDOWS_DAEMON_START_WRAPPER = "cyt-hook-daemon-start.cmd"
WINDOWS_DAEMON_START_DEV_WRAPPER = "cyt-hook-daemon-start-dev.cmd"
_WINDOWS_HOOK_WRAPPER_NAMES = (
    WINDOWS_CLIENT_WRAPPER,
    WINDOWS_CLIENT_DEV_WRAPPER,
    WINDOWS_DAEMON_START_WRAPPER,
    WINDOWS_DAEMON_START_DEV_WRAPPER,
)


def use_windows_hook_wrappers(*, use_dev: bool) -> bool:
    """Use ``.cmd`` wrappers only for installed (prod) hooks on Windows."""
    if sys.platform != "win32":
        return False
    return not use_dev


def cursor_hooks_dir() -> Path:
    return Path("~/.cursor/hooks").expanduser()


def prefix_command_env(env: dict[str, str], command: str) -> str:
    if not env:
        return command
    if sys.platform == "win32":
        parts = [f'set "{key}={value}"' for key, value in env.items()]
        return 'cmd /c "' + "&& ".join([*parts, f'call "{command}"']) + '"'
    prefix = " ".join(f"{key}={value}" for key, value in env.items())
    return f"{prefix} {command}"


def _write_windows_wrapper(path: Path, inner_command: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ("@echo off", inner_command)
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def install_windows_hook_wrappers(
    *,
    use_dev: bool,
    dev_repo_root: Path | None,
) -> dict[str, Path]:
    """Write Cursor hook wrapper ``.cmd`` scripts and return name → path mapping."""
    hooks_dir = cursor_hooks_dir()
    hooks_dir.mkdir(parents=True, exist_ok=True)

    client_inner = _inline_cyt_client_command(use_dev=use_dev, dev_repo_root=dev_repo_root)
    daemon_inner = _inline_cyt_daemon_start_command(use_dev=use_dev, dev_repo_root=dev_repo_root)

    client_name = WINDOWS_CLIENT_DEV_WRAPPER if use_dev else WINDOWS_CLIENT_WRAPPER
    daemon_name = WINDOWS_DAEMON_START_DEV_WRAPPER if use_dev else WINDOWS_DAEMON_START_WRAPPER

    client_path = hooks_dir / client_name
    daemon_path = hooks_dir / daemon_name
    _write_windows_wrapper(client_path, client_inner)
    _write_windows_wrapper(daemon_path, daemon_inner)

    for stale_name in _WINDOWS_HOOK_WRAPPER_NAMES:
        if stale_name in {client_name, daemon_name}:
            continue
        stale_path = hooks_dir / stale_name
        if stale_path.is_file():
            stale_path.unlink()

    return {
        "client": client_path,
        "daemon_start": daemon_path,
    }


def _inline_cyt_client_command(*, use_dev: bool, dev_repo_root: Path | None) -> str:
    if use_dev and dev_repo_root is not None:
        return build_uv_run_dev_command(dev_repo_root, CYT_CLIENT_SCRIPT_REL)
    return INSTALLED_CYT_CLIENT_COMMAND


def _inline_cyt_daemon_start_command(*, use_dev: bool, dev_repo_root: Path | None) -> str:
    if use_dev and dev_repo_root is not None:
        return build_uv_run_dev_command(
            dev_repo_root,
            CYT_PROXY_SCRIPT_REL,
            "hook",
            "daemon",
            "start",
            "--unattended",
        )
    return INSTALLED_CYT_DAEMON_START_COMMAND


def _cursor_hook_client_command(
    *,
    use_dev: bool,
    dev_repo_root: Path | None,
) -> str:
    if use_windows_hook_wrappers(use_dev=use_dev):
        wrappers = install_windows_hook_wrappers(use_dev=use_dev, dev_repo_root=dev_repo_root)
        return str(wrappers["client"])
    return _inline_cyt_client_command(use_dev=use_dev, dev_repo_root=dev_repo_root)


def _cursor_hook_daemon_start_command(
    *,
    use_dev: bool,
    dev_repo_root: Path | None,
) -> str:
    if use_windows_hook_wrappers(use_dev=use_dev):
        wrappers = install_windows_hook_wrappers(use_dev=use_dev, dev_repo_root=dev_repo_root)
        return str(wrappers["daemon_start"])
    return _inline_cyt_daemon_start_command(use_dev=use_dev, dev_repo_root=dev_repo_root)


def is_windows_hook_wrapper_command(command: str) -> bool:
    normalized = command.strip().strip('"').casefold()
    if not normalized.endswith(".cmd"):
        return False
    name = Path(normalized).name.casefold()
    return name in {wrapper.casefold() for wrapper in _WINDOWS_HOOK_WRAPPER_NAMES}


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
    if sys.platform == "win32":
        # Always quote the directory: cmd.exe needs sane quoting, and downstream
        # shlex.split must round-trip Windows paths with backslashes.
        directory = str(repo_root).replace('"', '""')
        tail = subprocess.list2cmdline([script_rel, *args])
        return f'uv run --directory "{directory}" {tail}'
    parts = ["uv", "run", "--directory", str(repo_root), script_rel, *args]
    return shlex.join(parts)


def is_cyt_hook_command(command: object) -> bool:
    if not isinstance(command, str):
        return False
    normalized = _strip_env_prefix(command.strip())
    if is_windows_hook_wrapper_command(normalized):
        return True
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


def _command_uses_launch_agent_prefix(command: str) -> bool:
    stripped = command.strip()
    if stripped.startswith(f"{CYT_LAUNCH_AGENT_ENV}="):
        return True
    return stripped.lower().startswith("cmd /c") and CYT_LAUNCH_AGENT_ENV in stripped


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
            if isinstance(command, str) and _command_uses_launch_agent_prefix(command):
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
    command = _cursor_hook_client_command(use_dev=use_dev, dev_repo_root=dev_repo_root)
    if set_launch_agent:
        command = prefix_command_env({CYT_LAUNCH_AGENT_ENV: agent}, command)
    return command


def cyt_daemon_start_hook_command(
    agent: str,
    *,
    use_dev: bool,
    dev_repo_root: Path | None,
    set_launch_agent: bool,
) -> str:
    command = _cursor_hook_daemon_start_command(use_dev=use_dev, dev_repo_root=dev_repo_root)
    if set_launch_agent:
        command = prefix_command_env({CYT_LAUNCH_AGENT_ENV: agent}, command)
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
        "preCompact": [client_entry],
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
