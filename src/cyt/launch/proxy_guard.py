"""Ensure a local CYT reverse proxy is running before agent launch."""

from __future__ import annotations

import atexit
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeGuard
from urllib.error import URLError
from urllib.request import urlopen

from cyt.launch.secrets import CYT_SKIP_KEYRING_ENV

LOCAL_HOST = "127.0.0.1"
CYT_HEALTH_NAME = "cyt"
LAUNCH_PORT_OFFSET = 0
HEALTH_TIMEOUT_SECONDS = 1.5
STARTUP_POLL_SECONDS = 0.2
STARTUP_TIMEOUT_SECONDS = 35.0

LaunchPortAction = Literal["reuse", "spawn", "skip"]


@dataclass
class ProxyGuard:
    """Tracks a background proxy process started by launch."""

    process: subprocess.Popen[bytes] | None
    started_by_launch: bool
    port: int

    def terminate_if_started(self) -> None:
        if not self.started_by_launch or self.process is None:
            return
        proc = self.process
        if proc.poll() is not None:
            self.process = None
            self.started_by_launch = False
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1)
            except KeyboardInterrupt:
                proc.kill()
                try:
                    proc.wait(timeout=1)
                except (subprocess.TimeoutExpired, KeyboardInterrupt):
                    pass
        except KeyboardInterrupt:
            try:
                proc.kill()
            except OSError:
                pass
        finally:
            from cyt.runtime_registry import remove_proxy_entries

            remove_proxy_entries(ports={self.port})
            self.process = None
            self.started_by_launch = False


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
    return _is_cyt_health(payload)


def _is_cyt_health(health: dict[str, Any] | None) -> TypeGuard[dict[str, Any]]:
    return (
        isinstance(health, dict)
        and health.get("status") == "ok"
        and health.get("name") == CYT_HEALTH_NAME
    )


def _health_has_endpoint(health: dict[str, Any], endpoint: str) -> bool:
    endpoints = health.get("endpoints")
    if not isinstance(endpoints, list):
        return False
    return endpoint in [str(item) for item in endpoints]


def _proxy_debug_matches(
    health: dict[str, Any],
    *,
    debug: bool,
    debug_dry_run: bool,
) -> bool:
    if "debug" not in health or "debug_dry_run" not in health:
        return False
    return bool(health.get("debug")) == debug and bool(health.get("debug_dry_run")) == debug_dry_run


def _is_reusable_cyt_proxy(
    health: dict[str, Any] | None,
    *,
    endpoint: str,
    debug: bool,
    debug_dry_run: bool,
) -> TypeGuard[dict[str, Any]]:
    if not _is_cyt_health(health):
        return False
    if not _health_has_endpoint(health, endpoint):
        return False
    if (debug or debug_dry_run) and not _proxy_debug_matches(
        health,
        debug=debug,
        debug_dry_run=debug_dry_run,
    ):
        return False
    return True


