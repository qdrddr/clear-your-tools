"""Hook daemon lifecycle: start, stop, restart, status."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TextIO

from cyt.cloudflare.readiness import report_cloudflare_hook_readiness
from cyt.config import (
    load_config,
    required_proxy_env_var_names,
    required_tools_hook_env_var_names,
    resolve_reverse_port,
)
from cyt.cyt_mcp.readiness import report_cyt_mcp_hook_readiness
from cyt.hook.port import (
    STATUS_HEALTH_TIMEOUT_SECONDS,
    collect_known_hook_ports,
    fetch_cyt_health,
    find_hook_port_for_status,
    find_reusable_hook_port,
    find_spawn_port,
    hook_port_is_live,
    hook_url_for_port,
    is_hook_server,
)
from cyt.launch.proxy_guard import (
    LOCAL_HOST,
    STARTUP_POLL_SECONDS,
    STARTUP_TIMEOUT_SECONDS,
    is_port_in_use,
)
from cyt.launch.secrets import CYT_SKIP_KEYRING_ENV, resolve_hook_daemon_child_env
from cyt.mcpc.readiness import report_mcpc_hook_readiness
from cyt.runtime_registry import (
    find_hook_daemon_entry_for_port,
    read_hook_daemon_entries,
    read_hook_daemon_pidfile,
    remove_hook_daemon_entries,
    upsert_hook_daemon_entry,
    upsert_proxy_entry,
)

__all__ = [
    "sys",
]

HookDaemonOutcome = Literal["reused", "spawned", "already_running"]

_lifecycle_lock_state = threading.local()
_DAEMON_LOCK_POLL_SECONDS = 0.2
_DAEMON_LOCK_TIMEOUT_SECONDS = 120.0


@contextmanager
def _defer_sigint() -> Iterator[None]:
    """Ignore Ctrl+C while stopping daemons so console/taskkill noise cannot abort restart."""
    previous = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def _hook_daemon_spawn_kwargs() -> dict[str, Any]:
    """Return ``Popen`` kwargs that detach hook daemon children on Windows."""
    if sys.platform != "win32":
        return {}
    flags = subprocess.CREATE_NEW_PROCESS_GROUP
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags |= subprocess.CREATE_NO_WINDOW
    return {"creationflags": flags}


def _read_daemon_lock_holder(lock_path: Path) -> int | None:
    try:
        text = lock_path.read_text(encoding="utf-8").strip()
        if not text:
            return None
        holder = int(text.splitlines()[0].strip())
        return holder if holder > 0 else None
    except (OSError, ValueError):
        return None


def _write_daemon_lock_holder(handle: TextIO) -> None:
    handle.seek(0)
    handle.truncate(0)
    handle.write(f"{os.getpid()}\n")
    handle.flush()


@contextmanager
def _daemon_lifecycle_lock(*, command: str = "daemon") -> Iterator[None]:
    """Serialize hook daemon stop/start/restart across processes."""
    if getattr(_lifecycle_lock_state, "held", False):
        yield
        return
    from cyt.platform.filelock import try_exclusive_file_lock
    from cyt.runtime_registry import PID_REGISTRY

    lock_path = PID_REGISTRY.parent / "daemon.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    wait_started = time.monotonic()
    last_wait_log = wait_started
    acquired = False
    with lock_path.open("a+", encoding="utf-8") as handle:
        while not acquired:
            if try_exclusive_file_lock(handle.fileno()):
                acquired = True
                break
            holder = _read_daemon_lock_holder(lock_path)
            now = time.monotonic()
            elapsed = now - wait_started
            if now - last_wait_log >= 1.0:
                print(
                    f"hook daemon: waiting for {command} lock"
                    + (f" (held by pid={holder})" if holder is not None else "")
                    + "...",
                    file=sys.stderr,
                )
                last_wait_log = now
            if elapsed >= _DAEMON_LOCK_TIMEOUT_SECONDS:
                raise SystemExit(
                    "Timed out waiting for hook daemon lock"
                    + (f" (held by pid={holder})" if holder is not None else "")
                    + ". Another cyt hook daemon command is still running.",
                )
            time.sleep(_DAEMON_LOCK_POLL_SECONDS)

        _write_daemon_lock_holder(handle)
        _lifecycle_lock_state.held = True
        try:
            yield
        finally:
            _lifecycle_lock_state.held = False
            if acquired:
                try:
                    from cyt.platform.filelock import release_exclusive_file_lock

                    release_exclusive_file_lock(handle.fileno())
                except OSError:
                    pass


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


def _format_started_at_for_status(value: datetime | str) -> str:
    if isinstance(value, str):
        started = datetime.fromisoformat(value)
    else:
        started = value
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return started.astimezone().replace(microsecond=0).isoformat(sep=" ")


def _resolve_started_at(
    port: int,
    source: dict[str, Any] | None,
    *,
    skip_process_lookup: bool = False,
) -> str | None:
    if source is not None:
        value = source.get("started_at")
        if isinstance(value, str) and value:
            try:
                return _format_started_at_for_status(value)
            except ValueError:
                return value

    if skip_process_lookup:
        return None

    from cyt.platform.process import process_start_time

    listener_pid = _find_listen_pid(port)
    if listener_pid is not None:
        started = process_start_time(listener_pid)
        if started is not None:
            return _format_started_at_for_status(started)
    return None


def _running_status_line(
    *,
    port: int,
    hook_url: str,
    pid: int | None = None,
    reused: bool = False,
    started_at: str | None = None,
) -> str:
    if reused:
        pid_text = "pid=null (reused)"
    elif pid is not None:
        pid_text = f"pid={pid}"
    else:
        pid_text = "pid=null"
    line = f"hook daemon: running {pid_text} port={port} url={hook_url}"
    if started_at:
        line = f"{line} started={started_at}"
    return line


def _emit_start_status(result: HookDaemonStartResult, *, unattended: bool) -> None:
    if unattended:
        return
    entry = find_hook_daemon_entry_for_port(result.port)
    print(
        _running_status_line(
            port=result.port,
            hook_url=result.hook_url,
            pid=result.pid,
            reused=result.reused,
            started_at=_resolve_started_at(result.port, entry, skip_process_lookup=True),
        ),
        file=sys.stderr,
    )


def record_spawned_proxy_pidfile(
    *,
    port: int,
    pid: int,
    config_path: Path | None,
    credentials_injected: bool,
) -> None:
    """Record a launch-spawned proxy so hook ``sessionStart`` reuse won't restart it."""
    upsert_proxy_entry(
        port=port,
        pid=pid,
        owner="cyt-launch",
        config_path=config_path,
        credentials_injected=credentials_injected,
    )


