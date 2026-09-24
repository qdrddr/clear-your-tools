"""Dev/prod cyt hook command helpers (stdlib only)."""

from __future__ import annotations

from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cyt.hook.cli_invocation import HookCliInvocation

from cyt_client.compat import is_windows
from cyt_client.hook_executable import (
    build_installed_cyt_client_command,
    build_installed_cyt_daemon_start_command,
    build_uv_run_dev_command,
)
from cyt_client.mcp_entry import (
    CURSOR_WORKSPACE_FOLDER,
    CYT_MCP_SCRIPT_REL,
    CYT_WORKSPACE_ENV,
    _strip_env_prefix,
    dev_invocation_from_hooks_file,
    dev_invocation_from_mcp_file,
    is_cyt_dev_hook_command,
)

INSTALLED_CYT_CLIENT_COMMAND = "cyt-client"
INSTALLED_CYT_DAEMON_START_COMMAND = "cyt hook daemon start --unattended"
INSTALLED_CYT_DAEMON_START_COMMAND_BASE = "cyt hook daemon start"
CYT_CLIENT_SCRIPT_REL = "src/cyt_client/cli.py"
CYT_CLI_APP_SCRIPT_REL = "src/cyt/cli/app.py"
CYT_PROXY_SCRIPT_REL = "src/cyt/proxy/cli.py"
CYT_LAUNCH_AGENT_ENV = "CYT_LAUNCH_AGENT"
HOOK_TIMEOUT_SECONDS = 60
CURSOR_POST_TOOL_DEFINITIONS_MATCHER = (
    r"get-tool-definitions|cyt-mcp_get-tool-definitions|"
    r"mcp__cyt-mcp-usr__get-tool-definitions|"
    r"mcp__cyt-mcp-ws__get-tool-definitions|"
    r"mcp__cyt-mcp__get-tool-definitions|"
    r"MCP:get-tool-definitions|MCP:cyt-mcp_get-tool-definitions"
)
CURSOR_POST_TOOL_EXAMPLES_MATCHER = r"MCP:.*|mcp__.*"
CURSOR_POST_TOOL_TIER_FEEDBACK_MATCHER = r"CallDynamicTool|GetDynamicTools"
CURSOR_POST_TOOL_MATCHER = (
    f"{CURSOR_POST_TOOL_DEFINITIONS_MATCHER}|{CURSOR_POST_TOOL_EXAMPLES_MATCHER}|"
    f"{CURSOR_POST_TOOL_TIER_FEEDBACK_MATCHER}"
)
WINDOWS_CLIENT_WRAPPER = "cyt-client.cmd"
WINDOWS_CLIENT_DEV_WRAPPER = "cyt-client-dev.cmd"
WINDOWS_DAEMON_START_WRAPPER = "cyt-hook-daemon-start.cmd"
WINDOWS_DAEMON_START_DEV_WRAPPER = "cyt-hook-daemon-start-dev.cmd"
WINDOWS_CYT_MCP_DEV_WRAPPER_NAME = "mcp-dev.cmd"
WINDOWS_CYT_MCP_DEV_WRAPPER = f"cyt/{WINDOWS_CYT_MCP_DEV_WRAPPER_NAME}"
LEGACY_WINDOWS_CYT_MCP_DEV_WRAPPER = "cyt-mcp-dev.cmd"
_WINDOWS_HOOK_WRAPPER_NAMES = (
    WINDOWS_CLIENT_WRAPPER,
    WINDOWS_CLIENT_DEV_WRAPPER,
    WINDOWS_DAEMON_START_WRAPPER,
    WINDOWS_DAEMON_START_DEV_WRAPPER,
)


def use_hook_shell_wrappers(*, use_dev: bool) -> bool:
    """Use shell wrapper scripts so Cursor hooks work under fish and resolve workspace env."""
    _ = use_dev
    return True


def use_windows_hook_wrappers(*, use_dev: bool) -> bool:
    """Backward-compatible alias for :func:`use_hook_shell_wrappers`."""
    return use_hook_shell_wrappers(use_dev=use_dev)


def cursor_hooks_dir() -> Path:
    return Path("~/.cursor/hooks").expanduser()


def agent_hooks_cyt_dir(agent: str = "cursor") -> Path:
    """``~/.<agent>/hooks/cyt`` directory for cyt dev wrapper scripts."""
    from cyt.hook.workspace_resolution import agent_cyt_uv_dir

    cyt_dir = agent_cyt_uv_dir(agent)
    if cyt_dir is not None:
        return cyt_dir
    return cursor_hooks_dir() / "cyt"


def cyt_mcp_dev_wrapper_path(agent: str = "cursor") -> Path:
    return agent_hooks_cyt_dir(agent) / WINDOWS_CYT_MCP_DEV_WRAPPER_NAME


