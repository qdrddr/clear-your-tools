"""Subprocess wrapper for the ``mcpc`` CLI."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import cast

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 60.0


def mcpc_available(executable: str) -> bool:
    """Return True when the configured executable resolves on PATH."""
    text = str(executable or "").strip() or "mcpc"
    return shutil.which(text) is not None


def run_mcpc(
    executable: str,
    args: list[str],
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    cwd: str | None = None,
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
        logger.warning("mcpc executable not found: %s", cmd[0])
        return 127, "", f"executable not found: {cmd[0]}"
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode()
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode()
        logger.warning("mcpc command timed out after %.0fs: %s", timeout, " ".join(cmd))
        return 124, stdout, stderr or "timeout"
    return completed.returncode, completed.stdout, completed.stderr


def run_mcpc_json(
    executable: str,
    args: list[str],
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    cwd: str | None = None,
) -> object | None:
    """Run ``mcpc --json …`` and parse stdout JSON; return None on failure."""
    json_args = ["--json", *args]
    exit_code, stdout, stderr = run_mcpc(executable, json_args, timeout=timeout, cwd=cwd)
    if exit_code != 0:
        detail = (stderr or stdout or "").strip()
        logger.warning(
            "mcpc json command failed exit=%d cmd=%s detail=%s",
            exit_code,
            " ".join(json_args[:6]),
            detail[:240],
        )
        return None
    text = stdout.strip()
    if not text:
        return None
    try:
        return cast(object, json.loads(text))
    except json.JSONDecodeError as exc:
        logger.warning("mcpc json parse failed: %s", exc)
        return None