def _write_pidfile(
    *,
    port: int,
    hook_url: str,
    pid: int | None,
    reused: bool,
    mode: str,
    credentials_injected: bool = False,
) -> None:
    upsert_hook_daemon_entry(
        port=port,
        hook_url=hook_url,
        pid=pid,
        reused=reused,
        mode=mode,
        credentials_injected=credentials_injected,
    )


def _remove_pidfile() -> None:
    remove_hook_daemon_entries()


def _resolve_daemon_mode(config: dict[str, Any]) -> str:
    from cyt.config import any_agent_needs_proxy

    return "full_proxy" if any_agent_needs_proxy(config) else "hooks_only"


def _needs_credential_injection(config: dict[str, Any]) -> bool:
    """True when the configured hook pipeline requires remote credentials."""
    return bool(
        required_proxy_env_var_names(config) or required_tools_hook_env_var_names(config),
    )


def _hook_daemon_has_credentials(reused_port: int) -> bool:
    """True when the running cyt-managed daemon on ``reused_port`` already has creds."""
    pidfile = find_hook_daemon_entry_for_port(reused_port)
    if pidfile is None:
        return False
    if pidfile.get("owner") != "cyt-hook-daemon":
        return False
    return bool(pidfile.get("credentials_injected"))


def _report_missing_daemon_credentials(names: list[str], *, unattended: bool = False) -> None:
    if not names or unattended:
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
) -> subprocess.Popen[Any]:
    from cyt.hook.cli_invocation import build_hook_spawn_command, detect_hook_cli_invocation

    cmd = build_hook_spawn_command(
        port=port,
        config_path=config_path,
        invocation=detect_hook_cli_invocation(),
    )
    child_env = os.environ.copy()
    child_env[CYT_SKIP_KEYRING_ENV] = "1"
    from cyt.cache.warm import CYT_HOOK_DAEMON_CHILD_ENV

    child_env[CYT_HOOK_DAEMON_CHILD_ENV] = "1"
    if extra_env:
        child_env.update(extra_env)
    _log(verbose, f"hook daemon: spawning {' '.join(cmd)}")
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=None if verbose else subprocess.DEVNULL,
        env=child_env,
        **_hook_daemon_spawn_kwargs(),
    )


