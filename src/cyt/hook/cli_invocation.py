"""Detect how ``cyt hook`` was invoked and build matching agent hook commands."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from cyt.platform.compat import is_windows
from cyt_client.hook_executable import (
    build_installed_cyt_client_command,
    build_installed_cyt_daemon_restart_command,
    build_installed_cyt_daemon_start_command,
    build_uv_run_dev_command,
    is_uv_run_dev_hook_command,
    resolve_hook_executable,
)

CYT_DAEMON_START_ARGS = ("hook", "daemon", "start", "--unattended")
CYT_DAEMON_RESTART_ARGS = ("hook", "daemon", "restart")
INSTALLED_CYT_CLIENT_COMMAND = "cyt-client"
INSTALLED_CYT_DAEMON_START_COMMAND = "cyt hook daemon start --unattended"
INSTALLED_CYT_DAEMON_START_COMMAND_BASE = "cyt hook daemon start"
INSTALLED_CYT_DAEMON_RESTART_COMMAND = "cyt hook daemon restart"
INSTALLED_CYT_MCP_COMMAND = "cyt-mcp"
CYT_CLIENT_CLI_SCRIPT_REL = "src/cyt_client/cli.py"
CYT_PROXY_CLI_SCRIPT_REL = "src/cyt/proxy/cli.py"
CYT_MCP_CLI_SCRIPT_REL = "src/cyt_mcp/cli.py"
WINDOWS_CLIENT_WRAPPER = "cyt-client.cmd"
WINDOWS_CLIENT_DEV_WRAPPER = "cyt-client-dev.cmd"
WINDOWS_DAEMON_START_WRAPPER = "cyt-hook-daemon-start.cmd"
WINDOWS_DAEMON_START_DEV_WRAPPER = "cyt-hook-daemon-start-dev.cmd"
_WINDOWS_WRAPPER_NAMES = (
    WINDOWS_CLIENT_WRAPPER,
    WINDOWS_CLIENT_DEV_WRAPPER,
    WINDOWS_DAEMON_START_WRAPPER,
    WINDOWS_DAEMON_START_DEV_WRAPPER,
)

__all__ = [
    "CYT_CLIENT_CLI_SCRIPT_REL",
    "CYT_DAEMON_RESTART_ARGS",
    "CYT_DAEMON_START_ARGS",
    "CYT_MCP_CLI_SCRIPT_REL",
    "CYT_PROXY_CLI_SCRIPT_REL",
    "INSTALLED_CYT_CLIENT_COMMAND",
    "INSTALLED_CYT_DAEMON_RESTART_COMMAND",
    "INSTALLED_CYT_DAEMON_START_COMMAND",
    "INSTALLED_CYT_DAEMON_START_COMMAND_BASE",
    "INSTALLED_CYT_MCP_COMMAND",
    "HookCliInvocation",
    "build_installed_cyt_client_command",
    "build_installed_cyt_daemon_restart_command",
    "build_installed_cyt_daemon_start_command",
    "build_uv_run_dev_command",
    "cursor_hooks_dir",
    "cyt_client_cli_script_relpath",
    "cyt_client_command",
    "cyt_daemon_restart_command",
    "cyt_daemon_start_command",
    "cyt_mcp_cli_script_relpath",
    "cyt_mcp_mcp_server_entry",
    "detect_hook_cli_invocation",
    "is_uv_run_dev_hook_command",
    "proxy_cli_script_path",
    "repo_root_from_proxy_cli_script",
    "resolve_hook_executable",
    "use_windows_hook_wrappers",
]


@dataclass(frozen=True, slots=True)
class HookCliInvocation:
    mode: Literal["installed", "dev"]
    repo_root: Path | None = None

    @property
    def is_dev(self) -> bool:
        return self.mode == "dev"


def use_windows_hook_wrappers(*, invocation: HookCliInvocation | None = None) -> bool:
    """Use ``.cmd`` wrappers on Windows so hooks resolve absolute executable paths."""
    _ = invocation
    return is_windows()


def cursor_hooks_dir() -> Path:
    return Path("~/.cursor/hooks").expanduser()


def proxy_cli_script_path() -> Path:
    from cyt.proxy import cli as cli_mod

    return Path(cli_mod.__file__).resolve()


def cyt_client_cli_script_path() -> Path:
    from cyt_client import cli as cli_mod

    return Path(cli_mod.__file__).resolve()


def cyt_mcp_cli_script_path() -> Path:
    from cyt_mcp import cli as cli_mod

    return Path(cli_mod.__file__).resolve()


def repo_root_from_proxy_cli_script() -> Path | None:
    script = proxy_cli_script_path()
    candidate = script.parents[3]
    if (candidate / "pyproject.toml").is_file() and script.is_file():
        return candidate
    return None


def _canonical_repo_root() -> Path:
    repo_root = repo_root_from_proxy_cli_script()
    if repo_root is None:
        msg = "Could not resolve CYT repo root from proxy CLI script path"
        raise RuntimeError(msg)
    return repo_root


def script_relpath_from_repo(script: Path, repo_root: Path) -> str:
    return script.relative_to(repo_root.resolve()).as_posix()


def cyt_client_cli_script_relpath() -> str:
    return script_relpath_from_repo(cyt_client_cli_script_path(), _canonical_repo_root())


def proxy_cli_script_relpath() -> str:
    return script_relpath_from_repo(proxy_cli_script_path(), _canonical_repo_root())


def cyt_mcp_cli_script_relpath() -> str:
    return script_relpath_from_repo(cyt_mcp_cli_script_path(), _canonical_repo_root())


def proxy_cli_impl_script_path() -> Path:
    return proxy_cli_script_path().with_name("cli_impl.py")


def invoked_via_proxy_cli_script() -> bool:
    """Return True when the process was started via repo ``cli.py`` or ``cli_impl.py``.

    ``cyt.proxy.cli`` delegates to ``cli_impl.py`` with :func:`runpy.run_path`, which
    rewrites ``sys.argv[0]`` to the impl script path.
    """
    if not sys.argv:
        return False
    try:
        invoked = Path(sys.argv[0]).resolve()
    except (OSError, ValueError):
        return False
    return invoked in {proxy_cli_script_path(), proxy_cli_impl_script_path()}


def detect_hook_cli_invocation() -> HookCliInvocation:
    repo_root = repo_root_from_proxy_cli_script()
    if repo_root is not None and invoked_via_proxy_cli_script():
        return HookCliInvocation(mode="dev", repo_root=repo_root)
    return HookCliInvocation(mode="installed", repo_root=None)


def _inline_cyt_client_command(*, invocation: HookCliInvocation | None = None) -> str:
    invocation = invocation or detect_hook_cli_invocation()
    if invocation.is_dev and invocation.repo_root is not None:
        return build_uv_run_dev_command(
            invocation.repo_root,
            cyt_client_cli_script_relpath(),
        )
    return build_installed_cyt_client_command()


def _inline_cyt_daemon_start_command(*, invocation: HookCliInvocation | None = None) -> str:
    invocation = invocation or detect_hook_cli_invocation()
    if invocation.is_dev and invocation.repo_root is not None:
        return build_uv_run_dev_command(
            invocation.repo_root,
            proxy_cli_script_relpath(),
            *CYT_DAEMON_START_ARGS,
        )
    return build_installed_cyt_daemon_start_command(unattended=True)


def prefix_command_env(env: dict[str, str], command: str) -> str:
    if not env:
        return command
    if is_windows():
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
    invocation: HookCliInvocation | None = None,
) -> dict[str, Path]:
    """Write Cursor hook wrapper ``.cmd`` scripts and return name → path mapping."""
    invocation = invocation or detect_hook_cli_invocation()
    hooks_dir = cursor_hooks_dir()
    hooks_dir.mkdir(parents=True, exist_ok=True)

    client_inner = _inline_cyt_client_command(invocation=invocation)
    daemon_inner = _inline_cyt_daemon_start_command(invocation=invocation)

    client_name = WINDOWS_CLIENT_DEV_WRAPPER if invocation.is_dev else WINDOWS_CLIENT_WRAPPER
    daemon_name = (
        WINDOWS_DAEMON_START_DEV_WRAPPER if invocation.is_dev else WINDOWS_DAEMON_START_WRAPPER
    )

    client_path = hooks_dir / client_name
    daemon_path = hooks_dir / daemon_name
    _write_windows_wrapper(client_path, client_inner)
    _write_windows_wrapper(daemon_path, daemon_inner)

    for stale_name in _WINDOWS_WRAPPER_NAMES:
        if stale_name in {client_name, daemon_name}:
            continue
        stale_path = hooks_dir / stale_name
        if stale_path.is_file():
            stale_path.unlink()

    return {
        "client": client_path,
        "daemon_start": daemon_path,
    }


def remove_windows_hook_wrappers() -> list[Path]:
    removed: list[Path] = []
    hooks_dir = cursor_hooks_dir()
    for name in _WINDOWS_WRAPPER_NAMES:
        path = hooks_dir / name
        if path.is_file():
            path.unlink()
            removed.append(path)
    return removed


def is_windows_hook_wrapper_command(command: str) -> bool:
    normalized = command.strip().strip('"').casefold()
    if not normalized.endswith(".cmd"):
        return False
    name = Path(normalized).name.casefold()
    return name in {wrapper.casefold() for wrapper in _WINDOWS_WRAPPER_NAMES}


def cyt_client_command(*, invocation: HookCliInvocation | None = None) -> str:
    return _inline_cyt_client_command(invocation=invocation)


def cyt_daemon_start_command(*, invocation: HookCliInvocation | None = None) -> str:
    return _inline_cyt_daemon_start_command(invocation=invocation)


def cursor_hook_client_command(*, invocation: HookCliInvocation | None = None) -> str:
    """Return the command string written into Cursor ``hooks.json``."""
    invocation = invocation or detect_hook_cli_invocation()
    if use_windows_hook_wrappers(invocation=invocation):
        wrappers = install_windows_hook_wrappers(invocation=invocation)
        return str(wrappers["client"])
    return _inline_cyt_client_command(invocation=invocation)


def cursor_hook_daemon_start_command(*, invocation: HookCliInvocation | None = None) -> str:
    """Return the daemon start command written into Cursor ``hooks.json``."""
    invocation = invocation or detect_hook_cli_invocation()
    if use_windows_hook_wrappers(invocation=invocation):
        wrappers = install_windows_hook_wrappers(invocation=invocation)
        return str(wrappers["daemon_start"])
    return _inline_cyt_daemon_start_command(invocation=invocation)


def cyt_daemon_restart_command(*, invocation: HookCliInvocation | None = None) -> str:
    invocation = invocation or detect_hook_cli_invocation()
    if invocation.is_dev and invocation.repo_root is not None:
        return build_uv_run_dev_command(
            invocation.repo_root,
            proxy_cli_script_relpath(),
            *CYT_DAEMON_RESTART_ARGS,
        )
    return build_installed_cyt_daemon_restart_command()


def build_hook_spawn_command(
    *,
    port: int,
    config_path: Path | None,
    invocation: HookCliInvocation | None = None,
) -> list[str]:
    """Build argv for spawning a hook-capable CYT proxy server."""
    invocation = invocation or detect_hook_cli_invocation()
    args_tail = [
        "proxy",
        "--port",
        str(port),
        "--quiet",
        "--no-resolve-credentials",
    ]
    if config_path is not None:
        args_tail.extend(["--config", str(config_path)])

    if invocation.is_dev and invocation.repo_root is not None:
        uv = resolve_hook_executable("uv")
        if uv == "uv":
            uv = shutil.which("uv") or ""
        if uv:
            return [
                uv,
                "run",
                "--directory",
                str(invocation.repo_root),
                proxy_cli_script_relpath(),
                *args_tail,
            ]
        script = invocation.repo_root / proxy_cli_script_relpath()
        return [sys.executable, str(script), *args_tail]

    return [sys.executable, "-m", "cyt.proxy.cli", *args_tail]


def cyt_mcp_mcp_server_entry(
    agent: str,
    *,
    invocation: HookCliInvocation | None = None,
    transport: str = "stdio",
    http_host: str = "127.0.0.1",
    http_port: int = 8765,
    http_mcp_path: str = "/mcp",
) -> dict[str, Any]:
    from cyt_client.mcp_entry import build_cyt_mcp_mcp_server_entry, normalize_cyt_mcp_transport

    resolved_transport = normalize_cyt_mcp_transport(transport)
    invocation = invocation or detect_hook_cli_invocation()
    if invocation.is_dev and invocation.repo_root is not None:
        return build_cyt_mcp_mcp_server_entry(
            agent,
            transport=resolved_transport,
            dev_repo_root=invocation.repo_root,
            dev_script_rel=cyt_mcp_cli_script_relpath(),
            http_host=http_host,
            http_port=http_port,
            http_mcp_path=http_mcp_path,
        )
    return build_cyt_mcp_mcp_server_entry(
        agent,
        transport=resolved_transport,
        http_host=http_host,
        http_port=http_port,
        http_mcp_path=http_mcp_path,
    )


def is_dev_cyt_hook_command(command: str) -> bool:
    normalized = command.strip().strip('"')
    if is_windows_hook_wrapper_command(normalized):
        return normalized.casefold().endswith(WINDOWS_CLIENT_DEV_WRAPPER.casefold()) or (
            WINDOWS_DAEMON_START_DEV_WRAPPER.casefold() in normalized.casefold()
        )
    return is_uv_run_dev_hook_command(normalized)
