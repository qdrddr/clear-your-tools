"""Append-only NDJSON debug log for agent debug sessions (stdlib only)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

_SESSION_ID = os.environ.get("CYT_AGENT_DEBUG_SESSION_ID", "8f6e73")
_LOG_BASENAME = os.environ.get("CYT_AGENT_DEBUG_LOG", f"debug-{_SESSION_ID}.log")


def _log_path() -> Path:
    env_dir = os.environ.get("CYT_AGENT_DEBUG_LOG_DIR", "").strip()
    if env_dir:
        return Path(env_dir) / _LOG_BASENAME
    # Default: repo root (…/clear-your-tools/debug-*.log) so daemon + client share one file.
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / _LOG_BASENAME


def agent_debug_log(
    location: str,
    message: str,
    *,
    data: dict[str, Any] | None = None,
    hypothesis_id: str | None = None,
    run_id: str = "pre-fix",
) -> None:
    payload: dict[str, Any] = {
        "sessionId": _SESSION_ID,
        "timestamp": int(time.time() * 1000),
        "location": location,
        "message": message,
        "runId": run_id,
    }
    if hypothesis_id:
        payload["hypothesisId"] = hypothesis_id
    if data:
        payload["data"] = data
    try:
        path = _log_path()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, separators=(",", ":")) + "\n")
    except OSError:
        pass