def _wait_for_hook_server(port: int, *, process: subprocess.Popen[Any] | None) -> bool:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return False
        health = fetch_cyt_health(port, timeout=STATUS_HEALTH_TIMEOUT_SECONDS)
        if is_hook_server(health):
            return process is None or process.poll() is None
        time.sleep(STARTUP_POLL_SECONDS)
    return False


def _wait_for_port_free(port: int, *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_port_in_use(port):
            return True
        time.sleep(STARTUP_POLL_SECONDS)
    return not is_port_in_use(port)


def _fast_terminate_pid(pid: int) -> bool:
    """Stop *pid* immediately (no graceful wait)."""
    if not _pid_alive(pid):
        return True
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            check=False,
        )
        if _pid_alive(pid):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
    else:
        from cyt.platform.process import terminate_process

        terminate_process(pid, grace_seconds=0.2)
        if _pid_alive(pid) and hasattr(os, "kill"):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
    return not _pid_alive(pid)


def _fast_kill_port_listeners(port: int, *, verbose: bool, timeout: float = 2.0) -> bool:
    """Kill listeners on *port* without HTTP health probes."""
    deadline = time.monotonic() + timeout
    killed = False
    while time.monotonic() < deadline:
        listeners = _find_listen_pids(port)
        if not listeners and not is_port_in_use(port):
            return killed or True
        for listener in listeners:
            if _fast_terminate_pid(listener):
                killed = True
                _log(verbose, f"hook daemon: killed listener pid={listener} port={port}")
        if not listeners and is_port_in_use(port):
            time.sleep(STARTUP_POLL_SECONDS)
            if not _find_listen_pids(port):
                return True
        time.sleep(STARTUP_POLL_SECONDS)
    return killed and not is_port_in_use(port)


def _force_kill_port_listeners(port: int, *, verbose: bool, timeout: float = 5.0) -> bool:
    """Kill every listener on *port* until it is free or *timeout* expires."""
    return _fast_kill_port_listeners(port, verbose=verbose, timeout=min(timeout, 3.0))


def _stop_hook_port(port: int, *, verbose: bool, timeout: float = 2.0) -> bool:
    """Stop a hook server or other listener on *port*."""
    if hook_port_is_live(port):
        return _stop_hook_server_on_port(
            port,
            verbose=verbose,
            label="hook daemon",
            force=True,
        )
    if is_port_in_use(port):
        return _fast_kill_port_listeners(port, verbose=verbose, timeout=timeout)
    return False


def _ensure_spawn_port_available(port: int, *, verbose: bool) -> None:
    """Stop any listener on *port* and wait until it is free to bind."""
    _fast_kill_port_listeners(port, verbose=verbose, timeout=2.0)
    _wait_for_port_free(port, timeout=2.0)


def _find_reusable_hook_port(
    base_port: int,
    entries: list[dict[str, Any]],
    *,
    full_scan: bool = False,
    max_attempts: int = 100,
) -> int | None:
    return find_reusable_hook_port(
        base_port,
        entries,
        full_scan=full_scan,
        max_attempts=max_attempts,
    )


def _find_spawn_port(
    base_port: int,
    *,
    max_attempts: int = 100,
    prefer_port: int | None = None,
) -> int:
    return find_spawn_port(base_port, max_attempts=max_attempts, prefer_port=prefer_port)


def _configure_unattended_quiet() -> None:
    """Silence logging during unattended hook daemon start (Cursor sessionStart)."""
    from cyt.launch.quiet import configure_launch_quiet

    configure_launch_quiet()


def _schedule_warm_caches(config: dict[str, Any]) -> None:
    """Warm caches after the hook server is reachable (never block startup)."""
    from cyt.cache import schedule_warm_caches

    schedule_warm_caches(config)


