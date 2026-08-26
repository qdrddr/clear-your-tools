"""Cross-platform process and port discovery (stdlib only)."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime

_LOCAL_HOST = "127.0.0.1"


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def find_listen_pid(port: int, *, host: str = _LOCAL_HOST) -> int | None:
    """Return PID listening on *host*:*port*, or ``None``."""
    if sys.platform == "win32":
        return _find_listen_pid_windows(port, host=host)
    return _find_listen_pid_unix(port)


def process_start_time(pid: int) -> datetime | None:
    """Return the process start time for *pid*, or ``None`` when unavailable."""
    if sys.platform == "win32":
        return _process_start_time_windows(pid)
    return _process_start_time_unix(pid)


def process_command_line(pid: int) -> str | None:
    """Return the command line for *pid*, or ``None`` when unavailable."""
    if sys.platform == "win32":
        return _process_command_line_windows(pid)
    return _process_command_line_unix(pid)


def list_process_command_lines() -> list[tuple[int, str]]:
    """Return ``(pid, command_line)`` pairs for running processes."""
    if sys.platform == "win32":
        return _list_process_command_lines_windows()
    return _list_process_command_lines_unix()


def terminate_process(pid: int, *, grace_seconds: float = 5.0) -> None:
    """Terminate *pid*, escalating when it does not exit gracefully."""
    if sys.platform == "win32":
        _terminate_process_windows(pid, grace_seconds=grace_seconds)
        return
    _terminate_process_unix(pid, grace_seconds=grace_seconds)


def _find_listen_pid_unix(port: int) -> int | None:
    commands = (
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        ["lsof", "-t", "-i", f":{port}"],
    )
    for cmd in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except OSError:
            continue
        if result.returncode != 0:
            continue
        for line in result.stdout.strip().splitlines():
            candidate = line.strip()
            if candidate.isdigit():
                return int(candidate)
    return None


def _find_listen_pid_windows(port: int, *, host: str = _LOCAL_HOST) -> int | None:
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None

    host_lower = host.casefold()
    port_token = f":{port}"
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if "LISTENING" not in stripped.upper():
            continue
        parts = stripped.split()
        if len(parts) < 5:
            continue
        local_addr = parts[1]
        pid_str = parts[-1]
        if not pid_str.isdigit():
            continue
        addr_lower = local_addr.casefold()
        if not addr_lower.endswith(port_token):
            continue
        if host_lower not in ("0.0.0.0", "*") and not addr_lower.startswith(host_lower):
            if not addr_lower.startswith("[::]") and host_lower != "127.0.0.1":
                continue
        return int(pid_str)
    return None


def _process_start_time_unix(pid: int) -> datetime | None:
    cmd = ["ps", "-p", str(pid), "-o", "lstart="]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    if not text:
        return None
    try:
        started = datetime.strptime(text, "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None
    return started.replace(tzinfo=datetime.now().astimezone().tzinfo)


def _process_start_time_windows(pid: int) -> datetime | None:
    ps_cmd = (
        f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CreationDate'
        ".ToUniversalTime().ToString('o')"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _process_command_line_unix(pid: int) -> str | None:
    if sys.platform == "darwin":
        cmd = ["ps", "-p", str(pid), "-o", "command="]
    else:
        cmd = ["ps", "-p", str(pid), "-o", "args="]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    return text or None


def _process_command_line_windows(pid: int) -> str | None:
    ps_cmd = f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine'
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        result = None
    if result is not None and result.returncode == 0:
        text = result.stdout.strip()
        if text:
            return text

    try:
        result = subprocess.run(
            [
                "wmic",
                "process",
                "where",
                f"ProcessId={pid}",
                "get",
                "CommandLine",
                "/format:list",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if line.startswith("CommandLine="):
                return line.removeprefix("CommandLine=").strip() or None
    return None


def _list_process_command_lines_unix() -> list[tuple[int, str]]:
    if sys.platform == "darwin":
        cmd = ["ps", "-ax", "-o", "pid=,command="]
    else:
        cmd = ["ps", "-ax", "-o", "pid=,args="]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError:
        return []
    if result.returncode != 0:
        return []
    pairs: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid_str, command = parts
        if pid_str.isdigit():
            pairs.append((int(pid_str), command))
    return pairs


def _parse_powershell_process_csv(stdout: str) -> list[tuple[int, str]]:
    pairs: list[tuple[int, str]] = []
    for line in stdout.splitlines()[1:]:
        parts = line.split(",", 2)
        if len(parts) < 3:
            continue
        pid_str = parts[1].strip('"')
        command = parts[2].strip('"')
        if pid_str.isdigit():
            pairs.append((int(pid_str), command))
    return pairs


def _parse_wmic_process_csv(stdout: str) -> list[tuple[int, str]]:
    pairs: list[tuple[int, str]] = []
    for line in stdout.splitlines():
        if not line.strip() or line.startswith("Node,"):
            continue
        match = re.search(r',"?(.*?)"?,(\d+)\s*$', line)
        if match is None:
            continue
        command, pid_str = match.group(1), match.group(2)
        if pid_str.isdigit():
            pairs.append((int(pid_str), command))
    return pairs


def _list_process_command_lines_windows() -> list[tuple[int, str]]:
    ps_cmd = "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Csv -NoTypeInformation"
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        result = None
    if result is not None and result.returncode == 0:
        pairs = _parse_powershell_process_csv(result.stdout)
        if pairs:
            return pairs

    try:
        result = subprocess.run(
            ["wmic", "process", "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    return _parse_wmic_process_csv(result.stdout)


def _terminate_process_unix(pid: int, *, grace_seconds: float) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return
        time.sleep(0.1)
    if hasattr(signal, "SIGKILL"):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return


def _terminate_process_windows(pid: int, *, grace_seconds: float) -> None:
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"],
            capture_output=True,
            check=False,
        )
    except OSError:
        return
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return
        time.sleep(0.1)
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    except OSError:
        return
