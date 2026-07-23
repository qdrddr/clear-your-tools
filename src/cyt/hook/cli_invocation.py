"""Detect how ``cyt hook`` was invoked and build matching agent hook commands."""

from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

CYT_PROXY_CLI_REL = "src/cyt/proxy/cli.py"
CYT_CLIENT_CLI_REL = "src/cyt_client/cli.py"
CYT_DAEMON_START_ARGS = ("hook", "daemon", "start", "--unattended")
INSTALLED_CYT_CLIENT_COMMAND = "cyt-client"
INSTALLED_CYT_DAEMON_START_COMMAND = "cyt hook daemon start --unattended"
INSTALLED_CYT_DAEMON_START_COMMAND_BASE = "cyt hook daemon start"


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


def repo_root_from_proxy_cli_script() -> Path | None:
    script = proxy_cli_script_path()
    candidate = script.parents[3]
    if (candidate / "pyproject.toml").is_file() and script.is_file():
        return candidate
    return None


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
        return build_uv_run_dev_command(invocation.repo_root, CYT_CLIENT_CLI_REL)
    return INSTALLED_CYT_CLIENT_COMMAND


def cyt_daemon_start_command(*, invocation: HookCliInvocation | None = None) -> str:
    invocation = invocation or detect_hook_cli_invocation()
    if invocation.is_dev and invocation.repo_root is not None:
        return build_uv_run_dev_command(
            invocation.repo_root,
            CYT_PROXY_CLI_REL,
            *CYT_DAEMON_START_ARGS,
        )
    return INSTALLED_CYT_DAEMON_START_COMMAND


def is_dev_cyt_hook_command(command: str) -> bool:
    normalized = command.strip()
    if not normalized.startswith("uv run "):
        return False
    if CYT_CLIENT_CLI_REL in normalized:
        return True
    return CYT_PROXY_CLI_REL in normalized and " hook daemon start" in normalized