def _unattended_fallback_result(
    base_port: int,
    *,
    mode: str,
    config: dict[str, Any],
) -> HookDaemonStartResult:
    """Best-effort success for hook sessionStart when spawn/wait fails."""
    reused_port = _find_reusable_hook_port(base_port, read_hook_daemon_entries())
    port = reused_port if reused_port is not None else base_port
    hook_url = hook_url_for_port(port)
    reused = reused_port is not None
    if reused:
        _write_pidfile(
            port=port,
            hook_url=hook_url,
            pid=None,
            reused=True,
            mode=mode,
        )
        _schedule_warm_caches(config)
    return HookDaemonStartResult(
        outcome="reused" if reused else "already_running",
        port=port,
        hook_url=hook_url,
        pid=None,
        reused=reused,
    )


def _report_hook_credentials_status(config: dict[str, Any]) -> None:
    """Fast credential summary for ``daemon status`` (env files only, no keyring)."""
    from cyt.config import load_proxy_env
    from cyt.hook.credentials import required_hook_daemon_env_var_names

    names = required_hook_daemon_env_var_names(config)
    if not names:
        return
    load_proxy_env()
    missing = [name for name in names if not os.environ.get(name)]
    if missing and not sys.stdin.isatty():
        joined = ", ".join(missing)
        print(f"Hook credentials missing: {joined}", file=sys.stderr)


def daemon_start(
    *,
    config_path: Path | None = None,
    verbose: bool = False,
    foreground: bool = False,
    unattended: bool = False,
    force_spawn: bool = False,
    ports_already_stopped: bool = False,
) -> HookDaemonStartResult:
    """Ensure a hook-capable CYT server is available (reuse-first)."""
    with _daemon_lifecycle_lock(command="daemon_start"):
        return _daemon_start_locked(
            config_path=config_path,
            verbose=verbose,
            foreground=foreground,
            unattended=unattended,
            force_spawn=force_spawn,
            ports_already_stopped=ports_already_stopped,
        )


def _return_reused_daemon(
    *,
    reused_port: int,
    config: dict[str, Any],
    mode: str,
    verbose: bool,
    unattended: bool,
    schedule_warm: bool = True,
) -> HookDaemonStartResult:
    hook_url = hook_url_for_port(reused_port)
    existing = find_hook_daemon_entry_for_port(reused_port)
    creds_injected = bool(existing and existing.get("credentials_injected"))
    _write_pidfile(
        port=reused_port,
        hook_url=hook_url,
        pid=None,
        reused=True,
        mode=mode,
        credentials_injected=creds_injected,
    )
    _log(verbose, f"hook daemon: reusing {hook_url}")
    if schedule_warm:
        _schedule_warm_caches(config)
    result = HookDaemonStartResult(
        outcome="reused",
        port=reused_port,
        hook_url=hook_url,
        pid=None,
        reused=True,
    )
    _emit_start_status(result, unattended=unattended)
    return result


def _resolve_start_reused_port(
    *,
    force_spawn: bool,
    ports_already_stopped: bool,
    base_port: int,
    entries: list[dict[str, Any]],
    known_ports: list[int],
    verbose: bool,
    unattended: bool,
    config: dict[str, Any],
    mode: str,
) -> tuple[int | None, HookDaemonStartResult | None]:
    if force_spawn:
        if not ports_already_stopped:
            _ensure_hook_ports_stopped(known_ports, verbose=verbose)
        elif is_port_in_use(base_port):
            _fast_kill_port_listeners(base_port, verbose=verbose, timeout=1.0)
        return None, None

    reused_port = _find_reusable_hook_port(base_port, entries)
    if reused_port is not None and unattended:
        return None, _return_reused_daemon(
            reused_port=reused_port,
            config=config,
            mode=mode,
            verbose=verbose,
            unattended=unattended,
            schedule_warm=False,
        )
    return reused_port, None


def _resolve_start_extra_env(
    config: dict[str, Any],
    *,
    needs_creds: bool,
    reused_port: int | None,
    allow_prompt: bool,
    require_all: bool,
    unattended: bool,
) -> dict[str, str] | None:
    if not needs_creds or reused_port is not None:
        return None
    required_names = list(
        dict.fromkeys(
            [
                *required_proxy_env_var_names(config),
                *required_tools_hook_env_var_names(config),
            ],
        ),
    )
    extra_env = _resolve_spawn_credentials(
        config,
        allow_prompt=allow_prompt,
        require_all=require_all,
    )
    missing = [name for name in required_names if not extra_env or name not in extra_env]
    _report_missing_daemon_credentials(missing, unattended=unattended)
    return extra_env


