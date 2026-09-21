"""NDJSON debug logging for active debug sessions (remove after verification)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

_SESSION_ID = "3a7f2e"
_LOG_PATH = Path(__file__).resolve().parents[2] / "debug-3a7f2e.log"


def debug_session_log(
    *,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any] | None = None,
    run_id: str = "pre-fix",
) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": _SESSION_ID,
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        with _LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # #endregion
