"""Stop running CYT reverse proxies and hook daemons."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _find_cyt_proxy_pids() -> list[int]:
    """Return PIDs for running ``cyt.proxy.cli proxy`` processes."""
    if sys.platform == "darwin":
        cmd = ["ps", "-ax", "-o", "pid=,command="]
    else:
        cmd = ["ps", "-ax", "-o", "pid=,args="]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError:
        return []
    current_pid = os.getpid()
    pids: list[int] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid_str, command = parts
        if not pid_str.isdigit():
            continue
        pid = int(pid_str)
        if pid == current_pid:
            continue
        if "cyt.proxy.cli" in command and " proxy " in command:
            pids.append(pid)
    return pids


def stop_running_proxies(*, verbose: bool = False) -> bool:
    """Stop any running ``cyt proxy`` processes not already terminated."""
    from cyt.hook.daemon import _log, _pid_alive, _process_matches_cyt_proxy, _terminate_pid

    stopped = False
    seen: set[int] = set()
    for pid in _find_cyt_proxy_pids():
        if pid in seen:
            continue
        seen.add(pid)
        if not _pid_alive(pid) or not _process_matches_cyt_proxy(pid):
            continue
        _terminate_pid(pid)
        _log(verbose, f"cyt stop: stopped proxy pid={pid}")
        stopped = True
    return stopped


def stop_proxies_only(*, verbose: bool = False) -> None:
    """Stop tracked and untracked reverse proxies without touching hook daemons."""
    from cyt.hook.daemon import stop_tracked_proxies

    stop_tracked_proxies(verbose=verbose)
    stop_running_proxies(verbose=verbose)


def stop_all(*, verbose: bool = False, config_path: Path | None = None) -> None:
    """Stop hook daemons and any remaining CYT reverse proxies."""
    from cyt.hook.daemon import daemon_stop

    daemon_stop(verbose=verbose, config_path=config_path)
    stop_proxies_only(verbose=verbose)