def _run_foreground_daemon(
    *,
    spawn_port: int,
    config_path: Path | None,
    mode: str,
    extra_env: dict[str, str] | None,
    verbose: bool,
    unattended: bool,
) -> None:
    from cyt.config import proxy_http2_settings
    from cyt.proxy.bootstrap import prepare_runtime
    from cyt.proxy.cli_impl import run_reverse_server
    from cyt.proxy.transport import run_async_cli

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
        credentials_injected=bool(extra_env),
    )
    _schedule_warm_caches(runtime.config)
    _log(verbose, f"hook daemon: serving foreground on port {spawn_port}")
    _emit_start_status(
        HookDaemonStartResult(
            outcome="spawned",
            port=spawn_port,
            hook_url=hook_url_for_port(spawn_port),
            pid=os.getpid(),
            reused=False,
        ),
        unattended=unattended,
    )
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


def _spawn_background_daemon(
    *,
    spawn_port: int,
    base_port: int,
    config_path: Path | None,
    config: dict[str, Any],
    mode: str,
    extra_env: dict[str, str] | None,
    verbose: bool,
    unattended: bool,
    force_spawn: bool,
) -> HookDaemonStartResult:
    spawn_attempts = 2 if force_spawn else 1
    process: subprocess.Popen[Any] | None = None
    for attempt in range(spawn_attempts):
        if attempt:
            _ensure_spawn_port_available(spawn_port, verbose=verbose)
        process = _spawn_hook_server(
            port=spawn_port,
            config_path=config_path,
            verbose=verbose,
            extra_env=extra_env,
        )
        if _wait_for_hook_server(spawn_port, process=process):
            break
        process.terminate()
    else:
        if unattended:
            return _unattended_fallback_result(base_port, mode=mode, config=config)
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
        credentials_injected=bool(extra_env),
    )
    _log(verbose, f"hook daemon: started pid={process.pid} port={spawn_port}")
    _schedule_warm_caches(config)
    result = HookDaemonStartResult(
        outcome="spawned",
        port=spawn_port,
        hook_url=hook_url,
        pid=process.pid,
        reused=False,
    )
    _emit_start_status(result, unattended=unattended)
    return result


def _daemon_start_locked(
    *,
    config_path: Path | None = None,
    verbose: bool = False,
    foreground: bool = False,
    unattended: bool = False,
    force_spawn: bool = False,
    ports_already_stopped: bool = False,
) -> HookDaemonStartResult:
    if unattended:
        verbose = False
        _configure_unattended_quiet()
    config = load_config(config_path)
    base_port = resolve_reverse_port(config, None)
    mode = _resolve_daemon_mode(config)
    entries = read_hook_daemon_entries()
    known_ports = collect_known_hook_ports(base_port, entries)
    needs_creds = _needs_credential_injection(config)

    reused_port, early_result = _resolve_start_reused_port(
        force_spawn=force_spawn,
        ports_already_stopped=ports_already_stopped,
        base_port=base_port,
        entries=entries,
        known_ports=known_ports,
        verbose=verbose,
        unattended=unattended,
        config=config,
        mode=mode,
    )
    if early_result is not None:
        return early_result

    report_cyt_mcp_hook_readiness(config, unattended=True)
    report_mcpc_hook_readiness(config, unattended=True)
    report_cloudflare_hook_readiness(config, unattended=True)

    if reused_port is not None and needs_creds and not _hook_daemon_has_credentials(reused_port):
        if not unattended:
            _log(
                verbose,
                "hook daemon: restarting existing server to inject pruning credentials",
            )
            _stop_hook_server_on_port(reused_port, verbose=verbose, force=True)
            reused_port = None

    allow_prompt = not unattended and not force_spawn and sys.stdin.isatty()
    require_all = not unattended and not force_spawn
    extra_env = _resolve_start_extra_env(
        config,
        needs_creds=needs_creds,
        reused_port=reused_port,
        allow_prompt=allow_prompt,
        require_all=require_all,
        unattended=unattended,
    )

    if reused_port is not None:
        return _return_reused_daemon(
            reused_port=reused_port,
            config=config,
            mode=mode,
            verbose=verbose,
            unattended=unattended,
        )

    spawn_port = _find_spawn_port(
        base_port,
        prefer_port=base_port if force_spawn else None,
    )
    if not foreground:
        if force_spawn:
            _ensure_spawn_port_available(spawn_port, verbose=verbose)
        else:
            _wait_for_port_free(spawn_port, timeout=3.0)
    if foreground:
        _run_foreground_daemon(
            spawn_port=spawn_port,
            config_path=config_path,
            mode=mode,
            extra_env=extra_env,
            verbose=verbose,
            unattended=unattended,
        )

    return _spawn_background_daemon(
        spawn_port=spawn_port,
        base_port=base_port,
        config_path=config_path,
        config=config,
        mode=mode,
        extra_env=extra_env,
        verbose=verbose,
        unattended=unattended,
        force_spawn=force_spawn,
    )


