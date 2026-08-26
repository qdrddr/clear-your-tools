"""Resolve hook executables for Cursor's stripped PATH (stdlib only)."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

CYT_CLIENT_SCRIPT_REL = "src/cyt_client/cli.py"
CYT_PROXY_SCRIPT_REL = "src/cyt/proxy/cli.py"


def resolve_hook_executable(name: str) -> str:
    """Return an absolute path for *name* when discoverable, else the bare name."""
    stripped = str(name or "").strip()
    if not stripped:
        return name
    found = shutil.which(stripped)
    if found:
        return str(Path(found).resolve())
    if sys.platform == "win32":
        home = Path.home()
        local_app = os.environ.get("LOCALAPPDATA", "")
        candidates = (
            home / ".local" / "bin" / f"{stripped}.exe",
            home / ".local" / "bin" / stripped,
            Path(local_app) / "Programs" / "uv" / f"{stripped}.exe" if local_app else None,
        )
        for candidate in candidates:
            if candidate is not None and candidate.is_file():
                return str(candidate.resolve())
    return stripped


def quote_for_cmd_exe(token: str) -> str:
    """Quote *token* for cmd.exe when it is an absolute/relative path or contains spaces."""
    if not token:
        return token
    if " " in token or "\t" in token:
        return f'"{token.replace(chr(34), chr(34) * 2)}"'
    if sys.platform == "win32" and (len(token) > 1 and token[1] == ":" or token.startswith("\\")):
        return f'"{token.replace(chr(34), chr(34) * 2)}"'
    return token


def build_uv_run_dev_command(repo_root: Path, script_rel: str, *args: str) -> str:
    """Build a dev ``uv run --directory …`` hook command with an absolute ``uv`` path on Windows."""
    uv = quote_for_cmd_exe(resolve_hook_executable("uv"))
    if sys.platform == "win32":
        directory = str(repo_root).replace('"', '""')
        tail = subprocess.list2cmdline([script_rel, *args])
        return f"{uv} run --directory \"{directory}\" {tail}"
    parts = [uv, "run", "--directory", str(repo_root), script_rel, *args]
    return shlex.join(parts)


def build_installed_cyt_client_command() -> str:
    return quote_for_cmd_exe(resolve_hook_executable("cyt-client"))


def build_installed_cyt_daemon_start_command(*, unattended: bool = True) -> str:
    cyt = quote_for_cmd_exe(resolve_hook_executable("cyt"))
    tail = "hook daemon start --unattended" if unattended else "hook daemon start"
    return f"{cyt} {tail}"


def build_installed_cyt_daemon_restart_command() -> str:
    cyt = quote_for_cmd_exe(resolve_hook_executable("cyt"))
    return f"{cyt} hook daemon restart"


def repo_root_from_uv_run_hook_command(command: str) -> Path | None:
    """Parse ``--directory`` from a ``uv run`` hook command (bare or absolute ``uv`` path)."""
    quoted = re.search(r'run --directory "((?:[^"]|"")*)"', command)
    if quoted:
        return Path(quoted.group(1).replace('""', '"'))
    try:
        parts = shlex.split(command, posix=(sys.platform != "win32"))
    except ValueError:
        return None
    for index, part in enumerate(parts):
        if part == "run" and index + 2 < len(parts) and parts[index + 1] == "--directory":
            return Path(parts[index + 2])
    return None


def is_uv_run_dev_hook_command(command: str) -> bool:
    """Return True when *command* is a repo-local ``uv run --directory …`` CYT hook."""
    normalized = command.strip()
    if " run --directory " not in normalized:
        return False
    if CYT_CLIENT_SCRIPT_REL in normalized or "cyt_client/cli.py" in normalized:
        return True
    if (CYT_PROXY_SCRIPT_REL in normalized or "cyt/proxy/cli.py" in normalized) and (
        " hook daemon start" in normalized or " hook daemon restart" in normalized
    ):
        return True
    return False