def is_port_in_use(port: int) -> bool:
    """Return whether *port* is already bound on ``LOCAL_HOST``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((LOCAL_HOST, port))
        except OSError:
            return True
    return False


def _evaluate_launch_port(
    port: int,
    *,
    base_port: int,
    required_endpoint: str,
    debug: bool,
    debug_dry_run: bool,
    allow_reuse: bool = True,
) -> LaunchPortAction:
    if is_port_in_use(port):
        health = _proxy_health(port)
        if allow_reuse and _is_reusable_cyt_proxy(
            health,
            endpoint=required_endpoint,
            debug=debug,
            debug_dry_run=debug_dry_run,
        ):
            return "reuse"
        return "skip"
    return "spawn"


def resolve_launch_proxy_port(
    *,
    base_port: int,
    required_endpoint: str,
    debug: bool = False,
    debug_dry_run: bool = False,
    max_attempts: int = 100,
) -> tuple[int, Literal["reuse", "spawn"]]:
    """Pick a port to reuse an existing proxy or spawn a new one."""
    for attempt in range(max_attempts):
        port = base_port + attempt
        action = _evaluate_launch_port(
            port,
            base_port=base_port,
            required_endpoint=required_endpoint,
            debug=debug,
            debug_dry_run=debug_dry_run,
        )
        if action == "reuse":
            return port, "reuse"
        if action == "spawn":
            return port, "spawn"
    raise SystemExit(
        f"No free port found starting from {base_port} (tried {max_attempts} ports).",
    )


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


def resolve_launch_port(
    base_port: int,
    *,
    required_endpoint: str,
    quiet: bool = False,
    debug: bool = False,
    debug_dry_run: bool = False,
) -> int:
    """Pick the listen port ``cyt launch`` would use for this agent endpoint."""
    port, _ = resolve_launch_proxy_port(
        base_port=base_port,
        required_endpoint=required_endpoint,
        debug=debug,
        debug_dry_run=debug_dry_run,
    )
    preferred = base_port + LAUNCH_PORT_OFFSET
    if not quiet and port != preferred:
        print(
            f"Port {preferred} is in use; launching on {port}.",
            file=sys.stderr,
        )
    return port


def _spawn_proxy(
    *,
    port: int,
    config_path: Path | None,
    quiet: bool,
    agent: str | None = None,
    debug: bool = False,
    debug_dry_run: bool = False,
    debug_strict: bool = False,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    cmd = [
        sys.executable,
        "-m",
        "cyt.proxy.cli_impl",
        "proxy",
        "--port",
        str(port),
    ]
    # Launch prints the env report; keep the background child quiet on stderr.
    cmd.append("--quiet")
    # Inherit pruning credentials via extra_env; skip resolving every upstream in config.
    cmd.append("--no-resolve-credentials")
    if config_path is not None:
        cmd.extend(["--config", str(config_path)])
    if agent is not None:
        cmd.extend(["--launch-agent", agent])
    if debug:
        cmd.append("--debug")
    if debug_dry_run:
        cmd.append("--debug-dry-run")
        if debug_strict:
            cmd.append("--debug-strict")
    child_env = os.environ.copy()
    child_env[CYT_SKIP_KEYRING_ENV] = "1"
    if extra_env:
        child_env.update(extra_env)
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=child_env,
    )


def _spawn_and_wait_for_healthy_proxy(
    *,
    port: int,
    config_path: Path | None,
    quiet: bool,
    agent: str | None,
    debug: bool,
    debug_dry_run: bool,
    debug_strict: bool,
    extra_env: dict[str, str] | None = None,
) -> ProxyGuard | None:
    """Spawn a proxy on *port*; return ``None`` when it exits before becoming healthy."""
    process = _spawn_proxy(
        port=port,
        config_path=config_path,
        quiet=quiet,
        agent=agent,
        debug=debug,
        debug_dry_run=debug_dry_run,
        debug_strict=debug_strict,
        extra_env=extra_env,
    )
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return None
        if _health_ok(port):
            if process.poll() is not None:
                return None
            time.sleep(0.3)
            if process.poll() is not None or not _health_ok(port):
                return None
            guard = ProxyGuard(process=process, started_by_launch=True, port=port)
            atexit.register(guard.terminate_if_started)
            from cyt.hook.daemon import record_spawned_proxy_pidfile

            record_spawned_proxy_pidfile(
                port=port,
                pid=process.pid,
                config_path=config_path,
                credentials_injected=bool(extra_env),
            )
            return guard
        time.sleep(STARTUP_POLL_SECONDS)

    process.terminate()
    raise SystemExit(
        f"Timed out waiting for cyt proxy on http://{LOCAL_HOST}:{port}/health",
    )


def ensure_proxy(
    *,
    base_port: int,
    required_endpoint: str,
    config_path: Path | None = None,
    quiet: bool = False,
    agent: str | None = None,
    debug: bool = False,
    debug_dry_run: bool = False,
    debug_strict: bool = False,
    max_attempts: int = 100,
    extra_env: dict[str, str] | None = None,
    allow_reuse: bool = True,
) -> ProxyGuard:
    """Reuse or spawn a CYT reverse proxy for this launch."""
    preferred_spawn = base_port + LAUNCH_PORT_OFFSET
    for attempt in range(max_attempts):
        port = base_port + attempt
        action = _evaluate_launch_port(
            port,
            base_port=base_port,
            required_endpoint=required_endpoint,
            debug=debug,
            debug_dry_run=debug_dry_run,
            allow_reuse=allow_reuse,
        )
        if action == "reuse":
            if not quiet and port != preferred_spawn:
                print(
                    f"Port {preferred_spawn} is in use; launching on {port}.",
                    file=sys.stderr,
                )
            return ProxyGuard(process=None, started_by_launch=False, port=port)
        if action != "spawn":
            continue
        guard = _spawn_and_wait_for_healthy_proxy(
            port=port,
            config_path=config_path,
            quiet=quiet,
            agent=agent,
            debug=debug,
            debug_dry_run=debug_dry_run,
            debug_strict=debug_strict,
            extra_env=extra_env,
        )
        if guard is not None:
            if not quiet and port != preferred_spawn:
                print(
                    f"Port {preferred_spawn} is in use; launching on {port}.",
                    file=sys.stderr,
                )
            return guard

    raise SystemExit(
        f"No free port found starting from {base_port} (tried {max_attempts} ports).",
    )


def require_healthy_proxy(*, port: int, debug: bool = False, debug_dry_run: bool = False) -> None:
    """Fail fast when the reverse proxy is not accepting requests."""
    health = _proxy_health(port)
    if not _is_cyt_health(health):
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
