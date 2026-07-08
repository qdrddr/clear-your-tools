"""Temporary agent debug session logging (session 698c1a)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

_LOG = Path(__file__).resolve().parents[3] / ".cursor" / "debug-698c1a.log"
_SESSION = "698c1a"


def agent_debug_log(
    *,
    location: str,
    message: str,
    data: dict[str, Any],
    hypothesis_id: str,
    run_id: str = "pre-fix",
) -> None:
    payload = {
        "sessionId": _SESSION,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")
    except Exception:
        pass
