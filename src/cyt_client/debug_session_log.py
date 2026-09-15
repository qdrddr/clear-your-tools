"""NDJSON debug logging for agent debug sessions (stdlib only)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

_DEFAULT_LOG = Path(__file__).resolve().parents[2] / ".cursor" / "debug-5e6ec8.log"
_DEBUG_LOG = Path(os.environ.get("CYT_AGENT_DEBUG_LOG", str(_DEFAULT_LOG)))
_SESSION = os.environ.get("CYT_AGENT_DEBUG_SESSION", "5e6ec8")


def agent_debug_log(
    location: str,
    message: str,
    data: dict[str, Any] | None = None,
    *,
    hypothesis_id: str = "",
    run_id: str = "pre-fix",
) -> None:
    # #region agent log
    try:
        entry: dict[str, Any] = {
            "sessionId": _SESSION,
            "timestamp": int(time.time() * 1000),
            "location": location,
            "message": message,
            "data": data or {},
            "hypothesisId": hypothesis_id,
            "runId": run_id,
        }
        _DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _DEBUG_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # #endregion