def _pid_alive(pid: int) -> bool:
    from cyt.platform.process import pid_alive

    return pid_alive(pid)


def _process_matches_cyt_proxy(pid: int) -> bool:
    from cyt.platform.process import process_command_line
    from cyt.stop import is_cyt_proxy_command

    command = process_command_line(pid)
    if not command:
        return False
    return is_cyt_proxy_command(command)


def _find_listen_pid(port: int) -> int | None:
    """Return PID listening on ``LOCAL_HOST:port``, or ``None``."""
    from cyt.platform.process import find_listen_pids

    pids = find_listen_pids(port, host=LOCAL_HOST)
    return pids[0] if pids else None


def _find_listen_pids(port: int) -> list[int]:
    from cyt.platform.process import find_listen_pids

    return find_listen_pids(port, host=LOCAL_HOST)


def _terminate_pid(pid: int) -> bool:
    from cyt.platform.process import pid_alive, terminate_process

    terminate_process(pid, grace_seconds=1.0)
    if pid_alive(pid):
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        elif hasattr(os, "kill"):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
    return not pid_alive(pid)


def _stop_registry_entry(
    entry: dict[str, Any],
    *,
    verbose: bool,
    label: str,
) -> bool:
    target_pid: int | None = None
    if not entry.get("reused"):
        pid_value = entry.get("pid")
        if pid_value is not None:
            target_pid = int(pid_value)

    stopped = False
    if target_pid is not None:
        if _pid_alive(target_pid):
            if _fast_terminate_pid(target_pid):
                stopped = True
                _log(verbose, f"{label}: stopped pid={target_pid}")
            else:
                _log(verbose, f"{label}: failed to stop pid={target_pid}")
        else:
            _log(verbose, f"{label}: pid {target_pid} not running")

    port_value = entry.get("port")
    if isinstance(port_value, int):
        if _fast_kill_port_listeners(port_value, verbose=verbose, timeout=2.0):
            stopped = True
        elif not stopped and not is_port_in_use(port_value):
            stopped = True
    return stopped


def _ensure_hook_ports_stopped(ports: list[int], *, verbose: bool, timeout: float = 1.0) -> None:
    if not ports:
        return
    if not any(is_port_in_use(port) for port in ports):
        return
    for port in ports:
        _fast_kill_port_listeners(port, verbose=verbose, timeout=timeout)


def _stop_hook_server_on_port(
    port: int,
    *,
    verbose: bool,
    label: str = "hook daemon",
    force: bool = False,
) -> bool:
    """Stop a CYT hook server listening on *port* when one is present."""
    port_in_use = is_port_in_use(port)
    live = hook_port_is_live(port) if port_in_use else False
    if not live and not (force and port_in_use):
        return False

    pid = _find_listen_pid(port)
    if pid is None:
        if force and port_in_use:
            _log(verbose, f"{label}: port {port} in use but no listener pid found; force killing")
            return _force_kill_port_listeners(port, verbose=verbose)
        return False
    if not force and not _process_matches_cyt_proxy(pid):
        _log(
            verbose,
            f"{label}: port {port} listener pid {pid} is not a cyt proxy; leaving process alone",
        )
        return False

    if not _terminate_pid(pid):
        _log(verbose, f"{label}: failed to stop pid={pid} port={port}")
        return False

    _wait_for_port_free(port, timeout=2.0)
    if hook_port_is_live(port):
        _log(verbose, f"{label}: hook server still live on port {port}")
        return _force_kill_port_listeners(port, verbose=verbose)

    listener = _find_listen_pid(port)
    if listener is not None:
        _log(verbose, f"{label}: port {port} still has listener pid={listener}")
        return _force_kill_port_listeners(port, verbose=verbose)

    _log(verbose, f"{label}: stopped pid={pid} port={port}")
    return True


