"""Hook daemon lifecycle: start, stop, restart, status."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from cyt.cloudflare.readiness import report_cloudflare_hook_readiness
from cyt.config import (
    load_config,
    required_proxy_env_var_names,
    required_tools_hook_env_var_names,
    resolve_reverse_port,
)
from cyt.cyt_mcp.readiness import report_cyt_mcp_hook_readiness
from cyt.hook.port import (
    fetch_cyt_health,
    hook_url_for_port,
    is_hook_server,
)
from cyt.launch.proxy_guard import (
    LOCAL_HOST,
    STARTUP_POLL_SECONDS,
    STARTUP_TIMEOUT_SECONDS,
    find_available_port,
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


def _format_started_at_for_status(value: datetime | str) -> str:
    if isinstance(value, str):
        started = datetime.fromisoformat(value)
    else:
        started = value
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return started.astimezone().replace(microsecond=0).isoformat(sep=" ")


def _resolve_started_at(port: int, source: dict[str, Any] | None) -> str | None:
    if source is not None:
        value = source.get("started_at")
        if isinstance(value, str) and value:
            try:
                return _format_started_at_for_status(value)
            except ValueError:
                return value

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
            started_at=_resolve_started_at(result.port, entry),
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
) -> subprocess.Popen[bytes]:
    from cyt.hook.cli_invocation import build_hook_spawn_command, detect_hook_cli_invocation

    cmd = build_hook_spawn_command(
        port=port,
        config_path=config_path,
        invocation=detect_hook_cli_invocation(),
    )
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
    reused_port = _find_reusable_hook_port(base_port)
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


def daemon_start(
    *,
    config_path: Path | None = None,
    verbose: bool = False,
    foreground: bool = False,
    unattended: bool = False,
    force_spawn: bool = False,
) -> HookDaemonStartResult:
    """Ensure a hook-capable CYT server is available (reuse-first)."""
    if unattended:
        verbose = False
        _configure_unattended_quiet()
    config = load_config(config_path)
    report_cyt_mcp_hook_readiness(config, unattended=unattended)
    report_mcpc_hook_readiness(config, unattended=unattended)
    report_cloudflare_hook_readiness(config, unattended=unattended)
    base_port = resolve_reverse_port(config, None)
    mode = _resolve_daemon_mode(config)
    needs_creds = _needs_credential_injection(config)
    allow_prompt = not unattended and sys.stdin.isatty()
    require_all = not unattended
    extra_env: dict[str, str] | None = None
    if needs_creds:
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

    reused_port = _find_reusable_hook_port(base_port)
    if force_spawn and reused_port is not None:
        _log(
            verbose,
            f"hook daemon: force spawn requested; stopping existing server on port {reused_port}",
        )
        _stop_hook_server_on_port(reused_port, verbose=verbose)
        reused_port = None

    if reused_port is not None and needs_creds and not _hook_daemon_has_credentials(reused_port):
        _log(
            verbose,
            "hook daemon: restarting existing server to inject pruning credentials",
        )
        _stop_hook_server_on_port(reused_port, verbose=verbose)
        reused_port = None

    if reused_port is not None:
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

    process = _spawn_hook_server(
        port=spawn_port,
        config_path=config_path,
        verbose=verbose,
        extra_env=extra_env,
    )
    if not _wait_for_hook_server(spawn_port, process=process):
        process.terminate()
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
    from cyt.platform.process import find_listen_pid

    return find_listen_pid(port, host=LOCAL_HOST)


def _terminate_pid(pid: int) -> None:
    from cyt.platform.process import terminate_process

    terminate_process(pid)


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
        if _pid_alive(target_pid) and _process_matches_cyt_proxy(target_pid):
            _terminate_pid(target_pid)
            stopped = True
            _log(verbose, f"{label}: stopped pid={target_pid}")
        elif not _pid_alive(target_pid):
            _log(verbose, f"{label}: pid {target_pid} not running")
        else:
            _log(
                verbose,
                f"{label}: pid {target_pid} is not a cyt proxy; leaving process alone",
            )

    port_value = entry.get("port")
    if not stopped and isinstance(port_value, int):
        stopped = _stop_hook_server_on_port(port_value, verbose=verbose, label=label)
    return stopped


def _stop_hook_server_on_port(port: int, *, verbose: bool, label: str = "hook daemon") -> bool:
    """Stop a CYT hook server listening on *port* when one is present."""
    health = fetch_cyt_health(port)
    if not is_hook_server(health):
        return False

    pid = _find_listen_pid(port)
    if pid is None:
        _log(verbose, f"{label}: hook server on port {port} but no listener pid found")
        return False
    if not _process_matches_cyt_proxy(pid):
        _log(
            verbose,
            f"{label}: port {port} listener pid {pid} is not a cyt proxy; leaving process alone",
        )
        return False

    _terminate_pid(pid)
    _log(verbose, f"{label}: stopped pid={pid} port={port}")
    return True


def _resolve_stop_ports(
    entries: list[dict[str, Any]],
    *,
    config_path: Path | None,
) -> list[int]:
    ports: list[int] = []
    seen: set[int] = set()
    for entry in entries:
        port_value = entry.get("port")
        if isinstance(port_value, int) and port_value not in seen:
            seen.add(port_value)
            ports.append(port_value)

    if ports:
        return ports

    config = load_config(config_path)
    base_port = resolve_reverse_port(config, None)
    reusable_port = _find_reusable_hook_port(base_port)
    return [reusable_port] if reusable_port is not None else []


def daemon_restart(
    *,
    config_path: Path | None = None,
    verbose: bool = False,
    unattended: bool = False,
) -> HookDaemonStartResult:
    """Stop any hook daemon and start a fresh one."""
    daemon_stop(verbose=verbose, config_path=config_path)
    return daemon_start(
        config_path=config_path,
        verbose=verbose,
        foreground=False,
        unattended=unattended,
        force_spawn=True,
    )


def daemon_stop(*, verbose: bool = False, config_path: Path | None = None) -> None:
    """Stop hook daemon processes and clear registry state."""
    entries = read_hook_daemon_entries()
    stop_ports = _resolve_stop_ports(entries, config_path=config_path)
    stopped = False

    for entry in entries:
        if _stop_registry_entry(entry, verbose=verbose, label="hook daemon"):
            stopped = True

    for port in stop_ports:
        if any(entry.get("port") == port for entry in entries):
            continue
        if _stop_hook_server_on_port(port, verbose=verbose, label="hook daemon"):
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
    report_cyt_mcp_hook_readiness(config)
    report_mcpc_hook_readiness(config)
    report_cloudflare_hook_readiness(config)
    if _needs_credential_injection(config):
        from cyt.hook.credentials import report_and_ensure_hook_credentials

        report_and_ensure_hook_credentials(config, exit_on_missing_non_tty=False)

    pidfile = read_hook_daemon_pidfile()
    entries = read_hook_daemon_entries()
    port = _find_reusable_hook_port(resolve_reverse_port(config, None))
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
                started_at=_resolve_started_at(port, source),
            ),
            file=sys.stderr,
        )
        for entry in entries:
            entry_port = entry.get("port")
            if entry_port == port:
                continue
            if not isinstance(entry_port, int):
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
                    started_at=_resolve_started_at(entry_port, entry),
                ),
                file=sys.stderr,
            )
        return
    print("hook daemon: not running", file=sys.stderr)
