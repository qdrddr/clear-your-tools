"""Subprocess helpers for the cyt-mcp CLI."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


def cyt_mcp_available(executable: str) -> bool:
    return bool(str(executable or "").strip()) and shutil.which(str(executable).strip()) is not None


def run_cyt_mcp_catalog_json(
    executable: str,
    *,
    agent: str,
    timeout: float = 120.0,
) -> dict[str, Any] | None:
    cmd = [
        str(executable).strip() or "cyt-mcp",
        "catalog",
        "--agent",
        str(agent).strip() or "cursor",
        "--json",
    ]
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("cyt-mcp catalog subprocess failed: %s", exc)
        return None
    if proc.returncode != 0:
        logger.warning(
            "cyt-mcp catalog exit %s stderr=%s",
            proc.returncode,
            (proc.stderr or "")[:300],
        )
        return None
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        logger.warning("cyt-mcp catalog returned invalid JSON")
        return None
    return payload if isinstance(payload, dict) else None