def _resolve_stop_ports(
    entries: list[dict[str, Any]],
    *,
    config_path: Path | None,
) -> list[int]:
    config = load_config(config_path)
    base_port = resolve_reverse_port(config, None)
    return collect_known_hook_ports(base_port, entries)


def daemon_restart(
    *,
    config_path: Path | None = None,
    verbose: bool = False,
    unattended: bool = False,
) -> HookDaemonStartResult:
    """Stop any hook daemon and start a fresh one."""
    if not unattended:
        print("hook daemon: restarting...", file=sys.stderr)
    with _daemon_lifecycle_lock(command="daemon_restart"):
        with _defer_sigint():
            _daemon_stop_locked(verbose=verbose, config_path=config_path)
        return _daemon_start_locked(
            config_path=config_path,
            verbose=verbose,
            foreground=False,
            unattended=unattended,
            force_spawn=True,
            ports_already_stopped=True,
        )


def daemon_stop(*, verbose: bool = False, config_path: Path | None = None) -> None:
    """Stop hook daemon processes and clear registry state."""
    with _daemon_lifecycle_lock(command="daemon_stop"):
        with _defer_sigint():
            _daemon_stop_locked(verbose=verbose, config_path=config_path)


def _daemon_stop_locked(*, verbose: bool = False, config_path: Path | None = None) -> None:
    entries = read_hook_daemon_entries()
    stop_ports = _resolve_stop_ports(entries, config_path=config_path)
    stopped = False

    for entry in entries:
        if _stop_registry_entry(entry, verbose=verbose, label="hook daemon"):
            stopped = True

    for port in stop_ports:
        if _stop_hook_port(port, verbose=verbose):
            stopped = True

    if not stopped:
        _log(verbose, "hook daemon: no daemon recorded")

    _remove_pidfile()


def stop_tracked_proxies(*, verbose: bool = False) -> bool:
    """Stop reverse proxies recorded in ``~/.config/cyt/pid.json``."""
    from cyt.stop import stop_tracked_proxies as _stop_tracked_proxies

    return _stop_tracked_proxies(verbose=verbose)


def daemon_status(*, config_path: Path | None = None) -> None:
    """Print hook daemon status to stderr."""
    config = load_config(config_path)
    report_cyt_mcp_hook_readiness(config, unattended=True)
    report_mcpc_hook_readiness(config, unattended=True)
    report_cloudflare_hook_readiness(config, unattended=True)
    if _needs_credential_injection(config):
        _report_hook_credentials_status(config)

    pidfile = read_hook_daemon_pidfile()
    entries = read_hook_daemon_entries()
    base_port = resolve_reverse_port(config, None)
    port = find_hook_port_for_status(base_port, entries, pidfile)
    if port is not None:
        hook_url = hook_url_for_port(port)
        matching = find_hook_daemon_entry_for_port(port)
        pid: int | None = None
        reused = False
        source = matching if matching is not None else pidfile
        if source is not None:
            pid_value = source.get("pid")
            if pid_value is not None:
                pid = int(pid_value)
            reused = bool(source.get("reused"))
        print(
            _running_status_line(
                port=port,
                hook_url=hook_url,
                pid=pid,
                reused=reused,
                started_at=_resolve_started_at(port, source, skip_process_lookup=True),
            ),
            file=sys.stderr,
        )
        for entry in entries:
            entry_port = entry.get("port")
            if entry_port == port:
                continue
            if not isinstance(entry_port, int):
                continue
            if not hook_port_is_live(entry_port):
                continue
            entry_hook_url = entry.get("hook_url")
            if not isinstance(entry_hook_url, str):
                entry_hook_url = hook_url_for_port(entry_port)
            entry_pid = entry.get("pid")
            print(
                _running_status_line(
                    port=entry_port,
                    hook_url=entry_hook_url,
                    pid=int(entry_pid) if entry_pid is not None else None,
                    reused=bool(entry.get("reused")),
                    started_at=_resolve_started_at(entry_port, entry, skip_process_lookup=True),
                ),
                file=sys.stderr,
            )
        return
    print("hook daemon: not running", file=sys.stderr)