def is_cyt_mcp_dev_wrapper_command(command: str) -> bool:
    normalized = command.strip().strip('"').casefold().replace("\\", "/")
    return normalized.endswith(
        (
            WINDOWS_CYT_MCP_DEV_WRAPPER.casefold(),
            LEGACY_WINDOWS_CYT_MCP_DEV_WRAPPER.casefold(),
        ),
    )


_AGENT_WORKSPACE_ENV_VARS: tuple[str, ...] = (
    "CURSOR_PROJECT_DIR",
    "CURSOR_WORKSPACE_FOLDER",
    "CLAUDE_PROJECT_DIR",
    "CODEX_PROJECT_DIR",
)


def prefix_command_env(env: dict[str, str], command: str) -> str:
    if not env:
        return command
    try:
        from cyt.hook.cli_invocation import is_hook_shell_wrapper_command

        if is_hook_shell_wrapper_command(command):
            return command
    except ImportError:
        pass
    if is_windows():
        if is_windows_hook_wrapper_command(command):
            return command
        parts: list[str] = []
        for key, value in env.items():
            if any(char in value for char in (" ", '"', "&", "|", "<", ">", "^")):
                escaped = value.replace('"', '""')
                parts.append(f'set "{key}={escaped}"')
            else:
                parts.append(f"set {key}={value}")
        if command.lower().endswith(".cmd"):
            tail = f'call "{command}"'
        elif any(char in command for char in (" ", "&", "|", "<", ">")):
            tail = f'"{command}"'
        else:
            tail = command
        return "cmd /c " + " && ".join([*parts, tail])
    prefix = " ".join(f"{key}={value}" for key, value in env.items())
    return f"{prefix} {command}"


