"""Ensure a local CYT reverse proxy is running before agent launch."""

from __future__ import annotations

import atexit
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from cyt.launch.secrets import CYT_SKIP_KEYRING_ENV

LOCAL_HOST = "127.0.0.1"
HEALTH_TIMEOUT_SECONDS = 1.5
STARTUP_POLL_SECONDS = 0.2
STARTUP_TIMEOUT_SECONDS = 35.0
PROXY_SHUTDOWN_SECONDS = 2.0


@dataclass
class ProxyGuard:
    """Tracks a background proxy process started by launch."""

    process: subprocess.Popen[bytes] | None
    started_by_launch: bool

    def terminate_if_started(self) -> None:
        if not self.started_by_launch or self.process is None:
            return
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()


def _proxy_health(port: int) -> dict[str, Any] | None:
    url = f"http://{LOCAL_HOST}:{port}/health"
    try:
        with urlopen(url, timeout=HEALTH_TIMEOUT_SECONDS) as response:
            code = response.getcode()
            if not isinstance(code, int) or code != 200:
                return None
            payload = json.loads(response.read())
            return payload if isinstance(payload, dict) else None
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None


def _health_ok(port: int) -> bool:
    payload = _proxy_health(port)
    return isinstance(payload, dict) and payload.get("status") == "ok"


def _proxy_debug_matches(
    health: dict[str, Any],
    *,
    debug: bool,
    debug_dry_run: bool,
) -> bool:
    if "debug" not in health or "debug_dry_run" not in health:
        return False
    return bool(health.get("debug")) == debug and bool(health.get("debug_dry_run")) == debug_dry_run


def _listener_pids_on_port(port: int) -> list[int]:
    """Return PIDs listening on *port* (not clients connected to it)."""
    result = subprocess.run(
        ["lsof", "-nP", "-iTCP", f":{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        check=False,
    )
    return [int(pid) for pid in result.stdout.split() if pid.strip().isdigit()]


def is_port_in_use(port: int) -> bool:
    """Return whether a process is already listening on *port*."""
    return bool(_listener_pids_on_port(port))


def find_available_port(start: int, *, max_attempts: int = 100) -> int:
    """Return the first free TCP listen port at or above *start*."""
    port = start
    for _ in range(max_attempts):
        if not is_port_in_use(port):
            return port
        port += 1
    raise SystemExit(
        f"No free port found starting from {start} (tried {max_attempts} ports).",
    )


def resolve_launch_port(start: int) -> int:
    """Pick a listen port for ``cyt launch``, bumping when *start* is taken."""
    port = find_available_port(start)
    if port != start:
        print(
            f"Port {start} is in use; launching on {port}.",
            file=sys.stderr,
        )
    return port


def _terminate_listeners_on_port(port: int) -> None:
    pids = _listener_pids_on_port(port)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    if not pids:
        return
    deadline = time.monotonic() + PROXY_SHUTDOWN_SECONDS
    while time.monotonic() < deadline:
        if not _health_ok(port):
            return
        time.sleep(STARTUP_POLL_SECONDS)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue


def _spawn_proxy(
    *,
    port: int,
    config_path: Path | None,
    quiet: bool,
    debug: bool = False,
    debug_dry_run: bool = False,
    debug_strict: bool = False,
) -> subprocess.Popen[bytes]:
    cmd = [
        sys.executable,
        "-m",
        "cyt.proxy.cli",
        "proxy",
        "--port",
        str(port),
    ]
    # Launch prints the env report; keep the background child quiet on stderr.
    cmd.append("--quiet")
    cmd.append("--no-resolve-credentials")
    if config_path is not None:
        cmd.extend(["--config", str(config_path)])
    if debug:
        cmd.append("--debug")
    if debug_dry_run:
        cmd.append("--debug-dry-run")
        if debug_strict:
            cmd.append("--debug-strict")
    stderr = None if debug or debug_dry_run else subprocess.DEVNULL
    child_env = os.environ.copy()
    child_env[CYT_SKIP_KEYRING_ENV] = "1"
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=stderr,
        env=child_env,
    )


def ensure_proxy(
    *,
    port: int,
    config_path: Path | None = None,
    quiet: bool = False,
    debug: bool = False,
    debug_dry_run: bool = False,
    debug_strict: bool = False,
) -> ProxyGuard:
    """Return immediately when proxy is healthy; otherwise spawn one in the background."""
    health = _proxy_health(port)
    if health is not None and health.get("status") == "ok":
        needs_debug = debug or debug_dry_run
        if not needs_debug or _proxy_debug_matches(
            health,
            debug=debug,
            debug_dry_run=debug_dry_run,
        ):
            return ProxyGuard(process=None, started_by_launch=False)
        _terminate_listeners_on_port(port)

    process = _spawn_proxy(
        port=port,
        config_path=config_path,
        quiet=quiet,
        debug=debug,
        debug_dry_run=debug_dry_run,
        debug_strict=debug_strict,
    )
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(
                f"cyt proxy exited before becoming healthy on port {port}.",
            )
        if _health_ok(port):
            if process.poll() is not None:
                raise SystemExit(
                    f"cyt proxy exited before becoming healthy on port {port}.",
                )
            guard = ProxyGuard(process=process, started_by_launch=True)
            atexit.register(guard.terminate_if_started)
            return guard
        time.sleep(STARTUP_POLL_SECONDS)

    process.terminate()
    raise SystemExit(
        f"Timed out waiting for cyt proxy on http://{LOCAL_HOST}:{port}/health",
    )


def require_healthy_proxy(*, port: int, debug: bool = False, debug_dry_run: bool = False) -> None:
    """Fail fast when the reverse proxy is not accepting requests."""
    health = _proxy_health(port)
    if health is None or health.get("status") != "ok":
        raise SystemExit(
            f"cyt proxy is not healthy on http://{LOCAL_HOST}:{port}/health. "
            "Check for a stale process on that port or retry launch.",
        )
    if (debug or debug_dry_run) and not _proxy_debug_matches(
        health,
        debug=debug,
        debug_dry_run=debug_dry_run,
    ):
        raise SystemExit(
            f"cyt proxy on port {port} is running without --debug. "
            "Stop it and retry: pkill -f 'cyt.proxy.cli proxy'",
        )
