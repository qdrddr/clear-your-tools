"""Hook daemon lifecycle: start, stop, status."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from cyt.config import launch_needs_proxy, load_config, resolve_reverse_port
from cyt.hook.port import (
    HOOK_DAEMON_PIDFILE,
    fetch_cyt_health,
    hook_url_for_port,
    is_hook_server,
    read_hook_daemon_pidfile,
)
from cyt.launch.proxy_guard import (
    LOCAL_HOST,
    STARTUP_POLL_SECONDS,
    STARTUP_TIMEOUT_SECONDS,
    find_available_port,
    is_port_in_use,
)
from cyt.launch.secrets import CYT_SKIP_KEYRING_ENV, ensure_proxy_pipeline_credentials

HookDaemonOutcome = Literal["reused", "spawned", "already_running"]


@dataclass
class HookDaemonStartResult:
    outcome: HookDaemonOutcome
    port: int
    hook_url: str
    pid: int | None
    reused: bool


def _log(verbose: bool, message: str) -> None:
    if verbose:
        print(message, file=sys.stderr)


def _write_pidfile(
    *,
    port: int,
    hook_url: str,
    pid: int | None,
    reused: bool,
    mode: str,
) -> None:
    payload = {
        "pid": pid,
        "port": port,
        "hook_url": hook_url,
        "mode": mode,
        "owner": "cyt-hook-daemon",
        "started_at": datetime.now(tz=UTC).isoformat(),
        "reused": reused,
    }
    path = HOOK_DAEMON_PIDFILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _remove_pidfile() -> None:
    path = HOOK_DAEMON_PIDFILE
    if path.is_file():
        path.unlink(missing_ok=True)


def _resolve_daemon_mode(config: dict[str, Any]) -> str:
    return "full_proxy" if launch_needs_proxy(config) else "hooks_only"


def _spawn_extra_env(credential_sources: dict[str, str]) -> dict[str, str] | None:
    extra = {
        name: value
        for name in credential_sources
        if (value := os.environ.get(name))
    }
    return extra or None


def _resolve_spawn_credentials(config: dict[str, Any]) -> dict[str, str] | None:
    """Resolve pruning pipeline keys (shell → .env → keyring) for a spawned proxy child."""
    credential_sources: dict[str, str] = {}
    ensure_proxy_pipeline_credentials(config, credential_sources=credential_sources)
    return _spawn_extra_env(credential_sources)


def _spawn_hook_server(
    *,
    port: int,
    config_path: Path | None,
    verbose: bool,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    cmd = [
        sys.executable,
        "-m",
        "cyt.proxy.cli",
        "proxy",
        "--port",
        str(port),
        "--quiet",
        "--no-resolve-credentials",
    ]
    if config_path is not None:
        cmd.extend(["--config", str(config_path)])
    child_env = os.environ.copy()
    child_env[CYT_SKIP_KEYRING_ENV] = "1"
    if extra_env:
        child_env.update(extra_env)
    _log(verbose, f"hook daemon: spawning {' '.join(cmd)}")
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL if not verbose else None,
        env=child_env,
    )


def _wait_for_hook_server(port: int, *, process: subprocess.Popen[bytes] | None) -> bool:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return False
        health = fetch_cyt_health(port)
        if is_hook_server(health):
            return process is None or process.poll() is None
        time.sleep(STARTUP_POLL_SECONDS)
    return False


def _find_reusable_hook_port(
    base_port: int,
    *,
    max_attempts: int = 100,
) -> int | None:
    for attempt in range(max_attempts):
        port = base_port + attempt
        health = fetch_cyt_health(port)
        if is_hook_server(health):
            return port
    return None


def _find_spawn_port(base_port: int, *, max_attempts: int = 100) -> int:
    for attempt in range(max_attempts):
        port = base_port + attempt
        if not is_port_in_use(port):
            return port
        health = fetch_cyt_health(port)
        if is_hook_server(health):
            continue
    return find_available_port(base_port, max_attempts=max_attempts)


def daemon_start(
    *,
    config_path: Path | None = None,
    verbose: bool = False,
    foreground: bool = False,
) -> HookDaemonStartResult:
    """Ensure a hook-capable CYT server is available (reuse-first)."""
    config = load_config(config_path)
    from cyt.cache import warm_caches

    warm_caches(config)
    base_port = resolve_reverse_port(config, None)
    mode = _resolve_daemon_mode(config)

    reused_port = _find_reusable_hook_port(base_port)
    if reused_port is not None:
        hook_url = hook_url_for_port(reused_port)
        _write_pidfile(
            port=reused_port,
            hook_url=hook_url,
            pid=None,
            reused=True,
            mode=mode,
        )
        _log(verbose, f"hook daemon: reusing {hook_url}")
        return HookDaemonStartResult(
            outcome="reused",
            port=reused_port,
            hook_url=hook_url,
            pid=None,
            reused=True,
        )

    spawn_port = _find_spawn_port(base_port)
    extra_env = _resolve_spawn_credentials(config)
    if foreground:
        from cyt.config import proxy_http2_settings
        from cyt.proxy.bootstrap import prepare_runtime
        from cyt.proxy.cli_impl import run_async_cli, run_reverse_server

        runtime = prepare_runtime(
            agent=None,
            config_path=config_path,
            port=spawn_port,
            upstream_url=None,
            upstream_kind=None,
            upstream_name=None,
            resolve_credentials=False,
        )
        http2_settings = proxy_http2_settings(runtime.config)
        _write_pidfile(
            port=spawn_port,
            hook_url=hook_url_for_port(spawn_port),
            pid=os.getpid(),
            reused=False,
            mode=mode,
        )
        _log(verbose, f"hook daemon: serving foreground on port {spawn_port}")
        run_async_cli(
            run_reverse_server(
                config=runtime.config,
                reverse_port=spawn_port,
                debug=False,
                debug_dry_run=False,
                debug_strict=False,
                http2_upstream=http2_settings["http2_upstream"],
                http2_serve=http2_settings["http2_serve"],
                ssl_keyfile=http2_settings["ssl_keyfile"],
                ssl_certfile=http2_settings["ssl_certfile"],
                pruner_settings=runtime.pruner_settings,
                launch_agent=None,
            ),
        )
        raise SystemExit(0)

    process = _spawn_hook_server(
        port=spawn_port,
        config_path=config_path,
        verbose=verbose,
        extra_env=extra_env,
    )
    if not _wait_for_hook_server(spawn_port, process=process):
        process.terminate()
        raise SystemExit(
            f"Timed out waiting for hook server on http://{LOCAL_HOST}:{spawn_port}/health",
        )

    hook_url = hook_url_for_port(spawn_port)
    _write_pidfile(
        port=spawn_port,
        hook_url=hook_url,
        pid=process.pid,
        reused=False,
        mode=mode,
    )
    _log(verbose, f"hook daemon: started pid={process.pid} port={spawn_port}")
    return HookDaemonStartResult(
        outcome="spawned",
        port=spawn_port,
        hook_url=hook_url,
        pid=process.pid,
        reused=False,
    )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _process_matches_cyt_proxy(pid: int) -> bool:
    if sys.platform == "darwin":
        cmd = ["ps", "-p", str(pid), "-o", "command="]
    else:
        cmd = ["ps", "-p", str(pid), "-o", "args="]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError:
        return False
    text = result.stdout.strip()
    return "cyt.proxy.cli" in text and " proxy " in text


def daemon_stop(*, verbose: bool = False) -> None:
    """Stop a hook daemon process we spawned, or clear a reuse pidfile."""
    pidfile = read_hook_daemon_pidfile()
    if pidfile is None:
        _log(verbose, "hook daemon: no daemon recorded")
        return

    reused = bool(pidfile.get("reused"))
    pid_value = pidfile.get("pid")
    if reused or pid_value is None:
        _log(verbose, "hook daemon: reused external server, nothing to stop")
        _remove_pidfile()
        return

    pid = int(pid_value)
    if not _pid_alive(pid):
        _log(verbose, f"hook daemon: pid {pid} not running")
        _remove_pidfile()
        return

    if not _process_matches_cyt_proxy(pid):
        _log(verbose, f"hook daemon: pid {pid} is not a cyt proxy; leaving process alone")
        _remove_pidfile()
        return

    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            break
        time.sleep(0.1)
    else:
        os.kill(pid, signal.SIGKILL)

    _remove_pidfile()
    _log(verbose, f"hook daemon: stopped pid={pid}")


def daemon_status(*, config_path: Path | None = None) -> None:
    """Print hook daemon status to stderr."""
    pidfile = read_hook_daemon_pidfile()
    port = _find_reusable_hook_port(resolve_reverse_port(load_config(config_path), None))
    if port is not None:
        hook_url = hook_url_for_port(port)
        pid_text = "pid=null"
        if pidfile is not None:
            pid_value = pidfile.get("pid")
            if pid_value is not None:
                pid_text = f"pid={pid_value}"
            if pidfile.get("reused"):
                pid_text = "pid=null (reused)"
        print(f"hook daemon: running {pid_text} port={port} url={hook_url}", file=sys.stderr)
        return
    print("hook daemon: not running", file=sys.stderr)