def _windows_wrapper_env_lines(env: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for key, value in env.items():
        if value == CURSOR_WORKSPACE_FOLDER and key == CYT_WORKSPACE_ENV:
            for agent_var in _AGENT_WORKSPACE_ENV_VARS:
                lines.append(
                    f'if not defined {key} if defined {agent_var} set "{key}=!{agent_var}!"',
                )
            continue
        escaped = value.replace("%", "%%")
        lines.append(f'set "{key}={escaped}"')
    return lines


def _write_windows_wrapper(
    path: Path,
    inner_command: str,
    *,
    env: dict[str, str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["@echo off", "setlocal EnableDelayedExpansion"]
    if env:
        lines.extend(_windows_wrapper_env_lines(env))
    lines.append(inner_command)
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def install_windows_hook_wrappers(
    *,
    use_dev: bool,
    dev_repo_root: Path | None,
    hook_env: dict[str, str] | None = None,
) -> dict[str, Path]:
    """Write Cursor hook wrapper ``.cmd`` scripts and return name → path mapping."""
    hooks_dir = cursor_hooks_dir()
    hooks_dir.mkdir(parents=True, exist_ok=True)

    client_inner = _inline_cyt_client_command(use_dev=use_dev, dev_repo_root=dev_repo_root)
    daemon_inner = _inline_cyt_daemon_start_command(use_dev=use_dev, dev_repo_root=dev_repo_root)
    wrapper_env = dict(
        hook_env
        or {
            CYT_WORKSPACE_ENV: CURSOR_WORKSPACE_FOLDER,
        },
    )

    client_name = WINDOWS_CLIENT_DEV_WRAPPER if use_dev else WINDOWS_CLIENT_WRAPPER
    daemon_name = WINDOWS_DAEMON_START_DEV_WRAPPER if use_dev else WINDOWS_DAEMON_START_WRAPPER

    client_path = hooks_dir / client_name
    daemon_path = hooks_dir / daemon_name
    _write_windows_wrapper(client_path, client_inner, env=wrapper_env)
    _write_windows_wrapper(daemon_path, daemon_inner, env=wrapper_env)

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


def install_windows_cyt_mcp_dev_wrapper(
    *,
    dev_repo_root: Path,
    agent: str = "cursor",
    hook_env: dict[str, str] | None = None,
) -> Path:
    """Write ``hooks/cyt/mcp-dev.cmd`` so Cursor MCP spawn uses absolute ``uv`` on Windows."""
    cyt_dir = agent_hooks_cyt_dir(agent)
    cyt_dir.mkdir(parents=True, exist_ok=True)
    inner = build_uv_run_dev_command(dev_repo_root, CYT_MCP_SCRIPT_REL) + " %*"
    wrapper_env = dict(
        hook_env
        or {
            CYT_WORKSPACE_ENV: CURSOR_WORKSPACE_FOLDER,
        },
    )
    wrapper_path = cyt_dir / WINDOWS_CYT_MCP_DEV_WRAPPER_NAME
    _write_windows_wrapper(wrapper_path, inner, env=wrapper_env)
    legacy = cursor_hooks_dir() / LEGACY_WINDOWS_CYT_MCP_DEV_WRAPPER
    if legacy.is_file():
        legacy.unlink()
    return wrapper_path


def _inline_cyt_client_command(*, use_dev: bool, dev_repo_root: Path | None) -> str:
    if use_dev and dev_repo_root is not None:
        return build_uv_run_dev_command(dev_repo_root, CYT_CLIENT_SCRIPT_REL)
    return build_installed_cyt_client_command()


def _inline_cyt_daemon_start_command(*, use_dev: bool, dev_repo_root: Path | None) -> str:
    if use_dev and dev_repo_root is not None:
        return build_uv_run_dev_command(
            dev_repo_root,
            CYT_CLI_APP_SCRIPT_REL,
            "hook",
            "daemon",
            "start",
            "--unattended",
        )
    return build_installed_cyt_daemon_start_command(unattended=True)


def _hook_cli_invocation(*, use_dev: bool, dev_repo_root: Path | None) -> HookCliInvocation:
    from cyt.hook.cli_invocation import HookCliInvocation

    return HookCliInvocation(
        mode="dev" if use_dev else "installed",
        repo_root=dev_repo_root if use_dev else None,
    )


def _cursor_hook_client_command(
    *,
    use_dev: bool,
    dev_repo_root: Path | None,
    hook_env: dict[str, str] | None = None,
) -> str:
    if use_hook_shell_wrappers(use_dev=use_dev):
        from cyt.hook.cli_invocation import install_hook_shell_wrappers

        wrappers = install_hook_shell_wrappers(
            invocation=_hook_cli_invocation(use_dev=use_dev, dev_repo_root=dev_repo_root),
            hook_env=hook_env,
        )
        return str(wrappers["client"])
    return _inline_cyt_client_command(use_dev=use_dev, dev_repo_root=dev_repo_root)


def _cursor_hook_daemon_start_command(
    *,
    use_dev: bool,
    dev_repo_root: Path | None,
    hook_env: dict[str, str] | None = None,
) -> str:
    if use_hook_shell_wrappers(use_dev=use_dev):
        from cyt.hook.cli_invocation import install_hook_shell_wrappers

        wrappers = install_hook_shell_wrappers(
            invocation=_hook_cli_invocation(use_dev=use_dev, dev_repo_root=dev_repo_root),
            hook_env=hook_env,
        )
        return str(wrappers["daemon_start"])
    return _inline_cyt_daemon_start_command(use_dev=use_dev, dev_repo_root=dev_repo_root)


def _windows_cmd_basename(command: str) -> str:
    """Return the final ``.cmd`` filename from a Windows hook command path."""
    return PureWindowsPath(command.strip().strip('"')).name.casefold()


def is_windows_hook_wrapper_command(command: str) -> bool:
    normalized = command.strip().strip('"').casefold()
    if not normalized.endswith(".cmd"):
        return False
    name = _windows_cmd_basename(normalized)
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


def is_cyt_hook_command(command: object) -> bool:
    if not isinstance(command, str):
        return False
    normalized = _strip_env_prefix(command.strip())
    try:
        from cyt.hook.cli_invocation import is_hook_shell_wrapper_command

        if is_hook_shell_wrapper_command(normalized):
            return True
    except ImportError:
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


def _agent_hook_command_env(*, agent: str, set_launch_agent: bool) -> dict[str, str]:
    env = {CYT_WORKSPACE_ENV: CURSOR_WORKSPACE_FOLDER}
    if set_launch_agent:
        env[CYT_LAUNCH_AGENT_ENV] = agent
    return env


def _prefix_agent_hook_command(command: str, *, agent: str, set_launch_agent: bool) -> str:
    env = _agent_hook_command_env(agent=agent, set_launch_agent=set_launch_agent)
    if use_hook_shell_wrappers(use_dev=False):
        try:
            from cyt.hook.cli_invocation import is_hook_shell_wrapper_command

            if is_hook_shell_wrapper_command(command):
                return command
        except ImportError:
            if is_windows() and is_windows_hook_wrapper_command(command):
                return command
    return prefix_command_env(env, command)


def cyt_client_hook_command(
    agent: str,
    *,
    use_dev: bool,
    dev_repo_root: Path | None,
    set_launch_agent: bool,
) -> str:
    hook_env = _agent_hook_command_env(agent=agent, set_launch_agent=set_launch_agent)
    command = _cursor_hook_client_command(
        use_dev=use_dev,
        dev_repo_root=dev_repo_root,
        hook_env=hook_env,
    )
    return _prefix_agent_hook_command(
        command,
        agent=agent,
        set_launch_agent=set_launch_agent,
    )


def cyt_daemon_start_hook_command(
    agent: str,
    *,
    use_dev: bool,
    dev_repo_root: Path | None,
    set_launch_agent: bool,
) -> str:
    hook_env = _agent_hook_command_env(agent=agent, set_launch_agent=set_launch_agent)
    command = _cursor_hook_daemon_start_command(
        use_dev=use_dev,
        dev_repo_root=dev_repo_root,
        hook_env=hook_env,
    )
    return _prefix_agent_hook_command(
        command,
        agent=agent,
        set_launch_agent=set_launch_agent,
    )


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
