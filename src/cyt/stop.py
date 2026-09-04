"""Stop running CYT reverse proxies and hook daemons."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

_CYT_PROXY_CLI_MARKERS = (
    "cyt.proxy.cli",
    "cyt/proxy/cli.py",
    "cyt/proxy/cli_impl",
)


def is_cyt_proxy_command(command: str) -> bool:
    """Return True when *command* looks like a CYT ``proxy`` subcommand."""
    normalized = command.replace("\\", "/")
    tokens = normalized.split()
    try:
        proxy_index = tokens.index("proxy")
    except ValueError:
        return False
    prefix = " ".join(tokens[:proxy_index])
    return any(marker in prefix for marker in _CYT_PROXY_CLI_MARKERS)


def _find_cyt_proxy_pids() -> list[int]:
    """Return PIDs for running ``cyt proxy`` processes."""
    from cyt.platform.process import list_process_command_lines

    current_pid = os.getpid()
    pids: list[int] = []
    for pid, command in list_process_command_lines():
        if pid == current_pid:
            continue
        if is_cyt_proxy_command(command):
            pids.append(pid)
    return pids


def _proxy_registry_ports() -> set[int]:
    from cyt.runtime_registry import read_proxy_entries

    ports: set[int] = set()
    for entry in read_proxy_entries():
        port = entry.get("port")
        if isinstance(port, int):
            ports.add(port)
    return ports


def _hook_daemon_exclude_sets(*, except_proxy_registry: bool = False) -> tuple[set[int], set[int]]:
    """Return hook-daemon ports and PIDs that must not be stopped as proxies."""
    from cyt.hook.daemon import _find_listen_pid
    from cyt.runtime_registry import read_hook_daemon_entries

    proxy_registry_ports = _proxy_registry_ports() if except_proxy_registry else set()
    ports: set[int] = set()
    pids: set[int] = set()
    for entry in read_hook_daemon_entries():
        port = entry.get("port")
        if isinstance(port, int):
            if except_proxy_registry and port in proxy_registry_ports:
                pass
            else:
                ports.add(port)
        pid = entry.get("pid")
        if isinstance(pid, int) and not entry.get("reused"):
            if except_proxy_registry and isinstance(port, int) and port in proxy_registry_ports:
                pass
            else:
                pids.add(pid)
        elif isinstance(port, int) and not (except_proxy_registry and port in proxy_registry_ports):
            listener = _find_listen_pid(port)
            if listener is not None:
                pids.add(listener)
    return ports, pids


def _proxy_listener_pid(port: int) -> int | None:
    from cyt.hook.daemon import _find_listen_pid
    from cyt.hook.port import fetch_cyt_health
    from cyt.launch.proxy_guard import _is_cyt_health, is_port_in_use

    if not is_port_in_use(port):
        return None
    health = fetch_cyt_health(port)
    if not _is_cyt_health(health):
        return None
    return _find_listen_pid(port)


def _try_add_proxy_pid(
    pid: int | None,
    *,
    seen: set[int],
    ordered: list[int],
    exclude_pids: set[int],
    require_command_match: bool,
) -> None:
    from cyt.hook.daemon import _pid_alive, _process_matches_cyt_proxy

    if pid is None or pid in seen or pid in exclude_pids or pid == os.getpid():
        return
    if not _pid_alive(pid):
        return
    if require_command_match and not _process_matches_cyt_proxy(pid):
        return
    seen.add(pid)
    ordered.append(pid)


def _collect_registry_proxy_pids(
    *,
    exclude_ports: set[int],
    exclude_pids: set[int],
    seen: set[int],
    ordered: list[int],
) -> None:
    from cyt.runtime_registry import read_proxy_entries

    for entry in read_proxy_entries():
        port = entry.get("port")
        if isinstance(port, int) and port in exclude_ports:
            continue
        pid = entry.get("pid")
        if isinstance(pid, int):
            _try_add_proxy_pid(
                pid,
                seen=seen,
                ordered=ordered,
                exclude_pids=exclude_pids,
                require_command_match=False,
            )
        if isinstance(port, int):
            _try_add_proxy_pid(
                _proxy_listener_pid(port),
                seen=seen,
                ordered=ordered,
                exclude_pids=exclude_pids,
                require_command_match=False,
            )


def _collect_default_port_proxy_pids(
    *,
    exclude_ports: set[int],
    exclude_pids: set[int],
    seen: set[int],
    ordered: list[int],
) -> None:
    from cyt.config import default_reverse_port
    from cyt.launch.proxy_guard import is_port_in_use

    base_port = default_reverse_port()
    candidates = [
        base_port + offset
        for offset in range(100)
        if base_port + offset not in exclude_ports
    ]
    if not candidates:
        return

    live_ports: list[int] = []
    workers = min(20, len(candidates))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(is_port_in_use, port): port for port in candidates}
        for future in as_completed(futures):
            port = futures[future]
            try:
                if future.result():
                    live_ports.append(port)
            except Exception:
                continue

    for port in sorted(live_ports):
        _try_add_proxy_pid(
            _proxy_listener_pid(port),
            seen=seen,
            ordered=ordered,
            exclude_pids=exclude_pids,
            require_command_match=False,
        )


def _collect_proxy_pids(
    *,
    exclude_ports: set[int],
    exclude_pids: set[int],
) -> list[int]:
    """Collect unique PIDs for CYT reverse proxies, excluding hook daemons."""
    seen: set[int] = set()
    ordered: list[int] = []

    _collect_registry_proxy_pids(
        exclude_ports=exclude_ports,
        exclude_pids=exclude_pids,
        seen=seen,
        ordered=ordered,
    )

    for pid in _find_cyt_proxy_pids():
        _try_add_proxy_pid(
            pid,
            seen=seen,
            ordered=ordered,
            exclude_pids=exclude_pids,
            require_command_match=True,
        )

    _collect_default_port_proxy_pids(
        exclude_ports=exclude_ports,
        exclude_pids=exclude_pids,
        seen=seen,
        ordered=ordered,
    )

    return ordered


def _stop_proxy_registry_entry(
    entry: dict[str, Any],
    *,
    verbose: bool,
    exclude_pids: set[int],
) -> bool:
    """Stop one reverse proxy from the runtime registry."""
    from cyt.hook.daemon import _log, _pid_alive, _terminate_pid

    port = entry.get("port")
    pid = entry.get("pid")
    targets: list[int] = []
    if isinstance(pid, int):
        targets.append(pid)
    if isinstance(port, int):
        listener = _proxy_listener_pid(port)
        if listener is not None:
            targets.append(listener)

    stopped = False
    seen: set[int] = set()
    for target_pid in targets:
        if target_pid in seen or target_pid in exclude_pids:
            continue
        seen.add(target_pid)
        if not _pid_alive(target_pid):
            continue
        _terminate_pid(target_pid)
        stopped = True
        _log(verbose, f"cyt stop: stopped proxy pid={target_pid} port={port}")
    if isinstance(port, int):
        from cyt.runtime_registry import remove_proxy_entries

        remove_proxy_entries(ports={port})
    return stopped


def proxy_registry_has_live_servers() -> bool:
    """Return True when ``pid.json`` lists a proxy with a live CYT ``/health`` listener."""
    from cyt.runtime_registry import read_proxy_entries

    for entry in read_proxy_entries():
        port = entry.get("port")
        if isinstance(port, int) and _proxy_listener_pid(port) is not None:
            return True
    return False


def stop_proxy_registry_servers(*, verbose: bool = False) -> bool:
    """Stop every reverse proxy listed in ``pid.json``."""
    from cyt.runtime_registry import read_proxy_entries

    stopped = False
    for entry in list(read_proxy_entries()):
        if _stop_proxy_registry_entry(entry, verbose=verbose, exclude_pids=set()):
            stopped = True
    return stopped


def proxies_conflicting_with_hook_setup() -> bool:
    """Return True when reverse proxies conflict with hook installation."""
    from cyt.runtime_registry import read_proxy_entries

    if not read_proxy_entries():
        return False
    if proxy_registry_has_live_servers():
        return True
    exclude_ports, exclude_pids = _hook_daemon_exclude_sets(except_proxy_registry=True)
    return bool(_collect_proxy_pids(exclude_ports=exclude_ports, exclude_pids=exclude_pids))


def stop_proxies_for_hook_setup(*, verbose: bool = False) -> bool:
    """Stop reverse proxies for hook setup and prune stale ``pid.json`` records."""
    stopped = stop_proxy_registry_servers(verbose=verbose)
    exclude_ports, exclude_pids = _hook_daemon_exclude_sets(except_proxy_registry=True)
    for pid in _collect_proxy_pids(exclude_ports=exclude_ports, exclude_pids=exclude_pids):
        from cyt.hook.daemon import _log, _terminate_pid

        _terminate_pid(pid)
        _log(verbose, f"cyt stop: stopped proxy pid={pid}")
        stopped = True
    from cyt.runtime_registry import prune_stale_runtime_entries

    prune_stale_runtime_entries()
    return stopped


def stop_tracked_proxies(*, verbose: bool = False) -> bool:
    """Stop reverse proxies recorded in ``~/.config/cyt/pid.json``."""
    from cyt.runtime_registry import read_proxy_entries

    _, exclude_pids = _hook_daemon_exclude_sets()
    stopped = False
    for entry in list(read_proxy_entries()):
        if _stop_proxy_registry_entry(entry, verbose=verbose, exclude_pids=exclude_pids):
            stopped = True
    return stopped


def stop_running_proxies(*, verbose: bool = False) -> bool:
    """Stop any running ``cyt proxy`` processes not already terminated."""
    from cyt.hook.daemon import _log, _terminate_pid
    from cyt.runtime_registry import prune_stale_runtime_entries

    exclude_ports, exclude_pids = _hook_daemon_exclude_sets()
    stopped = False
    for pid in _collect_proxy_pids(exclude_ports=exclude_ports, exclude_pids=exclude_pids):
        _terminate_pid(pid)
        _log(verbose, f"cyt stop: stopped proxy pid={pid}")
        stopped = True
    prune_stale_runtime_entries()
    return stopped


def proxies_are_running() -> bool:
    """Return True when at least one CYT reverse proxy appears to be running."""
    from cyt.hook.daemon import _pid_alive, _process_matches_cyt_proxy
    from cyt.runtime_registry import read_proxy_entries

    exclude_ports, exclude_pids = _hook_daemon_exclude_sets()

    for entry in read_proxy_entries():
        port = entry.get("port")
        if isinstance(port, int) and port in exclude_ports:
            continue
        if isinstance(port, int):
            listener = _proxy_listener_pid(port)
            if listener is not None and listener not in exclude_pids:
                return True
        pid = entry.get("pid")
        if isinstance(pid, int) and pid not in exclude_pids:
            if _pid_alive(pid) and _process_matches_cyt_proxy(pid):
                return True

    for pid in _collect_proxy_pids(exclude_ports=exclude_ports, exclude_pids=exclude_pids):
        if _pid_alive(pid):
            return True

    return False


def stop_proxies_only(*, verbose: bool = False) -> None:
    """Stop tracked and untracked reverse proxies without touching hook daemons."""
    stop_tracked_proxies(verbose=verbose)
    stop_running_proxies(verbose=verbose)


def stop_all(*, verbose: bool = False, config_path: Path | None = None) -> None:
    """Stop hook daemons and any remaining CYT reverse proxies."""
    from cyt.hook.daemon import daemon_stop

    daemon_stop(verbose=verbose, config_path=config_path)
    stop_proxies_only(verbose=verbose)
