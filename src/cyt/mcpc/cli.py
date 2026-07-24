"""Subprocess wrapper for the ``mcpc`` CLI."""

from __future__ import annotations

import copy
import json
import logging
import shutil
import subprocess
import threading
from typing import Any, cast

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 60.0


def mcpc_available(executable: str) -> bool:
    """Return True when the configured executable resolves on PATH."""
    text = str(executable or "").strip() or "mcpc"
    return shutil.which(text) is not None


def _normalize_session_name(session_name: str) -> str:
    name = str(session_name or "").strip()
    if not name:
        return ""
    return name if name.startswith("@") else f"@{name.lstrip('@')}"


_LIVE_STATUS = "live"


def _detail_suggests_disconnect(detail: str) -> bool:
    return "not connected" in detail.lower()


def _detail_is_method_not_found(detail: str) -> bool:
    lowered = detail.lower()
    return "method not found" in lowered or "-32601" in detail


_capabilities_cache: dict[tuple[str, str], dict[str, Any] | None] = {}
_capabilities_cache_lock = threading.Lock()


def clear_session_capabilities_cache() -> None:
    with _capabilities_cache_lock:
        _capabilities_cache.clear()


def session_capabilities(
    executable: str,
    session_name: str,
    *,
    quiet: bool = True,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    """Return ``capabilities`` from ``mcpc --json @session`` (cached per executable/session)."""
    name = _normalize_session_name(session_name)
    if not name:
        return None
    exe = str(executable or "mcpc").strip() or "mcpc"
    cache_key = (exe, name)
    with _capabilities_cache_lock:
        if cache_key in _capabilities_cache:
            cached = _capabilities_cache[cache_key]
            return copy.deepcopy(cached) if cached is not None else None
    payload = run_mcpc_json(
        exe,
        [name],
        quiet=quiet,
        timeout=timeout,
    )
    caps: dict[str, Any] | None = None
    if isinstance(payload, dict):
        payload_dict = cast(dict[str, Any], payload)
        raw = payload_dict.get("capabilities")
        if isinstance(raw, dict):
            caps = cast(dict[str, Any], raw)
    with _capabilities_cache_lock:
        _capabilities_cache[cache_key] = caps
    return copy.deepcopy(caps) if caps is not None else None


def session_supports_capability(
    executable: str,
    session_name: str,
    capability: str,
    *,
    quiet: bool = True,
) -> bool:
    caps = session_capabilities(executable, session_name, quiet=quiet)
    if caps is None:
        return False
    return capability in caps


def _session_status_from_payload(payload: object, session_name: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    payload_dict = cast(dict[str, object], payload)
    name = _normalize_session_name(session_name)
    if str(payload_dict.get("name") or "").strip() == name:
        status = payload_dict.get("status")
        if status is not None:
            return str(status).strip().lower() or None
    sessions = payload_dict.get("sessions")
    if not isinstance(sessions, list):
        return None
    for raw_item in sessions:
        if not isinstance(raw_item, dict):
            continue
        item = cast(dict[str, object], raw_item)
        if str(item.get("name") or "").strip() == name:
            status = item.get("status")
            if status is not None:
                return str(status).strip().lower() or None
    return None


def _session_reported_status(
    executable: str,
    session_name: str,
    *,
    quiet: bool = False,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> str | None:
    """Return the session ``status`` field from ``mcpc --json @session`` or the session list."""
    name = _normalize_session_name(session_name)
    if not name:
        return None
    exit_code, stdout, _stderr = run_mcpc(
        executable,
        ["--json", name],
        timeout=timeout,
        quiet=quiet,
    )
    if exit_code == 0 and stdout.strip():
        try:
            status = _session_status_from_payload(json.loads(stdout), name)
        except json.JSONDecodeError:
            status = None
        if status is not None:
            return status
    exit_code, stdout, _stderr = run_mcpc(
        executable,
        ["--json"],
        timeout=timeout,
        quiet=quiet,
    )
    if exit_code != 0 or not stdout.strip():
        return None
    try:
        return _session_status_from_payload(json.loads(stdout), name)
    except json.JSONDecodeError:
        return None


def _should_retry_session_command(
    executable: str,
    session: str,
    detail: str,
    *,
    quiet: bool,
    timeout: float,
) -> bool:
    if not session.startswith("@"):
        return False
    if _detail_suggests_disconnect(detail):
        return True
    status = _session_reported_status(executable, session, quiet=quiet, timeout=timeout)
    return status is not None and status != _LIVE_STATUS


def restart_mcpc_session(
    executable: str,
    session_name: str,
    *,
    quiet: bool = False,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> bool:
    """Run ``mcpc --json @session restart``; return True on success."""
    name = _normalize_session_name(session_name)
    if not name:
        return False
    exit_code, _stdout, _stderr = run_mcpc(
        executable,
        ["--json", name, "restart"],
        timeout=timeout,
        quiet=quiet,
    )
    if exit_code != 0:
        if not quiet:
            logger.debug("mcpc session restart failed session=%s exit=%d", name, exit_code)
        return False
    return True


def run_mcpc(
    executable: str,
    args: list[str],
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    cwd: str | None = None,
    quiet: bool = False,
) -> tuple[int, str, str]:
    """Run ``mcpc`` and return ``(exit_code, stdout, stderr)``."""
    cmd = [str(executable or "mcpc").strip() or "mcpc", *args]
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            check=False,
        )
    except FileNotFoundError:
        if not quiet:
            logger.warning("mcpc executable not found: %s", cmd[0])
        return 127, "", f"executable not found: {cmd[0]}"
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode()
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode()
        if not quiet:
            logger.warning("mcpc command timed out after %.0fs: %s", timeout, " ".join(cmd))
        return 124, stdout, stderr or "timeout"
    return completed.returncode, completed.stdout, completed.stderr


def _log_mcpc_json_failure(
    *,
    exit_code: int,
    json_args: list[str],
    detail: str,
    quiet: bool,
) -> None:
    if quiet:
        return
    logger.warning(
        "mcpc json command failed exit=%d cmd=%s detail=%s",
        exit_code,
        " ".join(json_args[:6]),
        detail[:240],
    )


def run_mcpc_json(
    executable: str,
    args: list[str],
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    cwd: str | None = None,
    quiet: bool = False,
    retry_on_disconnect: bool = True,
    optional_method: bool = False,
) -> object | None:
    """Run ``mcpc --json …`` and parse stdout JSON; return None on failure."""
    json_args = ["--json", *args]
    exit_code, stdout, stderr = run_mcpc(
        executable,
        json_args,
        timeout=timeout,
        cwd=cwd,
        quiet=quiet,
    )
    if exit_code != 0:
        detail = (stderr or stdout or "").strip()
        if optional_method and _detail_is_method_not_found(detail):
            return None
        session = str(args[0]) if args else ""
        if (
            retry_on_disconnect
            and session.startswith("@")
            and (len(args) < 2 or str(args[-1]) != "restart")
            and _should_retry_session_command(
                executable,
                session,
                detail,
                quiet=quiet,
                timeout=timeout,
            )
        ):
            restart_mcpc_session(executable, session, quiet=True, timeout=timeout)
            exit_code, stdout, stderr = run_mcpc(
                executable,
                json_args,
                timeout=timeout,
                cwd=cwd,
                quiet=quiet,
            )
        if exit_code != 0:
            detail = (stderr or stdout or "").strip()
            _log_mcpc_json_failure(
                exit_code=exit_code,
                json_args=json_args,
                detail=detail,
                quiet=quiet,
            )
            return None
    text = stdout.strip()
    if not text:
        return None
    try:
        return cast(object, json.loads(text))
    except json.JSONDecodeError as exc:
        if not quiet:
            logger.warning("mcpc json parse failed: %s", exc)
        return None
