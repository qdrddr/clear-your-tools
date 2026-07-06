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

from cyt.config import (
    launch_needs_proxy,
    load_config,
    required_proxy_env_var_names,
    resolve_reverse_port,
)
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
from cyt.launch.secrets import CYT_SKIP_KEYRING_ENV, resolve_hook_daemon_child_env

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


def _needs_credential_injection(config: dict[str, Any]) -> bool:
    """True when the configured hook pipeline requires remote pruner API keys."""
    return bool(required_proxy_env_var_names(config))


def _report_missing_daemon_credentials(names: list[str]) -> None:
    if not names:
        return
    joined = ", ".join(names)
    print(
        f"cyt hook daemon: missing {joined} (remote pruning disabled)",
        file=sys.stderr,
    )


def _resolve_spawn_credentials(
    config: dict[str, Any],
    *,
    allow_prompt: bool = False,
    require_all: bool = True,
) -> dict[str, str] | None:
    """Resolve pruning pipeline keys (shell → .env → keyring) for a spawned proxy child."""
    env = resolve_hook_daemon_child_env(
        config,
        allow_prompt=allow_prompt,
        require_all=require_all,
    )
    return env or None


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
    unattended: bool = False,
) -> HookDaemonStartResult:
    """Ensure a hook-capable CYT server is available (reuse-first)."""
    if unattended:
        verbose = False
    config = load_config(config_path)
    from cyt.cache import warm_caches

    warm_caches(config)
    base_port = resolve_reverse_port(config, None)
    mode = _resolve_daemon_mode(config)
    needs_creds = _needs_credential_injection(config)
    allow_prompt = not unattended and sys.stdin.isatty()
    require_all = not unattended
    extra_env: dict[str, str] | None = None
    if needs_creds:
        required_names = required_proxy_env_var_names(config)
        extra_env = _resolve_spawn_credentials(
            config,
            allow_prompt=allow_prompt,
            require_all=require_all,
        )
        if unattended:
            missing = [
                name
                for name in required_names
                if not extra_env or name not in extra_env
            ]
            _report_missing_daemon_credentials(missing)

    reused_port = _find_reusable_hook_port(base_port)
    if reused_port is not None and extra_env is not None:
        _log(
            verbose,
            "hook daemon: restarting existing server to inject pruning credentials",
        )
        _stop_hook_server_on_port(reused_port, verbose=verbose)
        reused_port = None

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
    if foreground:
        from cyt.config import proxy_http2_settings
        from cyt.proxy.bootstrap import prepare_runtime
        from cyt.proxy.cli_impl import run_async_cli, run_reverse_server

        if extra_env:
            os.environ.update(extra_env)
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


def _find_listen_pid(port: int) -> int | None:
    """Return PID listening on ``LOCAL_HOST:port``, or ``None``."""
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


def _terminate_pid(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.1)
    os.kill(pid, signal.SIGKILL)


def _stop_hook_server_on_port(port: int, *, verbose: bool) -> bool:
    """Stop a CYT hook server listening on *port* when one is present."""
    health = fetch_cyt_health(port)
    if not is_hook_server(health):
        return False

    pid = _find_listen_pid(port)
    if pid is None:
        _log(verbose, f"hook daemon: hook server on port {port} but no listener pid found")
        return False
    if not _process_matches_cyt_proxy(pid):
        _log(
            verbose,
            f"hook daemon: port {port} listener pid {pid} is not a cyt proxy; leaving process alone",
        )
        return False

    _terminate_pid(pid)
    _log(verbose, f"hook daemon: stopped pid={pid} port={port}")
    return True


def _resolve_stop_port(
    pidfile: dict[str, Any] | None,
    *,
    config_path: Path | None,
) -> int | None:
    if pidfile is not None:
        port_value = pidfile.get("port")
        if isinstance(port_value, int):
            return port_value

    config = load_config(config_path)
    base_port = resolve_reverse_port(config, None)
    return _find_reusable_hook_port(base_port)


def daemon_stop(*, verbose: bool = False, config_path: Path | None = None) -> None:
    """Stop a hook daemon process and clear pidfile state."""
    pidfile = read_hook_daemon_pidfile()
    target_port = _resolve_stop_port(pidfile, config_path=config_path)
    target_pid: int | None = None
    if pidfile is not None and not pidfile.get("reused"):
        pid_value = pidfile.get("pid")
        if pid_value is not None:
            target_pid = int(pid_value)

    stopped = False
    if target_pid is not None:
        if _pid_alive(target_pid) and _process_matches_cyt_proxy(target_pid):
            _terminate_pid(target_pid)
            stopped = True
            _log(verbose, f"hook daemon: stopped pid={target_pid}")
        elif not _pid_alive(target_pid):
            _log(verbose, f"hook daemon: pid {target_pid} not running")
        else:
            _log(
                verbose,
                f"hook daemon: pid {target_pid} is not a cyt proxy; leaving process alone",
            )

    if not stopped and target_port is not None:
        stopped = _stop_hook_server_on_port(target_port, verbose=verbose)

    if not stopped:
        _log(verbose, "hook daemon: no daemon recorded")

    _remove_pidfile()


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
