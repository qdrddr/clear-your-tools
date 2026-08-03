"""Detect how ``cyt hook`` was invoked and build matching agent hook commands."""

from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

CYT_DAEMON_START_ARGS = ("hook", "daemon", "start", "--unattended")
CYT_DAEMON_RESTART_ARGS = ("hook", "daemon", "restart")
INSTALLED_CYT_CLIENT_COMMAND = "cyt-client"
INSTALLED_CYT_DAEMON_START_COMMAND = "cyt hook daemon start --unattended"
INSTALLED_CYT_DAEMON_START_COMMAND_BASE = "cyt hook daemon start"
INSTALLED_CYT_DAEMON_RESTART_COMMAND = "cyt hook daemon restart"
INSTALLED_CYT_MCP_COMMAND = "cyt-mcp"


@dataclass(frozen=True, slots=True)
class HookCliInvocation:
    mode: Literal["installed", "dev"]
    repo_root: Path | None = None

    @property
    def is_dev(self) -> bool:
        return self.mode == "dev"


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


def build_uv_run_dev_command(repo_root: Path, script_rel: str, *args: str) -> str:
    return shlex.join(["uv", "run", "--directory", str(repo_root), script_rel, *args])


def cyt_client_command(*, invocation: HookCliInvocation | None = None) -> str:
    invocation = invocation or detect_hook_cli_invocation()
    if invocation.is_dev and invocation.repo_root is not None:
        return build_uv_run_dev_command(
            invocation.repo_root,
            cyt_client_cli_script_relpath(),
        )
    return INSTALLED_CYT_CLIENT_COMMAND


def cyt_daemon_start_command(*, invocation: HookCliInvocation | None = None) -> str:
    invocation = invocation or detect_hook_cli_invocation()
    if invocation.is_dev and invocation.repo_root is not None:
        return build_uv_run_dev_command(
            invocation.repo_root,
            proxy_cli_script_relpath(),
            *CYT_DAEMON_START_ARGS,
        )
    return INSTALLED_CYT_DAEMON_START_COMMAND


def cyt_daemon_restart_command(*, invocation: HookCliInvocation | None = None) -> str:
    invocation = invocation or detect_hook_cli_invocation()
    if invocation.is_dev and invocation.repo_root is not None:
        return build_uv_run_dev_command(
            invocation.repo_root,
            proxy_cli_script_relpath(),
            *CYT_DAEMON_RESTART_ARGS,
        )
    return INSTALLED_CYT_DAEMON_RESTART_COMMAND


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
    normalized = command.strip()
    if not normalized.startswith("uv run "):
        return False
    client_rel = cyt_client_cli_script_relpath()
    proxy_rel = proxy_cli_script_relpath()
    if client_rel in normalized:
        return True
    return proxy_rel in normalized and (
        " hook daemon start" in normalized or " hook daemon restart" in normalized
    )
